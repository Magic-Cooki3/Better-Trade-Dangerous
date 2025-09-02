
----------

TradeDangerous  
Copyright (C) Oliver "kfsone" Smith, July 2014  
Copyright (C) Bernd 'Gazelle' Gollesch 2016, 2017  
Copyright (C) Jonathan 'eyeonus' Jones 2018 - 2021

REQUIRES PYTHON 3.8 OR HIGHER.

----------

Better Trade Dangerous (GUI, Feature, and Bug Improvement Fork)
=================================

This fork packages Trade Dangerous with a modern GUI and several quality‑of‑life improvements, while remaining fully compatible with the original command‑line tools.

I have also fixed bugs that are not being worked on by the original dev as of yet and it was easier to just make this then deal with how many bug tickets I'd have to open and that's only if they can reproduce it.

What’s Different vs the Original
--------------------------------

- Fleet Carrier docking access awareness
  - Stations now store `carrier_docking_access` (e.g. `All`, `Friends`, `Squadron`, `Squadron Friends`).
  - The Spansh import plugin ingests this value.
  - Routing only uses Fleet Carriers with access `All`; restricted/unknown carriers are excluded to avoid dead ends.
- More robust cache rebuilds
  - Automatic DB rebuilds run with “ignore unknown” enabled to avoid aborting on stale `.prices` lines.
  - Duplicate `@ SYSTEM/Station` blocks in `.prices` are tolerated when ignoring unknowns — the later block wins.
  - A corrections entry suppresses a known non‑tradeable construction‑ship station line.
- Data model & templates
  - `tradedangerous/templates/TradeDangerous.sql` adds `carrier_docking_access`.
  - One‑time migration: `tradedangerous/templates/database_changes.json` is applied automatically on first run.
- Repo hygiene
  - Generated data (`data/`, `*.prices`, `*.db`, caches) stays out of version control.

The New GUI (td_gui.py)
-----------------------

- Dynamic forms for all TD subcommands with typed inputs.
- Live Preview of the exact CLI that will run.
- One‑click Run with a colorized, scrollable console.
- Route cards for `run` output: copy destination or swap into `--from` with a click.
- Sticky sessions: per‑command options, output, tab, scroll, and route selection persist in user config.
- Convenience entries:
  - “Rebuild DB (-i -f)” (runs `buildcache -i -f`).
  - “Update/Rebuild DB” (eddblink import with sensible options).
- Buildcache ergonomics: groups the buildcache flags and pre‑selects `--ignore-unknown` and `--force`.
- Dark theme and resizable panes; Help tab shows each command’s `-h` output.

Install, Setup, and Use
-----------------------

Requirements
- Python 3.8.19 or newer
- Pip and (optionally) a virtual environment

1) Install
```
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
```

2) Build the cache (creates `./data/TradeDangerous.db`)
```
python3 trade.py buildcache -i -f
```

3) (Recommended) Import Spansh data so carrier access is known
```
python3 trade.py import -P spansh
```

4) Launch the GUI
```
python3 td_gui.py
```

Notes
- The app generates `./data/` and `.prices` locally; these are not part of the repo.
- Carriers only appear when `carrier_docking_access` is `All`. Import Spansh to populate these values.

# What is Trade Dangerous? <img align="right" src="https://raw.githubusercontent.com/wiki/eyeonus/Trade-Dangerous/TradeDangerousCrest.png" alt="Trade Dangerous Crest">

TradeDangerous is a set of powerful trading tools for Elite Dangerous, organized around one of the most powerful trade run optimizers available.

The TRO is a heavy hitter that can calculate complex routes with multiple stops while taking into account the profits you make along the route 

The price data in TradeDangerous is either manually entered or crowd sourced from a website such as [Tromador's Trading Dangerously](http://elite.tromador.com/ "Tromador's Trading Dangerously"), often using a plugin such as the included eddblink.

# What can it do for me?

You're in a ship with 8 cargo spaces that can make 8.56 ly per jump; you're willing to make upto 2 jumps between stations, and we want to see how much money we can make if in 2 trade stops (hops).

    trade.py run --credits 5000 --capacity 8 --ly-per 8.56 --jumps 2 --hops 2

If we ran this, TD would search the galaxy for trade runs. But it could take us days to reach some of them. So lets say we're currently at Kummer City in the Andere system.

    trade.py run --from "andere/kummer city"
        --credits 5000 --capacity 8 --ly-per 8.56 --jumps 2 --hops 2

(The above represents a single line)

That's a lot to type. TD is designed to support laziness when it comes to typing, so it allows for all kinds of short-cuts.

    trade.py ru
        --fr and/kumm     find a station matching 'kumm' in a
                          system matching 'and'
        --cr 5k           'k', 'm' and 'b' are recognized suffixes
        --cap 8           8 units of cargo
        --ly 8.56         maximum distance *per jump*
        --ju 2            maximum 2 jumps

The default for hops is 2, so I didn't have to include it.

You can also use "=" to connect an option with its values:

    trade.py ru --fr=and/kumm --cr=5k --cap=8 --ly=8.56 --ju=2

With the data at the time I write this, this produces:

    ANDERE/Kummer City -> ANDERE/Malzberg Vision
      ANDERE/Kummer City: 6 x Titanium, 2 x Polymers,
      G 224-46/Lorrah Dock: 7 x Coltan, 1 x Lepidolite,
      ANDERE/Malzberg Vision +8,032cr (502/ton)

This tells us our overall route (line #1), what load to pick up from the first station, what to sell it for and pick up at the second stop and where to finish and unload for our final profit.

Note that it could have just told us to pick up 6 Titanium (the max we could afford) or 8 Copper (the highest profit we could fill up with), Instead, TD crunched hard numbers and maximized the earnings of every cargo space AND credit.

If you want to give Trade Dangerous a try, look no further than the [Setup Guide](https://github.com/eyeonus/Trade-Dangerous/wiki/Setup-Guide "Setup Guide") and the [User Guide](https://github.com/eyeonus/Trade-Dangerous/wiki/User-Guide "User Guide").

Curious about programming with Trade Dangerous/Python? Take the [Python Quick Peek](https://github.com/eyeonus/Trade-Dangerous/wiki/Python-Quick-Peek "Python Quick Peek").
