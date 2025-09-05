Develop Trade-Dangerous
=======================

## Setup Environment

__Linux/Mac__
```bash
git clone https://github.com/eyeonus/Trade-Dangerous
cd Trade-Dangerous
python3 -m venv venv
. venv/bin/activate
pip3 install -r requirements-dev.txt -e .
```

__Windows__ (powershell)
```powershell
git clone https://github.com/eyeonus/Trade-Dangerous
cd Trade-Dangerous
# This requires a python version >= 3.8.19
python3 -m venv venv
.\venv\Scripts\activate.ps1
pip3 install -r requirements-dev.txt -e .
```

## Generate Documentation

__Linux/Mac__
```bash
cd docs
make html
```

__Windows__
```powershell
cd docs
.\make.bat html
```

### Generate apidoc

```bash
cd docs
sphinx-apidoc -f -s md -o  source/ ../tradedangerous ../tradedangerous/mfd ../tradedangerous/templates ../tradedangerous/commands

## Data Model Notes

### Station table is WITHOUT ROWID
The `Station` table is defined `WITHOUT ROWID` and requires an explicit `station_id` primary key on insert. This improves index locality and joins, but means SQLite will not auto‑generate IDs.

### Placeholder stations during cache builds
While parsing legacy `.prices` files, the cache builder may encounter station names that aren’t present in the current CSV snapshot (e.g., OCR artifacts or renamed stations). To avoid discarding those price lines, the builder creates a local placeholder station with a negative `station_id` (−1, −2, …). This keeps foreign keys consistent and prevents clashes with real IDs.

- Placeholders are local only; they are not written to the CSV templates.
- Once a proper Station entry is imported via Spansh/EDDB, the placeholder will naturally become unused.
- Code that joins on `Station.station_id` should tolerate negative IDs.
```
