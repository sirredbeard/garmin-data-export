# Garmin Connect Data Export

Pulls your health and fitness data from Garmin Connect and saves it as
a plain text file with complete JSON data blocks. Designed for uploading
to LLM tools like NotebookLM, ChatGPT, and Claude. Also useful for
local backup or having your own numbers outside the Garmin app.

No Garmin developer API key required. Under the hood this uses the
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
library, which logs in through the same SSO flow as the Garmin website.

## What it exports

Everything. Every API response is dumped as complete JSON, nothing
gets filtered or summarized, so you get every field Garmin returns
including dates, timestamps, and metadata. If a category has no data
for your account, it says so instead of silently skipping it.

- **Profile**: user info, settings, devices, alarms, activity types
- **Daily health**: steps, heart rate, sleep, stress, body battery,
  SpO2, HRV, respiration, intensity minutes, all-day events, lifestyle
  logging. One section per day.
- **Activities**: every activity with full summary, splits, HR/power
  zones, exercise sets, weather, and complete time-series data.
- **Body composition**: weight, BMI, body fat, muscle/bone mass,
  weigh-ins (chunked yearly for long histories).
- **Training metrics**: VO2 max, fitness age, training readiness,
  lactate threshold, cycling FTP, hill/endurance scores, running
  tolerance, race predictions.
- **Goals and records**: personal records, earned badges, active and
  past goals.
- **Trends**: weekly aggregates (steps, stress, intensity minutes),
  daily steps, floors, progress summaries, body battery trend.
- **Golf**: scorecards, shot data per round.
- **Gear**: equipment list, stats per item, activity type defaults.
- **Training plans**: active and past plans with full details.
- **Workouts**: saved workouts with full structure.
- **Hydration**: daily fluid intake (per day, cached).
- **Nutrition**: food logs, meals, nutrition settings (per day, cached).
- **Women's health**: menstrual calendar, pregnancy summary.

## Setup

```
pip install garminconnect garth
```

## First run

You need to log in once. After that, tokens are cached locally for about
a year and you won't be asked again.

```
python garmin_export.py --login
```

It will ask for your Garmin email and password (sent directly to Garmin's
servers, not stored by this tool). If you have MFA enabled it will prompt
for the code too.

If you'd rather not type credentials every time you set up a new machine,
drop them in a `.env` file next to the script:

```
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=your-password
```

Or use environment variables (`GARMIN_EMAIL`, `GARMIN_PASSWORD`).

## Usage

```
# defaults: last 30 days of health data, up to 100 activities
python garmin_export.py

# everything, goes back to your very first Garmin activity
python garmin_export.py --all

# resume an interrupted export (cache picks up where it stopped)
python garmin_export.py --all

# force a clean re-fetch
python garmin_export.py --all --no-cache

# full year, more activities
python garmin_export.py --days 365 --activities 500

# go slower if you're worried about rate limits
python garmin_export.py --days 365 --delay 1.0

# split into multiple files for LLM tools with word count limits
python garmin_export.py --all --split

# incremental update: only fetch data since last export
python garmin_export.py --update
```

### Recommended workflow

Do a full initial export once:

```
python garmin_export.py --all --split
```

Then use `--update` for incremental data going forward:

```
python garmin_export.py --update
```

Output goes into `export/` as a single timestamped plain text file, like
`garmin_export_2026-03-22_162534.txt`.

## Output format

Everything lives in one `.txt` file. The structure uses plain text section
headers with raw JSON data blocks (no markdown formatting, no code fences).
This format was chosen because NotebookLM and other LLM tools have known
issues parsing markdown files. Plain text with raw JSON works best for
RAG indexing. By default, nothing is filtered, truncated, or summarized.

## Compact mode

A full `--all` export can be 150+ MB, which is too large for some LLM
tools (NotebookLM, ChatGPT file upload, etc.). Use `--compact` to reduce
the file size significantly:

```
python garmin_export.py --all --compact
```

What compact mode does:

- Strips null, empty, and zero-value fields from all JSON
- Uses single-line JSON instead of pretty-printed (saves whitespace)
- Drops activity time-series data (second-by-second sensor readings);
  summaries, splits, and zones are still included
