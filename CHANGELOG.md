# Changelog

## v2.0.0 - November 6, 2026

This release replaces the bulk exporter with a daily store. It is a breaking change: the old script, it's command line, and the .NET wrapper are gone.

The old tool wrote one giant text file for NotebookLM (now Gemini Notebook). That was fine for long-term analysis. It was the wrong shape for "how did I sleep last night."

What the tool does now:

- `garmin_daily` is a Python package, not a single script. Sync is the only part that talks to Garmin. Everything else reads a local JSON store and prints one JSON object to stdout.
- One file per day, one per activity, and a small summary per day: steps, sleep and it's score, stress, resting heart rate, HRV, body battery, workouts, and weigh-ins.
- Sync defaults to three days. Today's numbers are still moving, yesterday's sleep is often finalized overnight, and a watch can sync late.
- Reads never touch the network: `show`, `show today`, `show --week`, `activities --days 7`, `weight --days 30`, and `status`. Add `--fetch` to a read to sync it's window first.
- `latest` means the last complete day. Today is left out of the latest summary and the week roll-up on purpose, because it's steps and stress are still moving.
- A systemd user timer runs the sync at 07:15 local. Overnight sleep and HRV are final by then.
- Exit codes mean something now: 0 is clean, 2 is an auth failure, 3 is a partial sync.

The bulk export is still there, as the `export` command, for long-term analysis. The store is the tool. The export is a report the store can print.

Removed:

- `garmin_export.py` and it's cache. The cache never expired, which was fine for a one-off export and wrong for data Garmin rescores overnight.
- The .NET wrapper (`Program.cs`, the project file, and the solution). There are no release zips for v2.0.0. It is a Python tool now: Python 3.12 or later, install with `pip install -r requirements.txt`, and log in once with `python -m garmin_daily login`.

On my data, a one-day sync takes about 8 seconds and a seven-day sync about 26 seconds.
