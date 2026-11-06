---
name: using-garmin-data-export
description: Run garmin-data-export and choose commands safely.
---

# Using garmin-data-export

Log in once to cache Garmin tokens:

```bash
.venv/bin/python -m garmin_daily login
```

Tokens are stored under `~/.garminconnect`. Credentials come from
`GARMIN_EMAIL` and `GARMIN_PASSWORD`, or a `.env` file.

## Daily commands

```bash
.venv/bin/python -m garmin_daily sync            # last 3 days
.venv/bin/python -m garmin_daily show            # yesterday, JSON
.venv/bin/python -m garmin_daily show --week
.venv/bin/python -m garmin_daily activities --days 7
.venv/bin/python -m garmin_daily weight --days 30
.venv/bin/python -m garmin_daily status
```

Read commands never use the network and print JSON to stdout. Add
`--fetch` to sync first. Global flags (`--data`, `--tokenstore`,
`--delay`) go before the subcommand.

## Bulk export

```bash
.venv/bin/python -m garmin_daily export --days 90 --compact
.venv/bin/python -m garmin_daily export --all --split
```

`--all` backfills the whole account history and can run for hours.
It resumes where it stopped. Do not start it for a daily question.

Data, exports, and credentials stay out of version control through
`.gitignore`. The store defaults to `./data`; override with `--data`
or `GARMIN_DATA_DIR`.
