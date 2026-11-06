# Garmin Connect Data Export

Pulls your health and fitness data from Garmin Connect into a local
store on your own machine, in a shape a personal agent can use every
day. One JSON file per day, one per activity, and a small summary for
each day with the numbers a daily check-in actually needs: steps,
sleep, stress, resting heart rate, HRV, body battery, workouts, and
weigh-ins.

It started as a bulk export tool for NotebookLM (now Gemini Notebook):
one giant text file with everything in it. That file still exists, as
the `export` command, for long-term analysis. Day to day, nobody wants
to parse a 170 MB text file to answer "how did I sleep last night."
The store is the tool now. The export is a report the store can print.

No Garmin developer API key required. Under the hood this uses the
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
library, which logs in through the same SSO flow as the Garmin website.

## Setup

Requires Python 3.12 or later.

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Log in once. Tokens cache under `~/.garminconnect` for about a year,
and every later run is non-interactive. That is what makes a scheduled
daily sync possible.

```
.venv/bin/python -m garmin_daily login
```

It asks for your Garmin email and password (sent directly to Garmin's
servers, not stored by this tool) and an MFA code if you have one. Or
put the credentials in a `.env` file next to the checkout:

```
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=your-password
```

## Daily use

```
# Pull today, yesterday, and the day before. The default window is
# three days on purpose: today's numbers are still moving, yesterday's
# sleep is often finalized overnight, and a watch can sync late.
.venv/bin/python -m garmin_daily sync

# Read yesterday's summary. No network, prints JSON.
.venv/bin/python -m garmin_daily show

# Today so far, as a one-page summary instead of JSON.
.venv/bin/python -m garmin_daily show today --format md

# The last seven complete days, rolled up.
.venv/bin/python -m garmin_daily show --week

# Workouts and weigh-ins.
.venv/bin/python -m garmin_daily activities --days 7
.venv/bin/python -m garmin_daily weight --days 30

# What is in the store, when it last synced, whether tokens are cached.
.venv/bin/python -m garmin_daily status
```

Read commands (`show`, `activities`, `weight`, `status`) never touch
the network. They read the store and print one JSON object to stdout.
Logs go to stderr, so stdout stays safe to pipe into another program.
Add `--fetch` to any read command to sync its window first.

Sync windows:

```
.venv/bin/python -m garmin_daily sync --today           # today only
.venv/bin/python -m garmin_daily sync --yesterday       # yesterday only
.venv/bin/python -m garmin_daily sync --days 7          # the past week
.venv/bin/python -m garmin_daily sync --date 2026-11-05 # one day
.venv/bin/python -m garmin_daily sync --from 2026-10-01 --to 2026-10-31
.venv/bin/python -m garmin_daily sync --no-detail       # skip activity detail calls, faster
```

On the test account, a one-day sync takes about 8 seconds and a
seven-day sync about 26 seconds.

## Run it every day

`scripts/daily.sh` runs the three-day sync and logs to
`data/logs/`. Two ways to schedule it:

```
# systemd user timer (units in deploy/, runs at 07:15 local)
cp deploy/garmin-daily.service deploy/garmin-daily.timer ~/.config/systemd/user/
systemctl --user enable --now garmin-daily.timer

# or cron
15 7 * * * /path/to/garmin-export/scripts/daily.sh
```

07:15 is deliberate. Overnight sleep and HRV are final by then, and
the previous day is complete.

## What sync pulls

Per day: daily summary (steps, distance, floors, calories), heart
rate, resting heart rate, sleep with stages and score, all-day stress,
SpO2, respiration, HRV, body battery and its events, intensity
minutes, all-day events, lifestyle logging, training readiness
(regular and morning), hydration, and nutrition.

Per window, in one call each: weigh-ins, body composition, daily step
totals, resting heart rate, HRV summaries, VO2 max and fitness age,
and blood pressure.

Per activity in the window: the summary, plus full detail -- splits,
HR and power zones, exercise sets, weather, and time-series data.
Detail is fetched once per activity and kept, because it costs nine
calls and does not change.

If a category has no data for your account, it stores nothing for it
and says so in the summary. Nothing is silently dropped: a day with
no sleep record is marked incomplete, not zero.

## The store

Everything lives under `data/` (override with `--data` or
`GARMIN_DATA_DIR`). The layout is documented at the top of
`garmin_daily/store.py`. The short version:

- `data/daily/2026-11-05.json` -- raw API responses for one day
- `data/daily/2026-11-05.summary.json` -- the day's headline numbers
- `data/activities/` -- one file per activity, plus an index
- `data/range/` -- weigh-ins and the other window data, by date
- `data/summaries/latest.json` -- the newest complete day
- `data/summaries/week.json` -- the last seven complete days, rolled up
- `data/manifest.json` -- what is stored and when it was fetched
- `data/state.json` -- the last sync run and any errors it hit

"Complete day" means a day that has ended. Today gets a summary file
like any other day, but the latest pointer and the week rollup only
use finished days. A half-synced morning should not drag a weekly
average.

Raw days are big, hundreds of kilobytes of minute-level time series.
Summaries are about two kilobytes. Read summaries by default. Open a
raw file only for a question the summary does not answer.

## Bulk export

For long-term analysis, render the store as the plain text file this
project started as:

```
.venv/bin/python -m garmin_daily export --days 90 --compact
.venv/bin/python -m garmin_daily export --all --split
```

`--all` backfills the store to your oldest activity first. The
backfill skips days already stored, so an interrupted run resumes on
the next try. `--split` cuts the output under the 500,000-word
per-source limit in Gemini Notebook. The format is plain text with
raw JSON blocks and no code fences, because NotebookLM's indexer
skips fenced content and has known trouble with markdown files.

## Freshness and caching

There is no permanent cache anymore. The old design cached every day
forever, which is right for history and wrong for this week: sleep
gets rescored, stress backfills, a watch syncs a day late. Now:

- Days in a sync window are always refetched.
- Activity detail and days outside the window are kept as stored.
- Range data is refetched for the window and merged by date.
- Every write is atomic, so a killed sync leaves whole files.

## Rate limiting

The tool paces itself. It starts at 0.15 seconds between calls,
backs off when Garmin answers 429, and takes a short breather every
250 calls. Raise the base delay with `--delay 1.0` if you want to
play it safe on a long backfill.

## Development

```
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m py_compile garmin_daily/*.py
.venv/bin/python -m garmin_daily --help
```

The tests run offline. They pin down the Garmin response shapes the
extractor depends on: the sleep score is nested, weights arrive in
grams, and stress durations are in seconds.

## Security

`.gitignore` keeps your data out of version control: `data/`,
`export/`, `.env`, and the token cache. Do not commit any of those.

## License

[Apache 2.0](LICENSE)