- Downsamples daily high-frequency data (heart rate, stress, sleep,
  respiration) from per-minute readings to hourly averages
- Each section becomes a single JSON block with a schema description

The same cache is used for both modes. Compact mode just writes less data
to the output file; it does not affect what gets cached or fetched.

## Split mode (for LLM tools)

Many LLM tools have per-source word count limits. NotebookLM caps at
500,000 words per source, and other tools have similar restrictions.
Use `--split` (implies `--compact`) to split the export into multiple
files, each under the limit:

```
python garmin_export.py --all --split
```

This produces files like `garmin_export_..._part1of6.txt`. Each file
includes its own header listing which sections it contains. Upload all
files as separate sources.

NotebookLM supports up to 50 sources per notebook, so even a multi-year
export with 6-8 files fits easily. Other LLM tools that accept multiple
file uploads benefit from the same approach. Smaller files stay within
per-file token limits while preserving all data.

## Update mode (incremental exports)

After doing a full `--all --split` export, use `--update` to fetch only
the data that has arrived since the last export:

```
python garmin_export.py --update
```

The tool finds the most recent export file(s) in the output directory,
parses the end date, and sets the start date to one day before that (to
catch any late-arriving data). It implies `--compact` and only includes
per-day and per-activity sections: Daily Health, Activities, Hydration,
and Nutrition. Sections that don't change (Profile, Body Composition,
Training Metrics, Goals, Trends, Golf, Gear, Training Plans, Workouts,
Women's Health) are skipped since they're already in the base export.

Output filename: `garmin_export_YYYY-MM-DD_HHMMSS_compact_update.txt`

If no previous export is found, it falls back to the `--days` default
(30 days).

## .NET project structure

This is a .NET solution that wraps a Python export script. The Garmin
Connect library (`python-garminconnect`) is Python-only, so the actual
data fetching runs in Python. The .NET project provides the solution
structure, build integration, and can be extended with C# tooling.

To build and run:

```
dotnet build
dotnet run
```

This invokes the Python script with default arguments. Pass additional
flags after `--`:

```
dotnet run -- --all --split
```

Requires Python 3.7+ with dependencies installed (`pip install -r requirements.txt`).

## Options

| Flag | Default | What it does |
|------|---------|--------------|
| `--all` | | Export complete history (auto-detects start date) |
| `--days N` | 30 | How many days of daily health data to pull |
| `--activities N` | 100 | Max number of activities to export |
| `--output DIR` | ./export | Where to write the file |
| `--delay SEC` | 0.15 | Base delay between API calls (seconds) |
| `--no-cache` | | Re-fetch everything, ignore cached data |
| `--compact` | | Smaller output for LLM upload (see above) |
| `--split` | | Split output into multiple files under 480K words each |
| `--update` | | Export only new data since the last export (implies `--compact`) |
| `--login` | | Just log in and cache tokens, then exit |
| `--tokenstore DIR` | ~/.garminconnect | Where to store auth tokens |
| `--verbose` | | Show debug output |

When `--all` is used, `--days` and `--activities` are ignored. The tool
finds your oldest activity and works backward from there.

## Caching

Large exports (especially `--all` on multi-year accounts) can take a
while. If the export gets interrupted, just run the same command again.
The tool caches each day's health data, each activity, and each section
as it goes. On re-run it only fetches what's not already in the cache.

Cache is permanent, it never invalidates or expires. Use `--no-cache`
to force a full re-fetch from scratch, or just delete `export/.cache/`.

## Rate limiting

The tool paces itself automatically. It starts at 0.15 seconds between
calls, backs off if Garmin pushes back (429), and ramps back up when
things calm down. Every 250 calls it takes a short breather. For big
exports the delay adjusts on its own, but you can bump `--delay` to 1.0
or higher if you want to play it safe.

## Security

The `.gitignore` keeps your data out of version control:
- `export/` (your health data)
- `.env` (credentials)
- `.garminconnect/` (auth tokens)

Don't commit any of those.

## License

[Apache 2.0](LICENSE).