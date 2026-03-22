# AGENTS.md -- AI context for garmin-data-export

This file is for AI agents working on or consuming output from this project.
For human setup instructions, see [README.md](README.md).

## What this project does

A single Python script (`garmin_export.py`) downloads all available health
and fitness data from a user's Garmin Connect account and writes it as one
plain text file with raw JSON data blocks. The output is designed for LLM
consumption -- every API response is dumped as complete, unfiltered JSON.
No markdown formatting is used; plain text headers separate sections.

This format was specifically chosen for compatibility with NotebookLM and
other LLM tools. Research found that .md files have known parsing bugs in
NotebookLM's RAG indexer, and content inside code fences (```json) gets
skipped. Plain .txt with raw JSON avoids both issues.

No official Garmin API key exists for personal use. The script authenticates
through Garmin's SSO (same flow as the website) via the
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
library, which wraps [garth](https://github.com/matin/garth) for OAuth.

## Output file structure

Each export produces a single file: `export/garmin_export_YYYY-MM-DD_HHMMSS.txt`

The output uses plain text section headers and raw JSON (no markdown headings,
no code fences, no bold/italic). This is intentional -- NotebookLM's RAG
indexer has bugs with .md files and skips content inside code fences.

```
Garmin Connect Data Export            -- title + metadata (date range, export time)

Table of Contents                     -- numbered list with section names and descriptions;
                                         notes that all data is raw JSON
Profile                               -- section-level cache (fetched once, cached forever)
Daily Health                          -- per-day: 13 endpoints fetched concurrently (4 threads)
  2026-03-22                          -- one per day, newest first
    Steps / Heart Rate / Sleep        -- each sub-section has a plain text title + raw JSON
Activities                            -- per-activity cache; list then detail per item
  Activity {id}                       -- summary, splits, zones, weather, time-series
Body Composition                      -- chunked yearly API calls, section cache
Training Metrics                      -- VO2max, FTP, hill/endurance scores, etc.
Goals and Records                     -- PRs, badges, goals
Trends                                -- weekly aggregates, daily steps, floors, progress
Golf                                  -- list then scorecard + shots per round
Gear                                  -- needs userProfileNumber; list then stats per item
Training Plans                        -- list then detail per plan
Workouts                              -- list then detail per workout
Hydration                             -- per-day, fetched concurrently (4 threads)
Nutrition                             -- per-day (3 calls/day), fetched concurrently
Women's Health                        -- chunked date range + pregnancy summary

Errors During Export                  -- only if any sections failed
```

Empty sections contain: "No data available."

## Architecture

### Single file, three classes

| Class | Purpose |
|-------|---------|
| `RateLimiter` | Thread-safe adaptive pacer. Starts at 0.15s delay, doubles on 429, decays on success. Forced 2s pause every 250 calls. |
| `ExportCache` | Persistent JSON file cache under `export/.cache/`. Three namespaces: `daily/` (per-day), `activities/` (per-activity), `sections/` (whole-section blobs). Never invalidates -- only `--no-cache` or deleting `.cache/` forces re-fetch. |
| `GarminExporter` | Orchestrator. Authenticates, iterates the sections list, writes plain text output, handles Ctrl-C gracefully (saves partial export). |

### Concurrency model

- Daily health: `ThreadPoolExecutor(max_workers=4)` -- 13 endpoints per day fetched in parallel
- Hydration: 4 days fetched concurrently (1 API call per day)
- Nutrition: 4 days concurrently (3 API calls per day)
- Activities: sequential (each activity has ~8 detail calls)
- `RateLimiter.wait()` uses a `threading.Lock` so concurrent threads are still paced

### Caching strategy

Cache is **permanent**. Once a day/activity/section is cached, it is never
re-fetched unless the user passes `--no-cache` or deletes the cache directory.

| Cache type | Key | Location |
|------------|-----|----------|
| Daily health | `YYYY-MM-DD` | `export/.cache/daily/YYYY-MM-DD.json` |
| Hydration | `hydration_YYYY-MM-DD` | `export/.cache/daily/hydration_YYYY-MM-DD.json` |
| Nutrition | `nutrition_YYYY-MM-DD` | `export/.cache/daily/nutrition_YYYY-MM-DD.json` |
| Activity | `{activityId}` | `export/.cache/activities/{activityId}.json` |
| Section | `{name}` | `export/.cache/sections/{name}.json` |

On re-run, only uncached items are fetched. This makes interrupted `--all`
exports fully resumable -- just run the same command again.

### Chunked date-range calls

Several Garmin endpoints reject date ranges longer than about one year with
HTTP 400. The `_chunked_date_call()` helper breaks ranges into 365-day
segments, calls each, and merges the list results. Used for: endurance score,
running tolerance, weekly intensity minutes, hill score, body composition,
weigh-ins, and menstrual calendar.

### Rate limiting details

| Parameter | Value |
|-----------|-------|
| Base delay | 0.15s (configurable via `--delay`) |
| On HTTP 429 | Double delay (max 10s), wait 60s, retry once |
| On success streak (10+) | Gradually reduce delay toward base |
| Every 250 calls | Forced 2s pause |
| On general error | 1.2x delay bump |

### Authentication

- `garth` library handles Garmin SSO OAuth
- Tokens cached in `~/.garminconnect/` (about 1 year lifetime)
- Supports `.env` file or `GARMIN_EMAIL`/`GARMIN_PASSWORD` env vars
- `--login` flag to authenticate without exporting
- Friendly error messages for common failures (401, 403, 429, network errors)

### Compact mode (`--compact`)

Reduces output file size from ~170 MB to roughly 10-20 MB for LLM upload.
Applied at write time only -- the cache always stores full data.

What it does:
- `_strip_empty()` recursively removes None, empty strings, empty lists/dicts
- `_json()` uses `indent=None` (single-line) instead of `indent=2`
- `_compact_daily()` downsamples high-frequency daily time-series (heart rate,
  stress, sleep, respiration, HRV, body battery) to ~24 hourly data points
- Activity time-series (`details` key) is omitted entirely; summaries, splits,
  and zones are kept
- Each section becomes a single JSON block with a schema description
- Output filename gets a `_compact` suffix

### Split mode (`--split`)

Splits output into multiple files for NotebookLM's 500K word limit.
Implies `--compact`. Applied at write time after the full export is built.

What it does:
- `_SPLIT_WORD_LIMIT = 480000` (safety margin under 500K)
- Splits on section boundaries; oversized sections split by JSON keys/items
- Each file gets its own header with a list of sections it contains
- Output filenames: `..._compact_part1of6.txt`, `..._compact_part2of6.txt`, etc.

### NotebookLM compatibility notes

Based on extensive research (Reddit, official docs, community reports):
- Official supported formats: txt, md, csv, pdf, docx, pptx, Google Docs/Sheets
- Limits: 500K words per source, 200MB file size, 50 sources per notebook
- Known bug: .md files have parsing issues in NotebookLM's RAG indexer
- Content inside code fences (```json) gets skipped by the indexer
- Plain .txt is fastest and most reliable for the AI to process
- NotebookLM uses RAG (chunks documents, retrieves per query) -- it does NOT
  read entire files at once

## Lessons learned

1. **No official Garmin API for personal use.** The `python-garminconnect`
   library reverse-engineers the web API. Endpoints can change without notice.

2. **Rate limits are real but undocumented.** HTTP 429 responses start after
   sustained bursts. The adaptive rate limiter with exponential backoff handles
   this well. Starting conservative (0.15s) and ramping down is safer than
   starting fast.

3. **Some endpoints have a roughly one-year date range limit.** They return
   400 (not 429) for longer ranges. Chunking into yearly segments solved this.
   The body battery range endpoint (`/bodyBattery/reports/daily`) returns 400
   for any date span and was removed entirely -- per-day data covers it.

4. **Cache everything, invalidate nothing.** Historical health data does not
   change. Permanent caching makes multi-hour `--all` exports fully resumable
   and makes re-exports near-instant. The only cost is disk space (about 1-2 KB
   per cached day).

5. **Concurrent fetching needs thread-safe rate limiting.** Four threads hit
   the API simultaneously, but all go through one `RateLimiter` with a lock.
   This gives roughly 3-4x speedup while still respecting pacing.

6. **Graceful Ctrl-C matters for long exports.** The script catches
   `KeyboardInterrupt` per-section, saves whatever was exported so far, and
   notes the interruption. Cache is already persisted per-item, so nothing
   is lost.

7. **Garmin endpoints vary in structure.** Some are list+detail (activities,
   golf, gear, workouts), some are per-day (daily health, hydration,
   nutrition), some are single-call (profile, goals), and some need chunked
   date ranges (trends, training metrics, body comp). Each pattern needs
   its own caching strategy.

8. **Not all accounts have all data.** Golf, women's health, nutrition, and
   hydration may return empty or 404. The `safe_call()` wrapper catches all
   errors and returns `None`. Sections with no data get a "No data available"
   note instead of crashing.

9. **Output size can be large.** A 3-year `--all` export produces around
   170 MB of text. This is fine for LLMs with large context windows or
   RAG systems that chunk by section headers.

10. **The Table of Contents is critical for AI parsing.** It tells the model
    what sections exist, what each one contains, and that all data is raw JSON.
    Without it, models may struggle to navigate a 170 MB file.

11. **Plain text beats markdown for LLM tools.** NotebookLM has known bugs
    with .md files and skips content inside code fences. Reddit users
    universally recommend .txt for best results.

## Dependencies

Only two PyPI packages (see `requirements.txt`):

- `garminconnect` -- Garmin Connect API wrapper
- `garth` -- OAuth/SSO authentication library (pulled in by garminconnect)

Python 3.7+ required (uses f-strings, `typing.Optional`, `pathlib`, etc.).

## Files

| File | Purpose |
|------|---------|
| `garmin_export.py` | The entire tool -- single file, no internal packages |
| `requirements.txt` | `garminconnect` and `garth` |
| `README.md` | Human-facing setup, usage, and options reference |
| `AGENTS.md` | This file -- AI-facing architecture and context |
| `.gitignore` | Blocks `export/`, `.env`, `.garminconnect/`, Python artifacts |
| `LICENSE` | Apache 2.0 |
