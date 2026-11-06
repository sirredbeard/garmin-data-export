"""Bulk export for long-term analysis.

The daily store is the source of truth. This module renders it as one
plain text file with raw JSON blocks, the format that was built for
uploading to NotebookLM, now Gemini Notebook.

Why plain text: NotebookLM's indexer has known trouble with markdown
files and skips content inside code fences. Plain text headers with
raw JSON avoid both problems. The format is kept for that one job.
Daily use does not go through this file at all.

With --all, the export first backfills the store back to the oldest
activity on the account, then renders. The backfill is resumable.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from garminconnect import Garmin

from .client import safe_call
from .fetch import backfill
from .store import Store

log = logging.getLogger("garmin_daily")

SPLIT_WORD_LIMIT = 480_000
SECTION_ORDER = [
    "Profile", "Daily Health", "Activities", "Body Composition",
    "Training Metrics", "Goals and Records", "Trends", "Golf", "Gear",
    "Training Plans", "Workouts", "Hydration", "Nutrition", "Women's Health",
]


def _strip_empty(data: Any) -> Any:
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            cleaned = _strip_empty(value)
            if cleaned is not None and cleaned != "" and cleaned != [] and cleaned != {} and cleaned != 0:
                out[key] = cleaned
        return out
    if isinstance(data, list):
        return [c for c in (_strip_empty(v) for v in data) if c is not None and c != ""]
    return data


def _json(data: Any, compact: bool) -> str:
    if compact:
        return json.dumps(_strip_empty(data), default=str, ensure_ascii=False)
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _word_count(text: str) -> int:
    return len(text.split())


def detect_start_date(api: Garmin, today: date) -> date:
    """Oldest activity on the account, padded a week. Falls back to five
    years when the account answers nothing usable."""
    oldest = safe_call(api.get_activities_by_date, "2000-01-01", today.isoformat(),
                       None, "asc", label="oldest_activity")
    if isinstance(oldest, list) and oldest:
        start_str = (oldest[0].get("startTimeLocal") or "")[:10]
        try:
            return date.fromisoformat(start_str) - timedelta(days=7)
        except ValueError:
            pass
    return today - timedelta(days=365 * 5)


def fetch_sections(api: Garmin, store: Store, start: date, end: date) -> None:
    """Slow-changing sections, stored once and refreshed on each export."""
    profile: dict[str, Any] = {}
    profile["full_name"] = safe_call(api.get_full_name, label="full_name")
    profile["unit_system"] = safe_call(api.get_unit_system, label="unit_system")
    profile["user_profile"] = safe_call(api.get_user_profile, label="user_profile")
    profile["profile_settings"] = safe_call(api.get_userprofile_settings, label="profile_settings")
    profile["devices"] = safe_call(api.get_devices, label="devices")
    profile["primary_device"] = safe_call(api.get_primary_training_device, label="primary_device")
    profile["device_alarms"] = safe_call(api.get_device_alarms, label="device_alarms")
    profile["last_used_device"] = safe_call(api.get_device_last_used, label="last_used_device")
    profile["activity_types"] = safe_call(api.get_activity_types, label="activity_types")
    store.put_section("profile", profile)

    today_s = end.isoformat()
    training: dict[str, Any] = {}
    training["training_status"] = safe_call(api.get_training_status, today_s, label="training_status")
    training["max_metrics"] = safe_call(api.get_max_metrics, today_s, label="max_metrics")
    training["fitness_age"] = safe_call(api.get_fitnessage_data, today_s, label="fitness_age")
    training["lactate_threshold"] = safe_call(api.get_lactate_threshold, label="lactate")
    training["cycling_ftp"] = safe_call(api.get_cycling_ftp, label="ftp")
    training["race_predictions"] = safe_call(api.get_race_predictions, label="race_predictions")
    training["endurance_score"] = store.get_range("max_metrics")
    store.put_section("training", training)

    goals: dict[str, Any] = {}
    goals["personal_records"] = safe_call(api.get_personal_record, label="prs")
    goals["badges"] = safe_call(api.get_earned_badges, label="badges")
    # Upstream made goal pagination one-based in 0.3.17. start=0 raises.
    goals["active_goals"] = safe_call(api.get_goals, "active", 1, 100, label="active_goals")
    goals["past_goals"] = safe_call(api.get_goals, "past", 1, 100, label="past_goals")
    store.put_section("goals", goals)

    trends: dict[str, Any] = {}
    trends["weekly_steps"] = safe_call(api.get_weekly_steps, today_s, 52, label="weekly_steps")
    trends["weekly_stress"] = safe_call(api.get_weekly_stress, today_s, 52, label="weekly_stress")
    trends["daily_steps"] = store.get_range("steps")
    store.put_section("trends", trends)

    golf: dict[str, Any] = {"summary": safe_call(api.get_golf_summary, label="golf")}
    store.put_section("golf", golf)

    gear: dict[str, Any] = {}
    prof = profile.get("user_profile") or {}
    profile_num = str(prof.get("profileNumber") or prof.get("userProfileNumber") or "")
    if profile_num:
        gear["gear_list"] = safe_call(api.get_gear, profile_num, label="gear")
        gear["gear_defaults"] = safe_call(api.get_gear_defaults, profile_num, label="gear_defaults")
    store.put_section("gear", gear)

    plans = safe_call(api.get_training_plans, label="training_plans")
    store.put_section("training_plans", {"plans": plans})

    workouts = safe_call(api.get_workouts, 0, 1000, label="workouts")
    store.put_section("workouts", {"workout_list": workouts})

    womens: dict[str, Any] = {}
    womens["pregnancy"] = safe_call(api.get_pregnancy_summary, label="pregnancy")
    store.put_section("womens_health", womens)


def render(store: Store, start: date, end: date, compact: bool) -> str:
    now = datetime.now()
    lines = [
        "Garmin Connect Data Export",
        "",
        f"Exported: {now.isoformat()}",
        f"Date range: {start.isoformat()} to {end.isoformat()} ({(end - start).days} days)",
        "Format: " + ("compact (empty fields stripped, single-line JSON)" if compact else "full (complete JSON, all fields)"),
        "",
        "Table of Contents",
        "",
        "This file contains an export of Garmin Connect health and fitness data.",
        "All data is raw JSON from the Garmin Connect API.",
        "Sections with no data contain a note: No data available.",
        "",
    ]
    for i, name in enumerate(SECTION_ORDER, 1):
        lines.append(f"  {i}. {name}")
    lines.append("")

    def section(title: str, data: Any) -> None:
        lines.append(f"\n{title}\n")
        if data is None or data == {} or data == []:
            lines.append("No data available.\n")
        else:
            lines.append(_json(data, compact) + "\n")

    profile = store.get_section("profile")
    section("Profile", profile.get("data") if profile else None)

    daily: dict[str, Any] = {}
    cur = start
    while cur <= end:
        raw = store.get_day(cur)
        if raw is not None:
            daily[cur.isoformat()] = {k: v for k, v in raw.items() if k not in ("date", "fetched_at")}
        cur += timedelta(days=1)
    section("Daily Health", daily or None)

    acts = []
    for entry in store.activity_index():
        full = store.get_activity(entry.get("id"))
        if full:
            acts.append(full)
    section("Activities", acts or None)

    section("Body Composition", {
        "weigh_ins": store.get_range("weigh_ins"),
        "body_composition": store.get_range("body_composition"),
    })

    training = store.get_section("training")
    section("Training Metrics", training.get("data") if training else None)

    goals = store.get_section("goals")
    section("Goals and Records", goals.get("data") if goals else None)

    trends = store.get_section("trends")
    section("Trends", trends.get("data") if trends else None)

    for title, key in (("Golf", "golf"), ("Gear", "gear"),
                       ("Training Plans", "training_plans"), ("Workouts", "workouts"),
                       ("Women's Health", "womens_health")):
        stored = store.get_section(key)
        section(title, stored.get("data") if stored else None)

    # Hydration and nutrition are fetched per day and stored inside the
    # daily raw data, under the hydration and nutrition keys. They are
    # part of Daily Health above, not separate sections in this design.
    section("Hydration", "Per-day hydration is inside Daily Health, under the hydration key for each date.")
    section("Nutrition", "Per-day nutrition is inside Daily Health, under the nutrition key for each date.")
    return "\n".join(lines)


def write_export(text: str, out_dir: Path, split: bool) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if not split or _word_count(text) <= SPLIT_WORD_LIMIT:
        path = out_dir / f"garmin_export_{stamp}.txt"
        path.write_text(text, encoding="utf-8")
        return [path]

    pattern = r"(?=\n(?:" + "|".join(re.escape(n) for n in SECTION_ORDER) + r")\n)"
    parts = re.split(pattern, text)
    header, raw_sections = parts[0], parts[1:]
    files: list[list[str]] = []
    current: list[str] = []
    current_words = _word_count(header)
    for sec in raw_sections:
        words = _word_count(sec)
        if current and current_words + words > SPLIT_WORD_LIMIT:
            files.append(current)
            current, current_words = [], _word_count(header)
        current.append(sec)
        current_words += words
    if current:
        files.append(current)
    written = []
    total = len(files)
    for i, secs in enumerate(files, 1):
        names = [s.strip().split("\n")[1] if "\n" in s.strip() else s.strip()[:40] for s in secs]
        content = (f"Garmin Connect Data Export -- Part {i} of {total}\n\n"
                   f"Upload all {total} parts to the same notebook for complete data.\n"
                   + "\n".join(secs))
        path = out_dir / f"garmin_export_{stamp}_part{i}of{total}.txt"
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def run_export(api: Garmin, store: Store, out_dir: Path, days: Optional[int],
               fetch_all: bool, split: bool, compact: bool) -> list[Path]:
    today = date.today()
    if fetch_all:
        start = detect_start_date(api, today)
        backfill(api, store, start, today)
    else:
        start = today - timedelta(days=days or 30)
        backfill(api, store, start, today)
    fetch_sections(api, store, start, today)
    text = render(store, start, today, compact or split)
    return write_export(text, out_dir, split)
