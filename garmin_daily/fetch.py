"""Fetch from Garmin Connect and write the store.

Sync is the only code path that talks to the network. Everything else
reads what sync put on disk.

Freshness rules:
- Days inside the sync window are always refetched. Today's numbers
  are still moving, yesterday's sleep is often finalized overnight,
  and a watch can sync late. Refetching a few days is cheap.
- Days outside the window are skipped when they are already stored,
  unless --force is passed. That is what makes a long backfill
  resumable: run it again and it picks up where it stopped.
- Range data (weigh-ins, steps, resting heart rate, HRV, VO2 max,
  blood pressure) is refetched for the window on every sync and
  merged by date. Range calls are one call per kind, not per day.
- Activity detail is nine calls per activity, so it is fetched once
  per activity and kept. Set detail=False for a fast list-only pass.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any

from garminconnect import Garmin

from .client import safe_call
from .store import Store, utc_now
from .summarize import (
    normalize_weigh_ins,
    render_day_md,
    render_week_md,
    summarize_activity,
    summarize_day,
    summarize_week,
)

log = logging.getLogger("garmin_daily")

DAY_ENDPOINTS = [
    "summary", "heart_rate", "rhr", "sleep", "stress", "spo2",
    "respiration", "hrv", "body_battery", "bb_events",
    "intensity_min", "events", "lifestyle",
    "training_readiness", "morning_readiness", "hydration", "nutrition",
]


def _fetch_endpoint(api: Garmin, key: str, ds: str):
    if key == "summary":
        return safe_call(api.get_user_summary, ds, label=f"summary_{ds}")
    if key == "heart_rate":
        return safe_call(api.get_heart_rates, ds, label=f"hr_{ds}")
    if key == "rhr":
        return safe_call(api.get_rhr_day, ds, label=f"rhr_{ds}")
    if key == "sleep":
        return safe_call(api.get_sleep_data, ds, label=f"sleep_{ds}")
    if key == "stress":
        return safe_call(api.get_all_day_stress, ds, label=f"stress_{ds}")
    if key == "spo2":
        return safe_call(api.get_spo2_data, ds, label=f"spo2_{ds}")
    if key == "respiration":
        return safe_call(api.get_respiration_data, ds, label=f"resp_{ds}")
    if key == "hrv":
        return safe_call(api.get_hrv_data, ds, label=f"hrv_{ds}")
    if key == "body_battery":
        return safe_call(api.get_body_battery, ds, ds, label=f"bb_{ds}")
    if key == "bb_events":
        return safe_call(api.get_body_battery_events, ds, label=f"bbe_{ds}")
    if key == "intensity_min":
        return safe_call(api.get_intensity_minutes_data, ds, label=f"im_{ds}")
    if key == "events":
        return safe_call(api.get_all_day_events, ds, label=f"events_{ds}")
    if key == "lifestyle":
        return safe_call(api.get_lifestyle_logging_data, ds, label=f"ll_{ds}")
    if key == "training_readiness":
        return safe_call(api.get_training_readiness, ds, label=f"tr_{ds}")
    if key == "morning_readiness":
        return safe_call(api.get_morning_training_readiness, ds, label=f"mtr_{ds}")
    if key == "hydration":
        return safe_call(api.get_hydration_data, ds, label=f"hydration_{ds}")
    if key == "nutrition":
        return {
            "food_log": safe_call(api.get_nutrition_daily_food_log, ds, label=f"food_{ds}"),
            "meals": safe_call(api.get_nutrition_daily_meals, ds, label=f"meals_{ds}"),
            "settings": safe_call(api.get_nutrition_daily_settings, ds, label=f"nutr_set_{ds}"),
        }
    return None


def fetch_day(api: Garmin, day: date) -> dict:
    ds = day.isoformat()
    raw: dict[str, Any] = {"date": ds, "fetched_at": utc_now()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_endpoint, api, key, ds): key for key in DAY_ENDPOINTS}
        for future in as_completed(futures):
            key = futures[future]
            try:
                raw[key] = future.result()
            except Exception:
                raw[key] = None
    return raw


def _chunked(fn, start: date, end: date, label: str, chunk_days: int = 365):
    """Some Garmin range endpoints reject spans over about a year with a
    400. Call in yearly chunks and merge the list-shaped results."""
    results = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        result = safe_call(fn, cur.isoformat(), chunk_end.isoformat(), label=f"{label}_{cur}")
        if isinstance(result, list):
            results.extend(result)
        elif result is not None:
            results.append(result)
        cur = chunk_end + timedelta(days=1)
    return results or None


def fetch_range(api: Garmin, store: Store, start: date, end: date) -> dict:
    """One call per kind for the whole window. Merged into the store by
    date, so a three-day sync also repairs the surrounding week."""
    start_s, end_s = start.isoformat(), end.isoformat()
    fetched = {}

    weigh_raw = safe_call(api.get_weigh_ins, start_s, end_s, label="weigh_ins")
    if weigh_raw is not None:
        by_date = normalize_weigh_ins(weigh_raw)
        store.put_range("weigh_ins", by_date)
        fetched["weigh_ins"] = sum(len(v) for v in by_date.values())

    body_comp = safe_call(api.get_body_composition, start_s, end_s, label="body_comp")
    if body_comp is not None:
        by_date = {}
        entries = body_comp.get("dateWeightList") if isinstance(body_comp, dict) else None
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("calendarDate"):
                by_date[entry["calendarDate"]] = entry
        if by_date:
            store.put_range("body_composition", by_date)
        fetched["body_composition"] = len(by_date)

    steps = safe_call(api.get_daily_steps, start_s, end_s, label="daily_steps")
    if isinstance(steps, list):
        by_date = {row["calendarDate"]: row for row in steps
                   if isinstance(row, dict) and row.get("calendarDate")}
        store.put_range("steps", by_date)
        fetched["steps"] = len(by_date)

    rhr = safe_call(api.get_rhr_daily, start_s, end_s, label="rhr_daily")
    if isinstance(rhr, list):
        by_date = {row["calendarDate"]: row for row in rhr
                   if isinstance(row, dict) and row.get("calendarDate")}
        store.put_range("resting_heart_rate", by_date)
        fetched["resting_heart_rate"] = len(by_date)

    hrv = safe_call(api.get_hrv_data_range, start_s, end_s, label="hrv_range")
    if isinstance(hrv, dict) and isinstance(hrv.get("hrvSummaries"), list):
        by_date = {row["calendarDate"]: row for row in hrv["hrvSummaries"]
                   if isinstance(row, dict) and row.get("calendarDate")}
        store.put_range("hrv", by_date)
        fetched["hrv"] = len(by_date)

    max_metrics = safe_call(api.get_max_metrics_range, start_s, end_s, label="max_metrics")
    if isinstance(max_metrics, list):
        by_date = {}
        for row in max_metrics:
            generic = row.get("generic") if isinstance(row, dict) else None
            if isinstance(generic, dict) and generic.get("calendarDate"):
                by_date[generic["calendarDate"]] = row
        store.put_range("max_metrics", by_date)
        fetched["max_metrics"] = len(by_date)

    bp = safe_call(api.get_blood_pressure, start_s, end_s, label="blood_pressure")
    if bp is not None:
        store.put_range("blood_pressure", {f"{start_s}_{end_s}": bp})
        fetched["blood_pressure"] = 1

    return fetched


def fetch_activities(api: Garmin, store: Store, start: date, end: date,
                     detail: bool = True) -> list[dict]:
    activities = safe_call(api.get_activities_by_date, start.isoformat(),
                           end.isoformat(), None, label="activities_by_date") or []
    if not isinstance(activities, list):
        activities = []
    log.info("%d activities in window", len(activities))

    for act in activities:
        aid = act.get("activityId")
        if aid is None:
            continue
        existing = store.get_activity(aid) or {}
        data = {"summary": act}
        if detail and not existing.get("detail"):
            data["detail"] = safe_call(api.get_activity, aid, label=f"act_{aid}")
            data["splits"] = safe_call(api.get_activity_splits, aid, label=f"splits_{aid}")
            data["split_summaries"] = safe_call(api.get_activity_split_summaries, aid, label=f"ss_{aid}")
            data["typed_splits"] = safe_call(api.get_activity_typed_splits, aid, label=f"ts_{aid}")
            data["weather"] = safe_call(api.get_activity_weather, aid, label=f"wx_{aid}")
            data["hr_zones"] = safe_call(api.get_activity_hr_in_timezones, aid, label=f"hrz_{aid}")
            data["power_zones"] = safe_call(api.get_activity_power_in_timezones, aid, label=f"pwz_{aid}")
            data["exercise_sets"] = safe_call(api.get_activity_exercise_sets, aid, label=f"sets_{aid}")
            data["details"] = safe_call(api.get_activity_details, aid, label=f"details_{aid}")
        else:
            for key in ("detail", "splits", "split_summaries", "typed_splits", "weather",
                        "hr_zones", "power_zones", "exercise_sets", "details"):
                if existing.get(key) is not None:
                    data[key] = existing[key]
        store.put_activity(aid, data)
    rebuild_activity_index(store)
    return activities


def rebuild_activity_index(store: Store) -> list[dict]:
    index = []
    if store.activities_dir.exists():
        for path in store.activities_dir.glob("*.json"):
            if path.name == "index.json":
                continue
            data = store.get_activity(path.stem)
            summary = (data or {}).get("summary")
            if isinstance(summary, dict):
                index.append(summarize_activity(summary))
    index.sort(key=lambda a: a.get("start_local") or "", reverse=True)
    store.put_activity_index(index)
    return index


def rebuild_summaries(store: Store, days: list[date]) -> list[dict]:
    """Rebuild summary files for the given days from what is on disk, then
    refresh the latest-day pointer, the week rollup, and the manifest.
    Runs after every sync and can also run on its own, offline."""
    index = store.activity_index()
    # The index holds extracted summaries already. For summarize_day we
    # need the raw activity summaries, so rebuild them from the files.
    raw_by_date: dict[str, list[dict]] = {}
    if store.activities_dir.exists():
        for path in store.activities_dir.glob("*.json"):
            if path.name == "index.json":
                continue
            data = store.get_activity(path.stem)
            summary = (data or {}).get("summary")
            if isinstance(summary, dict):
                start = (summary.get("startTimeLocal") or "")[:10]
                if start:
                    raw_by_date.setdefault(start, []).append(summary)

    weigh_ins = store.get_range("weigh_ins")
    built = []
    for day in days:
        raw = store.get_day(day)
        if raw is None:
            continue
        summary = summarize_day(day, raw, raw_by_date.get(day.isoformat(), []),
                                weigh_ins.get(day.isoformat(), []))
        store.put_summary(day, summary)
        store.put_summary_md(day, render_day_md(summary))
        built.append(summary)

    all_days = store.stored_days()
    complete = []
    for day in all_days:
        s = store.get_summary(day)
        if s and s.get("complete"):
            complete.append(s)
    # Today does not count as complete, however much of it has synced.
    # Its sleep is final but its steps and stress are still moving.
    # The latest pointer and the week rollup use full days only, which
    # in practice means they end yesterday. Today's own summary file
    # is still written above and stays readable on its own.
    from datetime import date as _date
    full_days = [s for s in complete if s["date"] < _date.today().isoformat()]
    rollup_days = full_days or complete
    if rollup_days:
        latest = rollup_days[-1]
        store.put_latest(latest, render_day_md(latest))
        week_days = rollup_days[-7:]
        rollup = summarize_week(week_days, weigh_ins)
        store.put_week(rollup, render_week_md(rollup))

    manifest = {
        "updated_at": utc_now(),
        "days_stored": [d.isoformat() for d in all_days],
        "first_day": all_days[0].isoformat() if all_days else None,
        "last_day": all_days[-1].isoformat() if all_days else None,
        "activities_stored": len(index),
        "latest_complete_day": rollup_days[-1]["date"] if rollup_days else None,
    }
    store.put_manifest(manifest)
    return built


def sync(api: Garmin, store: Store, start: date, end: date,
         force: bool = False, detail: bool = True) -> dict:
    """Fetch a date window into the store and rebuild its summaries."""
    if end < start:
        start, end = end, start
    window_days = []
    cur = start
    while cur <= end:
        window_days.append(cur)
        cur += timedelta(days=1)

    log.info("Sync %s to %s (%d days)", start, end, len(window_days))
    # Every day in the window is refetched, force or not. See the
    # freshness rules at the top of this file. The force flag is kept
    # for callers that want to make that explicit.
    _ = force
    fetched_days, errors = [], []
    for day in window_days:
        try:
            raw = fetch_day(api, day)
            store.put_day(day, raw)
            fetched_days.append(day)
        except Exception as exc:
            errors.append(f"{day.isoformat()}: {exc}")
            log.error("Fetch failed for %s: %s", day, exc)

    range_counts = {}
    try:
        range_counts = fetch_range(api, store, start, end)
    except Exception as exc:
        errors.append(f"range: {exc}")

    activities = []
    try:
        activities = fetch_activities(api, store, start, end, detail=detail)
    except Exception as exc:
        errors.append(f"activities: {exc}")

    built = rebuild_summaries(store, window_days)
    state = {
        "ran_at": utc_now(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "days_fetched": [d.isoformat() for d in fetched_days],
        "activities_found": len(activities),
        "range_counts": range_counts,
        "summaries_built": [s["date"] for s in built],
        "errors": errors,
    }
    store.put_state(state)
    return state


def backfill(api: Garmin, store: Store, start: date, end: date,
             detail: bool = True) -> dict:
    """Long-window fetch for the bulk export. Days already stored are
    skipped, so an interrupted backfill resumes on the next run. Range
    and section data are fetched in yearly chunks by the caller."""
    missing = []
    cur = start
    while cur <= end:
        if not store.has_day(cur):
            missing.append(cur)
        cur += timedelta(days=1)
    log.info("Backfill %s to %s: %d of %d days missing", start, end,
             len(missing), (end - start).days + 1)
    errors = []
    for day in missing:
        try:
            store.put_day(day, fetch_day(api, day))
        except Exception as exc:
            errors.append(f"{day.isoformat()}: {exc}")
    try:
        fetch_range(api, store, start, end)
        fetch_activities(api, store, start, end, detail=detail)
    except Exception as exc:
        errors.append(str(exc))
    rebuild_summaries(store, [start + timedelta(days=i) for i in range((end - start).days + 1)])
    state = {"ran_at": utc_now(), "backfill": {"start": start.isoformat(), "end": end.isoformat()},
             "days_fetched": [d.isoformat() for d in missing], "errors": errors}
    store.put_state(state)
    return state
