# AGENTS.md -- AI context for garmin-data-export

This file is for AI agents working on this project or reading its
data. For human setup instructions, see README.md.

## What this project does

A Python package, `garmin_daily`, pulls Garmin Connect data into a
local JSON store and keeps it current with a small daily sync. It was
rebuilt in November 2026 from a bulk-export tool. The old design wrote
one giant text file for NotebookLM. This design writes a store an
agent can query every day, and keeps the bulk file as one command
that renders the store.

No official Garmin API exists for personal use. Auth goes through
Garmin's SSO via python-garminconnect and garth. Tokens cache under
`~/.garminconnect` for about a year.

## The one rule for reading data

Read summaries, not raw files, unless the summary cannot answer the
question. A summary is about 2 KB and holds the day's steps, sleep
score and stages, stress, resting heart rate, HRV, body battery,
SpO2, respiration, intensity minutes, training readiness, weigh-ins,
and activity summaries. A raw day is hundreds of KB of minute-level
time series.

Fastest paths, in order:

1. `data/summaries/latest.json` -- newest complete day
2. `data/summaries/week.json` -- last seven complete days, with averages
3. `data/daily/<date>.summary.json` -- any single day
4. The read commands below, which print the same data as JSON
5. `data/daily/<date>.json` -- raw, only when the question needs it

## Commands

Run from the repository root. Global flags go before the subcommand.

```
.venv/bin/python -m garmin_daily status
.venv/bin/python -m garmin_daily show                 # yesterday
.venv/bin/python -m garmin_daily show today
.venv/bin/python -m garmin_daily show 2026-11-05 --format md
.venv/bin/python -m garmin_daily show --week
.venv/bin/python -m garmin_daily activities --days 7
.venv/bin/python -m garmin_daily weight --days 30
.venv/bin/python -m garmin_daily sync                 # last 3 days
.venv/bin/python -m garmin_daily sync --days 7
.venv/bin/python -m garmin_daily rebuild              # offline, from stored raw
```

Contracts:

- Read commands (`show`, `activities`, `weight`, `status`, `rebuild`)
  never use the network. They print one JSON object to stdout. Logs go
  to stderr. Parse stdout only.
- `--fetch` on a read command syncs that command's window first.
- `sync` prints a JSON run report to stdout: window, days fetched,
  activity count, per-kind range counts, errors, API call count.
- Exit codes: 0 clean, 2 authentication failed, 3 partial (something
  failed, the store still holds what was fetched). A partial sync
  usually repairs itself on the next run, because the window overlaps.
- "Complete day" and "latest" mean days that have ended. Today is
  excluded from `latest.json` and the week rollup even after a sync.

## Which command answers which question

| Question | Use |
|----------|-----|
| How did he sleep, what was the score | `show yesterday`, or latest.json |
| Stress yesterday, or stress after a walk | `show <date>` |
| Steps today so far | `sync --today`, then `show today` |
| This week's training | `activities --days 7`, `show --week` |
| Weight trend | `weight --days 30` or `--days 90` |
| Is the data current | `status` (check `last_sync.ran_at` and `latest_complete_day`) |
| A number the summary lacks | `show <date> --raw`, search the raw day |
| Long-term analysis in Gemini Notebook | `export --all --split` (slow, see below) |

## Freshness

A systemd user timer on the board runs `scripts/daily.sh` at 07:15
local: a three-day sync, logged under `data/logs/`. If `status` shows
the last sync is older than a day, run a sync yourself before
answering a health question. Do not answer from stale summaries and
present them as current.

Sync always refetches the days in its window. Three days is the
default because yesterday's sleep is often finalized overnight and a
watch can sync late. Historical days outside the window are not
refetched. Activity detail is fetched once per activity.

Known gap: Garmin lost this account's stress and sleep data for
roughly October 22 to November 1, 2026. Days in that window may be
stored incomplete. That is missing source data, not a sync failure.
An incomplete day has `"complete": false` in its summary. Report the
gap. Do not treat missing days as zeros, in averages or in prose.

## Authentication failures

If a sync exits 2, the token cache is gone or expired. The fix is one
interactive login, and it has to be the account owner at a terminal:

```
.venv/bin/python -m garmin_daily login
```

Never ask for the Garmin password in chat, and never put credentials
in a command line. If you are running non-interactively, report that
the login is needed and stop.

## Bulk export

```
.venv/bin/python -m garmin_daily export --days 90 --compact
.venv/bin/python -m garmin_daily export --all --split
```

`--all` backfills to the oldest activity on the account. On a
multi-year account this is thousands of API calls and can run for
hours. It is resumable (stored days are skipped), but do not start it
to answer a daily question. Output is plain text with raw JSON, no
code fences, split under 500K words per file for Gemini Notebook.

## Architecture

```
garmin_daily/
  client.py     auth, adaptive rate limiter, safe_call (None on missing data)
  fetch.py      the only network path: fetch_day, fetch_range,
                fetch_activities, sync, backfill, rebuild_summaries
  store.py      the layout, atomic JSON writes, manifest and state
  summarize.py  pure extraction from raw responses to summaries,
                plus the markdown renderers. Testable offline.
  export.py     bulk text rendering of the store, section fetches
  cli.py        subcommands and the stdout JSON contracts
tests/          unittest, offline, shapes pinned from real responses
scripts/daily.sh
deploy/         systemd service and timer
```

Design choices worth keeping:

- Sync is the only writer and the only network path. Reads are pure.
- Summaries are derived data. `rebuild` regenerates every summary,
  the activity index, the manifest, and the rollups from stored raw
  data with no network. If summarize.py changes, run `rebuild`.
- Writes are atomic (temp file, then rename).
- Missing data is explicit. `safe_call` returns None, sections store
  nothing, summaries mark the day incomplete.

Garmin response facts the code depends on (all pinned in tests):

- The sleep score is at `dailySleepDTO.sleepScores.overall.value`.
- Weights, bone mass, and muscle mass arrive in grams.
- Sleep stage fields and daily-summary stress durations are seconds.
- Sleep start and end timestamps are epoch milliseconds; the "local"
  variant is wall time encoded as an epoch.
- Body battery high, low, and at-wake are precomputed on the daily
  summary. The per-day endpoint returns a sparse values array only.
- Some range endpoints reject spans over about a year with a 400.
- `get_goals()` pagination is one-based since garminconnect 0.3.17.

## Validation

```
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m py_compile garmin_daily/*.py
.venv/bin/python -m garmin_daily --help
.venv/bin/python -m garmin_daily status   # against the real store
```

Do not run `export --all` as a validation step.

## Files

| File | Purpose |
|------|---------|
| `garmin_daily/` | The package. Start at cli.py, then fetch.py. |
| `pyproject.toml` | Package metadata, `garmin-daily` console script |
| `requirements.txt` | garminconnect and garth |
| `README.md` | Human-facing setup and usage |
| `AGENTS.md` | This file |
| `data/` | The store. Gitignored. Never commit it. |
| `.gitignore` | Blocks data/, export/, .env, token cache |
