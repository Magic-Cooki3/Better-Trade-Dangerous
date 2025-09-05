Better Trade Dangerous (GUI + Live Ingest)
=========================================

This fork packages Trade Dangerous with a modern GUI and live data ingestion options. It remains compatible with the CLI and core data model while adding quality-of-life features for carrier‑heavy trading and streamlined updates.

Requires Python 3.8.19+.

Troubleshooting
- Import says “database disk image is malformed”:
  - The GUI/eddblink auto‑repair backs up the DB to `data/TradeDangerous.db.bak`, rebuilds, and retries. If running from the CLI, stop EDDN/other writers first, then rerun. Manual reset: `rm data/TradeDangerous.db && python3 trade.py import -P eddblink -O clean,all,skipvend,force`.
- Live listings seems slow (10–25 min typical):
  - The dump is large (tens of MB gzipped → millions of rows). Keep the DB on SSD; pause EDDN during the job to avoid writer contention; avoid `-O optimize` unless needed; use the GUI background tab and let it finish.
- Multiple route cards when `--routes 1`:
  - The GUI deduplicates and caps routes; if you still see multiple, make sure the `--routes` value is 1 (default) and you haven’t enabled `--summary` (which prints extra blocks). Report any reproducible cases with the full Output tab text.
- Run output looks compressed on one line:
  - The GUI only uses progress‑style streaming for imports/builds; `run` prints as clean line output. If you still see odd wrapping, increase the Output panel height or export the text to verify line breaks.
- Left or top pane collapsed on startup:
  - The app now sets initial sash positions (≈30% left, ≈40% top) and minimum sizes. Drag to adjust; it won’t auto‑reset during the session.
- Carriers missing or docking status unknown:
  - Import Spansh weekly to refresh station/services/access; keep EDDN Live running. The GUI excludes carriers unless docking access is `All` (public).
- VACUUM makes imports slow:
  - That’s expected. Use `-O optimize` occasionally, not every run.

Highlights
- EDDN Live import (new): Streams commodity snapshots from the public EDDN firehose to keep markets fresh (carriers and fixed stations).
- GUI background tabs (new): Long‑running imports (EDDN, EDDB Link, Spansh) run in dedicated tabs with their own Stop buttons, while you continue using the main Output tab.
- Spansh import preset (new): One‑click galaxy data import to seed/update systems, stations, services and carrier docking access.
- Safer rebuilds: Database rebuild/import flows tolerate unknowns and stale entries better.
- Progress bars and readable output: Build/import commands render live progress bars; normal commands (like `run`) use clean line output for readability.
- Route cards: The GUI deduplicates repeated route headers and respects `--routes` (default 1) when showing route cards.
- Closable tabs: Hover background tabs to reveal a “×” and click to stop/close. Foreground runs have their own red Stop button.
- Resilient layout: The left selector and the top “Selected Options” pane stay visible on startup; sashes remember user movement during the session.

Install
- Optional venv
  - python3 -m venv .venv && source .venv/bin/activate
- From repo root
  - pip install -r requirements.txt -e .

From Zero to a Working DB
1) Seed galaxy (systems/stations/services)
   - CLI: python3 trade.py import -P spansh
   - GUI: “Import Spansh Galaxy” (runs in a background tab)
   - Tip: For faster weekly refreshes, add -O maxage=7

2) Optional price backfill
   - CLI: python3 trade.py import -P eddblink -O listings_live
   - GUI: “Update Live Listings”

3) Start live prices
   - CLI (carriers only, public): python3 trade.py import -P eddn -O carrier_only,public_only
   - CLI (all markets): python3 trade.py import -P eddn
   - GUI: “EDDN Live (Carriers)” or “EDDN Live (All Markets)”
   - These run in their own tabs; you can run “run” or other commands simultaneously in the main Output tab.

Keeping Data Fresh
- EDDN Live: Leave running while you play for the freshest markets.
- Spansh: Weekly is a good default (or monthly if EDDN runs continuously). Re‑run if you see unknown stations/systems or service mismatches.
- EDDB Link: Optional nightly “listings_live” backfill to fill gaps when no one visits a station.

DB Options and Recommended Order/Frequency
1) Import Spansh Galaxy
   - What: Seeds/refreshes “galaxy structure” (systems, stations, pad size, services, carrier docking access). No prices.
   - When: First setup; then weekly (or after major game updates), and whenever you see unknown stations/systems or wrong services.
   - GUI: “Import Spansh Galaxy” (runs in background).

