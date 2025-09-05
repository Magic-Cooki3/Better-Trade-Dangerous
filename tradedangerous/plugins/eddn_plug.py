"""
Live market import plugin using EDDN (ED Data Network).

- Subscribes to the EDDN ZeroMQ firehose (tcp://eddn.edcd.io:9500).
- Ingests Commodity v3 snapshots and upserts StationItem in the DB.
- Optionally filters to Fleet Carriers and/or public-access carriers.
- Updates Station.carrier_docking_access when provided by EDDN.

Notes
- This plugin assumes your DB already has Systems and Stations seeded
  (e.g., via the spansh or eddblink plugins). It matches stations by
  (systemName, stationName) case-insensitive.
- If a station isn’t found, the message is skipped (no ad-hoc creation).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
import time
import typing
import zlib

try:
    import zmq  # type: ignore
except Exception as _e:  # pragma: no cover
    zmq = None  # lazy error at runtime

from .. import plugins


if typing.TYPE_CHECKING:
    from .. tradeenv import TradeEnv
    from .. tradedb import TradeDB


def _to_unix(ts: str) -> int:
    # EDDN timestamps are ISO 8601 UTC e.g. "2024-09-01T14:03:55Z" or with +00:00
    ts = ts.replace("Z", "+00:00")
    # Python 3.10: fromisoformat supports +00:00
    try:
        dt = _dt.datetime.fromisoformat(ts)
    except Exception:
        # Fallback: try stripping timezone
        dt = _dt.datetime.fromisoformat(ts.split("+")[0])
    if dt.tzinfo is None:
        # Treat as UTC if tz missing
        return int(dt.replace(tzinfo=_dt.timezone.utc).timestamp())
    return int(dt.timestamp())


class ImportPlugin(plugins.ImportPluginBase):  # pylint: disable=too-many-instance-attributes
    pluginOptions = {
        'host': 'EDDN ZMQ endpoint (default tcp://eddn.edcd.io:9500)',
        'duration': 'Seconds to run before exiting; 0 means run until interrupted.',
        'carrier_only': 'If set, only process Fleet Carrier markets.',
        'public_only': 'If set, only process carriers with carrierDockingAccess="all".',
        'optimize': 'VACUUM the DB after ingestion.',
        'debug_dump': 'Write last raw EDDN JSON to tmp/eddn_last.json for debugging.',
    }

    def __init__(self, tdb: 'TradeDB', tdenv: 'TradeEnv'):
        super().__init__(tdb, tdenv)
        self.host = self.getOption('host') or os.environ.get('TD_EDDN_HOST') or 'tcp://eddn.edcd.io:9500'
        try:
            self.duration = float(self.getOption('duration') or 0)
        except Exception:
            self.duration = 0.0
        self.carrier_only = bool(self.getOption('carrier_only'))
        self.public_only = bool(self.getOption('public_only'))
        self.optimize = bool(self.getOption('optimize'))
        self.debug_dump = bool(self.getOption('debug_dump'))

        self._ctx = None
        self._sub = None
        self._last_station_mod: dict[int, int] = {}
        self._item_symbol_map: dict[str, int] = {}
        self._stats = {
            'messages': 0,
            'commodity_messages': 0,
            'journal_messages': 0,
            'stations_matched': 0,
            'stations_skipped': 0,
            'items_written': 0,
            'items_skipped': 0,
            'carriers_filtered': 0,
        }

    # ----- Plugin entrypoints -----
    def run(self):
        if zmq is None:
            raise plugins.PluginException("pyzmq not installed. Install with: pip install pyzmq")

        # Ensure DB exists / up to date so we can resolve stations & items.
        self.tdb.reloadCache()
        self._prepare_item_symbol_map()
        self._connect()

        started = time.time()
        self.tdenv.NOTE("EDDN: listening on {} (carrier_only={}, public_only={})",
                        self.host, self.carrier_only, self.public_only)

        try:
            self._consume_loop(started)
        except KeyboardInterrupt:
            self.tdenv.NOTE("EDDN: interrupted by user")
        finally:
            try:
                if self._sub is not None:
                    self._sub.close(0)
                if self._ctx is not None:
                    self._ctx.term()
            except Exception:
                pass

        if self.optimize:
            try:
                self.tdb.getDB().execute("VACUUM")
            except sqlite3.Error:
                pass
        self.tdb.close()

        self.tdenv.NOTE("EDDN: done. msgs={} stations={} items={} skipped_items={}",
                        self._stats['messages'], self._stats['stations_matched'],
                        self._stats['items_written'], self._stats['items_skipped'])
        # We handled the import fully; stop import_cmd from proceeding
        return False

    def finish(self):
        # Not used; run() does the work.
        return False

    # ----- EDDN handling -----
    def _connect(self):
        self._ctx = zmq.Context.instance()
        self._sub = self._ctx.socket(zmq.SUB)
        self._sub.setsockopt(zmq.SUBSCRIBE, b"")
        self._sub.connect(self.host)

    def _consume_loop(self, started: float):  # pylint: disable=too-many-branches
        poller = zmq.Poller()
        poller.register(self._sub, zmq.POLLIN)
        while True:
            if self.duration and (time.time() - started) >= self.duration:
                return
            events = dict(poller.poll(timeout=500))
            if self._sub not in events:
                continue
            try:
                zdata = self._sub.recv(flags=zmq.NOBLOCK, copy=False)
            except zmq.error.Again:
                continue
            self._stats['messages'] += 1
            try:
                payload = zlib.decompress(zdata)
            except Exception as e:  # pragma: no cover
                self.tdenv.DEBUG1("EDDN: zlib error {}", e)
                continue
            try:
                data = json.loads(payload)
            except Exception as e:  # pragma: no cover
                self.tdenv.DEBUG1("EDDN: json error {}", e)
                continue
            if self.debug_dump:
                try:
                    os.makedirs(self.tdenv.tmpDir, exist_ok=True)
                    tmp = os.path.join(self.tdenv.tmpDir, 'eddn_last.json')
                    with open(tmp, 'wb') as fh:
                        fh.write(payload)
                except Exception:
                    pass

            schema = data.get('$schemaRef', '')
            if schema.endswith('/commodity/3'):
                self._stats['commodity_messages'] += 1
                self._handle_commodity(data)
            elif schema.endswith('/journal/1'):
                self._stats['journal_messages'] += 1
                # Currently unused, but could be extended to map MarketID->Station
                # self._handle_journal(data)

    # ----- Commodity processing -----
    def _handle_commodity(self, data: dict):  # pylint: disable=too-many-locals,too-many-branches
        msg = data.get('message') or {}
        system_name = (msg.get('systemName') or '').strip()
        station_name = (msg.get('stationName') or '').strip()
        station_type = (msg.get('stationType') or '').strip()
        docking = msg.get('carrierDockingAccess')
        market_id = msg.get('marketId')
        ts = msg.get('timestamp')
        commodities = msg.get('commodities') or []

        if self.carrier_only and station_type != 'FleetCarrier':
            self._stats['carriers_filtered'] += 1
            return
        if self.public_only and docking and str(docking).lower() != 'all':
            self._stats['carriers_filtered'] += 1
            return

        if not (system_name and station_name and ts and commodities):
            return

        # Resolve station_id via (System.name, Station.name)
        db = self.tdb.getDB()
        cur = db.cursor()
        cur.execute("SELECT system_id FROM System WHERE name = ? COLLATE NOCASE", (system_name,))
        row = cur.fetchone()
        if not row:
            self._stats['stations_skipped'] += 1
            self.tdenv.DEBUG1("EDDN: unknown system '{}' for station '{}'", system_name, station_name)
            return
        system_id = int(row[0])
        cur.execute(
            "SELECT station_id FROM Station WHERE system_id = ? AND name = ? COLLATE NOCASE",
            (system_id, station_name),
        )
        row = cur.fetchone()
        if not row:
            self._stats['stations_skipped'] += 1
            self.tdenv.DEBUG1("EDDN: unknown station '{} @ {}'", station_name, system_name)
            return
        station_id = int(row[0])

        # Update docking policy if provided
        if docking:
            try:
                cur.execute(
                    "UPDATE Station SET carrier_docking_access = ? WHERE station_id = ?",
                    (str(docking), station_id),
                )
            except sqlite3.Error:
                pass

        # Timestamp handling and station-level dedupe
        try:
            ts_unix = _to_unix(ts)
        except Exception:
            ts_unix = int(time.time())
        last_mod = self._last_station_mod.get(station_id, 0)
        if last_mod and ts_unix <= last_mod:
            # Older or equal snapshot; ignore
            return

        # Flush old StationItem rows for this station and insert fresh snapshot
        try:
            cur.execute("BEGIN TRANSACTION")
            cur.execute("DELETE FROM StationItem WHERE station_id = ?", (station_id,))
            add_stmt = (
                "INSERT OR REPLACE INTO StationItem (station_id, item_id, modified, from_live, "
                "demand_price, demand_units, demand_level, supply_price, supply_units, supply_level) "
                "VALUES (?, ?, datetime(?, 'unixepoch'), 1, ?, ?, ?, ?, ?, ?)"
            )
            written = 0
            for c in commodities:
                sym = c.get('name')
                if not sym:
                    continue
                item_id = self._item_symbol_map.get(self._norm_symbol(sym))
                if not item_id:
                    self._stats['items_skipped'] += 1
                    self.tdenv.DEBUG1("EDDN: unknown commodity '{}' at '{} @ {}'", sym, station_name, system_name)
                    continue
                # Map CAPI fields into TD StationItem columns
                demand_price = int(c.get('sellPrice') or 0)
                demand_units = int(c.get('demand') or 0)
                demand_level = int((c.get('demandBracket') or -1) or -1)
                supply_price = int(c.get('buyPrice') or 0)
                supply_units = int(c.get('stock') or 0)
                supply_level = int((c.get('stockBracket') or -1) or -1)
                cur.execute(
                    add_stmt,
                    (
                        station_id, item_id, ts_unix,
                        demand_price, demand_units, demand_level,
                        supply_price, supply_units, supply_level,
                    ),
                )
                written += 1
            cur.execute("COMMIT")
            if written:
                self._stats['stations_matched'] += 1
                self._stats['items_written'] += written
                self._last_station_mod[station_id] = ts_unix
        except sqlite3.Error as e:  # pragma: no cover
            try:
                cur.execute("ROLLBACK")
            except Exception:
                pass
            self.tdenv.WARN("EDDN DB error at '{} @ {}': {}", station_name, system_name, e)

    # ----- Helpers -----
    def _prepare_item_symbol_map(self):
        """Build a map from EDDN/CAPI commodity symbolic names to Item.item_id.

        We derive a normalized symbol from TD's Item.name to avoid needing
        external mapping files. This handles typical punctuation/hyphen/space
        variants between CAPI and DB naming.
        """
        db = self.tdb.getDB()
        cur = db.execute("SELECT item_id, name FROM Item")
        sym_map: dict[str, int] = {}
        for item_id, name in cur.fetchall():
            sym = self._norm_symbol(name)
            # Avoid overwrites; first wins
            sym_map.setdefault(sym, int(item_id))
        self._item_symbol_map = sym_map

    @staticmethod
    def _norm_symbol(name: str) -> str:
        s = name.strip().lower()
        # Normalize common punctuation/spaces to underscore
        s = s.replace('&', 'and')
        s = re.sub(r"[\s\-]+", "_", s)
        s = re.sub(r"[\.\'\(\)\[\],]", "", s)
        # Collapse multiple underscores
        s = re.sub(r"_+", "_", s)
        return s
