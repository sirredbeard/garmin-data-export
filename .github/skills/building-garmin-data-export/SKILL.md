---
name: building-garmin-data-export
description: Build, validate, and maintain garmin-data-export.
---

# Building garmin-data-export

Use the repository root as the working directory.

## Dependencies

Install the Python dependencies before running the exporter:

```bash
python3 -m pip install -r requirements.txt
```

The .NET wrapper targets `net11.0` and copies `garmin_export.py` and
`requirements.txt` into its output directory.

## Validation

Run the smallest checks that cover the change:

```bash
python3 -m py_compile garmin_export.py
python3 garmin_export.py --help
dotnet build
```

There is no checked-in test suite. Do not run a real export as a build check.
It needs Garmin credentials and can make many API calls.

## Upstream API changes

Check the
[python-garminconnect release notes](https://github.com/cyberjunky/python-garminconnect/releases)
before changing API calls. Release `0.3.17` made goal pagination one-based, so
calls to `get_goals()` must use `start=1` or a later value.