2) Update/Rebuild DB (EDDB Link: `clean,all,skipvend,force`)
   - What: Rebuilds schema and CSV‑based tables, then regenerates the DB and `.prices`. Good for first install or major updates.
   - When: First setup, after major schema/data changes, or if your DB becomes corrupted.
   - Notes: This is a heavy, DB‑exclusive task. The GUI pauses background writer tabs (EDDN, EDDB Link live, Spansh) before running, then restarts them after.

3) Update Live Listings (EDDB Link: `listings_live`)
   - What: Imports the community listings live dump to refresh market prices across many stations quickly.
   - When: Daily, or on demand before planning long runs. Add `-O optimize` occasionally to VACUUM (slower).
   - Tip: For best speed and to avoid writer contention, don’t run while EDDN Live is ingesting. If you do, SQLite will serialize writers (slower, but safe).

4) EDDN Live (Carriers) or EDDN Live (All Markets)
   - What: Streams live market snapshots from the community (via ZeroMQ). Carriers preset shows public carriers only; All Markets covers stations too.
   - When: Keep running while you play. This is the most up‑to‑date global stream wherever players visit.
   - Notes: Not a one‑shot DB job; runs until you stop it. Hover its tab and click “×” to stop/close.

Initial Order (fresh setup)
- Import Spansh Galaxy → Update/Rebuild DB → Update Live Listings → start EDDN Live.

Ongoing cadence
- EDDN Live: continuous while playing.
- Update Live Listings: daily or on demand.
- Spansh: weekly (or monthly if EDDN stays on and your area is stable).
- Rebuild DB: rarely; first setup or after big upstream changes.

GUI Overview
- Launch: python3 td_gui.py
- Quick options at top:
  - Update/Rebuild DB (EDDB Link)
  - Update Live Listings (EDDB Link live)
  - EDDN Live (Carriers)
  - EDDN Live (All Markets)
  - Import Spansh Galaxy
- Background tabs: Each import shows a timer, live log, and a red Stop button. You can return later and stop it from there.
- Closing background tabs: Hover the tab header to reveal a “×” and click it to stop the task and close the tab. Non‑background tabs (Output, Help) do not show an “×”.
- Output tab: Foreground commands (e.g., run) still show in the main Output tab with their own Stop button.

CLI Quickstart
- Best route near your current location:
  - python3 trade.py run --from "System/Station" --capacity 1040 --credits 1m --hops 3 --jumps-per 15 --ly-per 7 --pad-size L --no-planet
- Show nearby trading stations:
  - python3 trade.py local --near "System" --ly 30 --market
- Station details:
  - python3 trade.py station --stations --trading --near "System" --ly 10

Data Freshness and Affordability
- The optimizer respects both cargo capacity and credits available. If a suggested quantity exceeds what you can buy in‑game, your price data is likely stale or optimistic. Keep EDDN Live running (and optionally EDDB Link live) for the most accurate recommendations, and consider a margin (e.g., --margin 0.10) to buffer price drift.

Credits & License
- Trade Dangerous is Copyright (C) Oliver "kfsone" Smith and contributors.
- This fork retains the original license (see LICENSE).

Useful Links
- Original project wiki: https://github.com/eyeonus/Trade-Dangerous/wiki
- EDDN schemas: https://github.com/EDCD/EDDN/tree/master/schemas
- EDMC / EDDN info: https://github.com/EDCD/EDMarketConnector/wiki

Developer Notes: Placeholder Stations and WITHOUT ROWID
- Schema: `Station` is defined `WITHOUT ROWID` and uses an explicit `station_id` primary key.
- Placeholders: When parsing legacy `.prices` content or OCR‑noisy station names, the cache builder may create a local placeholder station so price lines can be imported. Because `WITHOUT ROWID` disables implicit rowids, placeholder rows must be inserted with an explicit primary key — we use negative `station_id` values (e.g., −1, −2, …) to avoid colliding with real IDs.
- Scope: Placeholders exist only in the local SQLite DB; they are not written back to the CSV templates. They let routes work when price lines reference a station not present in the current CSV snapshot.
- Cleanup: As soon as a proper Station entry arrives via Spansh/EDDB imports, normal positive IDs replace placeholders organically. Tools that query by foreign key should tolerate negative station IDs.
