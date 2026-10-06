---
name: using-garmin-data-export
description: Run garmin-data-export and choose export modes safely.
---

# Using garmin-data-export

Install Python 3.12+ and the dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Log in once to cache Garmin tokens:

```bash
python3 garmin_export.py --login
```

The tool reads `GARMIN_EMAIL` and `GARMIN_PASSWORD`, or the same values from
a `.env` file. Tokens are stored under `~/.garminconnect`.

## Common exports

```bash
python3 garmin_export.py
python3 garmin_export.py --all --split
python3 garmin_export.py --update
python3 garmin_export.py --all --compact
```

Use `--update` after the initial full export. It fetches the overlapping date
range needed for late-arriving Garmin data. Use `--no-cache` only when a clean
re-fetch is intentional.

The .NET wrapper accepts the same flags:

```bash
dotnet run -- --all --split
```

Exports and credentials stay out of version control through `.gitignore`.
Choose `--output` when working with a directory that should not contain
health data by default.
