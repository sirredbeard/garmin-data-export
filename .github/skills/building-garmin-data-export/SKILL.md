---
name: building-garmin-data-export
description: Build, validate, and maintain garmin-data-export.
---

# Building garmin-data-export

Use the repository root as the working directory. The tool is a Python
package, `garmin_daily`. There is no .NET project anymore; the wrapper
only forwarded arguments to the Python script, and it was removed in
the daily-use rebuild.

## Dependencies

Python 3.12 or later, in the checkout's virtual environment:

```bash
.venv/bin/pip install -r requirements.txt
```

## Validation

Run the smallest checks that cover the change:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m py_compile garmin_daily/*.py
.venv/bin/python -m garmin_daily --help
```

The tests run offline. Do not run a real sync or export as a build
check. A sync needs the token cache and makes live API calls.

## Upstream API changes

Check the
[python-garminconnect release notes](https://github.com/cyberjunky/python-garminconnect/releases)
before changing API calls. Release 0.3.17 made goal pagination
one-based, so calls to `get_goals()` must use `start=1` or later.
