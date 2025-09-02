Trade Dangerous — GUI Fork
==========================

Overview
This fork packages the upstream Trade Dangerous tooling together with a modern, self‑contained GUI and a handful of quality‑of‑life improvements aimed at reliability and carrier usability.

Quick Start
- Run the GUI: `python3 td_gui.py`
- Or run from CLI as usual: `python3 trade.py <command> [options]`

Key Differences vs Upstream
- New GUI (`td_gui.py`)
  - Dynamic form builder for every TD subcommand with live preview and one‑click Run.
  - Route cards parsed from `run` output (copy destination, swap into --from).
  - Sticky sessions: saves per‑command options, output, selected tab, and scroll position to `~/.config/TradeDangerous/td_gui_prefs.json` (Linux/macOS) or `%APPDATA%\TradeDangerous` (Windows).
  - Color‑themed dark UI with resizable panes and searchable output.
  - Convenience commands:
    - “Update/Rebuild DB” (uses eddblink with sensible options).
    - “Rebuild DB (-i -f)” runs `buildcache -i -f` in one click.
  - For the `buildcache` command, the GUI shows a dedicated “Rebuild” group and pre‑selects `--ignore-unknown` and `--force` (you can uncheck).

- Fleet Carrier docking access awareness
  - Data model: stations carry `carrier_docking_access` (e.g. `All`, `Friends`, `Squadron`, `Squadron Friends`).
    - Schema: `tradedangerous/templates/TradeDangerous.sql` adds a `carrier_docking_access` column.
    - One‑time migration: `tradedangerous/templates/database_changes.json` applies the ALTER when TD next starts.
  - Import: `spansh` plugin ingests `carrierDockingAccess` and stores it.
  - Routing: carriers are considered only when access is explicitly `All`.
    - `tradedangerous/tradedb.py:getDestinations()` filters destinations.
    - `tradedangerous/commands/run_cmd.py:checkStationSuitability()` enforces during planning.

- More robust cache (prices) rebuilds
  - Auto‑rebuilds triggered by TD temporarily run with `ignoreUnknown=True` to avoid aborting on stale data.
  - Duplicate `@ SYSTEM/Station` blocks in `.prices` are tolerated when ignoring unknowns; the later block wins (a NOTE is printed).
  - A corrections entry suppresses a known non‑tradeable construction‑ship line that appears in some `.prices` files.

Files Added/Updated (high‑level)
- `td_gui.py` — new GUI application.
- `tradedangerous/templates/TradeDangerous.sql` — adds `carrier_docking_access` to `Station`.
- `tradedangerous/templates/database_changes.json` — safe, one‑time DB migration.
- `tradedangerous/plugins/spansh_plug.py` — imports `carrierDockingAccess` and writes it to the DB.
- `tradedangerous/tradedb.py` — destination filtering; safer rebuild path; exposes new Station attribute.
- `tradedangerous/commands/run_cmd.py` — excludes non‑public carriers when choosing stops.
- `tradedangerous/cache.py` — tolerant duplicate handling when ignoring unknowns.
- `tradedangerous/corrections.py` — deletion mapping for an invalid station line.

Usage Notes
- Populate carrier access: `python3 trade.py import -P spansh`
- Force rebuild (CLI): `python3 trade.py buildcache -i -f`
- From the GUI: use “Rebuild DB (-i -f)” or select `buildcache` and keep the two checkboxes enabled.

Limitations
- Carrier access relies on imported Spansh data; without it, access status is unknown and carriers are excluded by design.
- This fork does not add Powerplay trade support (PP cargo is non‑market, outside TD’s price model).

Dedication (GUI)
To the Trade Dangerous community and maintainers past and present — thank you for the years of work that made TD the go‑to tool for commanders. This GUI is dedicated to every pilot who wanted TD’s power with fewer terminal hoops, and to those who generously share data and time to keep the galaxy mapped and the markets honest. o7

How To Publish This Fork
1) Create a new GitHub repo (empty):
   - Suggested name: `Trade-Dangerous-GUI` or `trade-dangerous-gui-fork`
2) In this working copy:
   - `git remote remove origin`    # if present and pointing upstream
   - `git remote add origin https://github.com/<you>/<repo>.git`
   - `git push -u origin HEAD`

License
This fork keeps the original project’s license. See `LICENSE` in the repository root.

