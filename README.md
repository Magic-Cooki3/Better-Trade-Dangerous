Better Trade Dangerous (GUI + Live Ingest)
=========================================

This fork packages Trade Dangerous with a modern GUI and live data ingestion options. It remains compatible with the CLI and core data model while adding quality-of-life features for carrier‑heavy trading and streamlined updates.

Requires Python 3.8.19+.

Highlights
- EDDN Live import (new): Streams commodity snapshots from the public EDDN firehose to keep markets fresh (carriers and fixed stations).
- GUI background tabs (new): Long‑running imports (EDDN, EDDB Link, Spansh) run in dedicated tabs with their own Stop buttons, while you continue using the main Output tab.
- Spansh import preset (new): One‑click galaxy data import to seed/update systems, stations, services and carrier docking access.
- Safer rebuilds: Database rebuild/import flows tolerate unknowns and stale entries better.

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
