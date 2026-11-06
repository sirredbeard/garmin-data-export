"""Turn raw Garmin responses into the small summary an agent reads.

Pure functions. No network, no disk. The same raw day always produces
the same summary, which is what makes this file testable offline.

Garmin field notes, learned from real responses:
- Weights arrive in grams. Bone and muscle mass too.
- Sleep stage fields are in seconds. So are the stress duration
  fields on the daily summary (restStressDuration and friends sum to
  about a day in seconds). The one exception is
  durationInMilliseconds, which is not used here.
- The sleep score lives at dailySleepDTO.sleepScores.overall.value.
  There is no top-level score field.
- HRV for the night lives at hrvSummary.lastNightAvg, in milliseconds.
- Body battery headline numbers are precomputed on the daily summary
  (bodyBatteryHighestValue and friends). The per-day body battery
  endpoint returns a sparse values array and no high or low.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

GRAMS_PER_KG = 1000.0
LB_PER_KG = 2.20462


def _get(obj: Any, *keys: str) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _num(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    return round(value, digits) if value is not None else None


def _min_from_seconds(value: Any) -> Optional[int]:
    v = _num(value)
    return int(round(v / 60)) if v is not None else None


def _ms_epoch_to_iso(value: Any) -> Optional[str]:
    v = _num(value)
    if v is None:
        return value if isinstance(value, str) else None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _kg(grams: Any) -> Optional[float]:
    v = _num(grams)
    return _round(v / GRAMS_PER_KG, 2) if v is not None else None


def _lb(grams: Any) -> Optional[float]:
    v = _num(grams)
    return _round(v / GRAMS_PER_KG * LB_PER_KG, 1) if v is not None else None


def normalize_weigh_in(entry: dict) -> dict:
    """One scale reading, in units a person thinks in."""
    return {
        "date": entry.get("calendarDate"),
        "timestamp_gmt": entry.get("timestampGMT") or entry.get("date"),
        "weight_lb": _lb(entry.get("weight")),
        "weight_kg": _kg(entry.get("weight")),
        "bmi": _round(_num(entry.get("bmi")), 1),
        "body_fat_pct": _num(entry.get("bodyFat")),
        "body_water_pct": _num(entry.get("bodyWater")),
        "bone_mass_lb": _lb(entry.get("boneMass")),
        "muscle_mass_lb": _lb(entry.get("muscleMass")),
        "source": entry.get("sourceType"),
    }


def normalize_weigh_ins(raw: Any) -> dict[str, list[dict]]:
    """get_weigh_ins response -> {date: [reading, ...]}, oldest first."""
    by_date: dict[str, list[dict]] = {}
    if not isinstance(raw, dict):
        return by_date
    for summary in raw.get("dailyWeightSummaries") or []:
        entries = summary.get("allWeightMetrics") or []
        if not entries and summary.get("latestWeight"):
            entries = [summary["latestWeight"]]
        for entry in entries:
            if isinstance(entry, dict) and entry.get("calendarDate"):
                by_date.setdefault(entry["calendarDate"], []).append(normalize_weigh_in(entry))
    for readings in by_date.values():
        readings.sort(key=lambda r: r.get("timestamp_gmt") or 0)
    return dict(sorted(by_date.items()))


def summarize_activity(act: dict) -> dict:
    """The activity fields a daily check-in uses. Drops GPS, device ids,
    owner profile image URLs, and the rest of the summary noise."""
    atype = act.get("activityType") if isinstance(act.get("activityType"), dict) else {}
    duration = _num(act.get("duration"))
    return {
        "id": act.get("activityId"),
        "name": act.get("activityName"),
        "type": atype.get("typeKey"),
        "start_local": act.get("startTimeLocal"),
        "duration_min": _round(duration / 60, 1) if duration is not None else None,
        "distance_m": _round(_num(act.get("distance")), 0),
        "steps": act.get("steps"),
        "avg_hr": act.get("averageHR"),
        "max_hr": act.get("maxHR"),
        "calories": act.get("calories"),
        "elevation_gain_m": _round(_num(act.get("elevationGain")), 0),
        "avg_speed_mps": _round(_num(act.get("averageSpeed")), 2),
        "training_load": act.get("activityTrainingLoad"),
        "aerobic_te": act.get("aerobicTrainingEffect"),
        "anaerobic_te": act.get("anaerobicTrainingEffect"),
        "vo2_max": act.get("vO2MaxValue"),
    }


def summarize_day(day: date, raw: dict, activities: list[dict],
                  weigh_ins: list[dict]) -> dict:
    """Build one day's summary from its raw fetch and stored context."""
    summary = raw.get("summary") or {}
    sleep = raw.get("sleep") or {}
    dto = sleep.get("dailySleepDTO") or {}
    scores = dto.get("sleepScores") or {}
    hrv_summary = _get(raw, "hrv", "hrvSummary") or {}
    stress = raw.get("stress") or {}
    spo2 = raw.get("spo2") or {}
    resp = raw.get("respiration") or {}
    readiness = _first_for_date(raw.get("training_readiness"), day)

    out: dict[str, Any] = {
        "date": day.isoformat(),
        "fetched_at": raw.get("fetched_at"),
        "complete": bool(summary) and bool(dto),
        "steps": {
            "total": summary.get("totalSteps"),
            "goal": summary.get("dailyStepGoal"),
            "distance_m": _round(_num(summary.get("totalDistanceMeters")), 0),
            "floors": _round(_num(summary.get("floorsAscended")), 1),
        },
        "calories": {
            "total": summary.get("totalKilocalories"),
            "active": summary.get("activeKilocalories"),
            "bmr": summary.get("bmrKilocalories"),
        },
        "heart": {
            "resting_bpm": summary.get("restingHeartRate") or _get(raw, "rhr", "value"),
            "min_bpm": summary.get("minHeartRate"),
            "max_bpm": summary.get("maxHeartRate"),
            "last_7d_avg_resting_bpm": summary.get("lastSevenDaysAvgRestingHeartRate"),
        },
        "sleep": {
            "score": _get(scores, "overall", "value"),
            "qualifier": _get(scores, "overall", "qualifierKey"),
            "duration_min": _min_from_seconds(dto.get("sleepTimeSeconds")),
            "deep_min": _min_from_seconds(dto.get("deepSleepSeconds")),
            "rem_min": _min_from_seconds(dto.get("remSleepSeconds")),
            "light_min": _min_from_seconds(dto.get("lightSleepSeconds")),
            "awake_min": _min_from_seconds(dto.get("awakeSleepSeconds")),
            "start_local": _ms_epoch_to_iso(dto.get("sleepStartTimestampLocal")),
            "end_local": _ms_epoch_to_iso(dto.get("sleepEndTimestampLocal")),
            "avg_respiration": dto.get("averageRespirationValue"),
            "avg_stress": dto.get("avgSleepStress"),
            "overnight_hrv_ms": sleep.get("avgOvernightHrv"),
        },
        "stress": {
            "avg": summary.get("averageStressLevel") or stress.get("avgStressLevel"),
            "max": summary.get("maxStressLevel") or stress.get("maxStressLevel"),
            "qualifier": summary.get("stressQualifier"),
            "rest_min": _min_from_seconds(summary.get("restStressDuration")),
            "low_min": _min_from_seconds(summary.get("lowStressDuration")),
            "medium_min": _min_from_seconds(summary.get("mediumStressDuration")),
            "high_min": _min_from_seconds(summary.get("highStressDuration")),
        },
        "body_battery": {
            "at_wake": summary.get("bodyBatteryAtWakeTime"),
            "highest": summary.get("bodyBatteryHighestValue"),
            "lowest": summary.get("bodyBatteryLowestValue"),
            "most_recent": summary.get("bodyBatteryMostRecentValue"),
            "charged": summary.get("bodyBatteryChargedValue"),
            "drained": summary.get("bodyBatteryDrainedValue"),
        },
        "spo2": {
            "avg": spo2.get("averageSpO2") or summary.get("averageSpo2"),
            "lowest": spo2.get("lowestSpO2") or summary.get("lowestSpo2"),
            "latest": spo2.get("latestSpO2") or summary.get("latestSpo2"),
        },
        "respiration": {
            "avg_waking": resp.get("avgWakingRespirationValue") or summary.get("avgWakingRespirationValue"),
            "avg_sleep": resp.get("avgSleepRespirationValue"),
            "highest": resp.get("highestRespirationValue"),
            "lowest": resp.get("lowestRespirationValue"),
        },
        "hrv": {
            "last_night_avg_ms": hrv_summary.get("lastNightAvg"),
            "weekly_avg_ms": hrv_summary.get("weeklyAvg"),
            "status": hrv_summary.get("status"),
            "baseline_low_ms": _get(hrv_summary, "baseline", "balancedLow"),
            "baseline_high_ms": _get(hrv_summary, "baseline", "balancedUpper"),
        },
        "intensity_minutes": {
            "moderate": summary.get("moderateIntensityMinutes"),
            "vigorous": summary.get("vigorousIntensityMinutes"),
            "goal": summary.get("intensityMinutesGoal"),
        },
        "training_readiness": None,
        "weigh_ins": weigh_ins,
        "activities": [summarize_activity(a) for a in activities],
    }
    if readiness:
        out["training_readiness"] = {
            "score": readiness.get("score"),
            "level": readiness.get("level"),
            "sleep_score": readiness.get("sleepScore"),
            "recovery_time_hours": _round(_num(readiness.get("recoveryTime")) / 60, 1)
            if _num(readiness.get("recoveryTime")) is not None else None,
            "feedback_short": readiness.get("feedbackShort"),
        }
    return out


def _first_for_date(value: Any, day: date) -> Optional[dict]:
    """Readiness endpoints return a list. Take the entry for this date,
    or the first entry when the list carries no date."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("calendarDate") == day.isoformat():
                return item
        for item in value:
            if isinstance(item, dict):
                return item
    return None


def latest_weight_on_or_before(weigh_ins: dict[str, list[dict]], day: date) -> Optional[dict]:
    best = None
    for ds in sorted(weigh_ins):
        if ds <= day.isoformat() and weigh_ins[ds]:
            best = weigh_ins[ds][-1]
    return best


def summarize_week(summaries: list[dict], weigh_ins: dict[str, list[dict]]) -> dict:
    """Roll up a run of day summaries. Averages skip missing days instead
    of treating a gap as a zero. Garmin losing a week of data, which has
    happened, should read as a gap, not as a week of zeros."""
    summaries = sorted(summaries, key=lambda s: s.get("date") or "")

    def avg(path: tuple[str, ...]) -> Optional[float]:
        vals = []
        for s in summaries:
            v = _get(s, *path)
            if isinstance(v, (int, float)):
                vals.append(v)
        return _round(sum(vals) / len(vals), 1) if vals else None

    def total(path: tuple[str, ...]) -> Optional[float]:
        vals = []
        for s in summaries:
            v = _get(s, *path)
            if isinstance(v, (int, float)):
                vals.append(v)
        return sum(vals) if vals else None

    activities = [a for s in summaries for a in (s.get("activities") or [])]
    by_type: dict[str, int] = {}
    for act in activities:
        by_type[act.get("type") or "unknown"] = by_type.get(act.get("type") or "unknown", 0) + 1

    weights = []
    if summaries:
        first_day = date.fromisoformat(summaries[0]["date"])
        last_day = date.fromisoformat(summaries[-1]["date"])
        window = {ds: v for ds, v in weigh_ins.items()
                  if first_day.isoformat() <= ds <= last_day.isoformat()}
        flat = [r for ds in sorted(window) for r in window[ds]]
        if flat:
            weights = [flat[0], flat[-1]] if len(flat) > 1 else [flat[0]]

    rollup: dict[str, Any] = {
        "start": summaries[0]["date"] if summaries else None,
        "end": summaries[-1]["date"] if summaries else None,
        "days_with_data": len(summaries),
        "steps_avg": avg(("steps", "total")),
        "steps_total": total(("steps", "total")),
        "sleep_score_avg": avg(("sleep", "score")),
        "sleep_duration_min_avg": avg(("sleep", "duration_min")),
        "stress_avg": avg(("stress", "avg")),
        "resting_hr_avg": avg(("heart", "resting_bpm")),
        "hrv_last_night_avg_ms": avg(("hrv", "last_night_avg_ms")),
        "body_battery_high_avg": avg(("body_battery", "highest")),
        "activity_count": len(activities),
        "activities_by_type": by_type,
        "activity_minutes_total": _round(sum(a.get("duration_min") or 0 for a in activities), 0) or None,
        "weight_first_lb": weights[0]["weight_lb"] if weights else None,
        "weight_last_lb": weights[-1]["weight_lb"] if weights else None,
        "days": summaries,
    }
    if len(weights) == 2 and weights[0]["weight_lb"] is not None and weights[1]["weight_lb"] is not None:
        rollup["weight_change_lb"] = _round(weights[1]["weight_lb"] - weights[0]["weight_lb"], 1)
    else:
        rollup["weight_change_lb"] = None
    return rollup


def _fmt(value: Any, suffix: str = "") -> str:
    return f"{value}{suffix}" if value is not None else "-"


def render_day_md(s: dict) -> str:
    lines = [f"Garmin day: {s.get('date')}", ""]
    steps = s.get("steps") or {}
    lines.append(f"Steps: {_fmt(steps.get('total'))} (goal {_fmt(steps.get('goal'))}), "
                 f"distance {_fmt(steps.get('distance_m'), ' m')}, floors {_fmt(steps.get('floors'))}")
    sleep = s.get("sleep") or {}
    lines.append(f"Sleep: score {_fmt(sleep.get('score'))} ({_fmt(sleep.get('qualifier'))}), "
                 f"{_fmt(sleep.get('duration_min'), ' min')} "
                 f"(deep {_fmt(sleep.get('deep_min'))}, rem {_fmt(sleep.get('rem_min'))}, "
                 f"light {_fmt(sleep.get('light_min'))}, awake {_fmt(sleep.get('awake_min'))})")
    stress = s.get("stress") or {}
    lines.append(f"Stress: avg {_fmt(stress.get('avg'))}, max {_fmt(stress.get('max'))}, "
                 f"{_fmt(stress.get('qualifier'))} "
                 f"(rest {_fmt(stress.get('rest_min'), ' min')}, high {_fmt(stress.get('high_min'), ' min')})")
    heart = s.get("heart") or {}
    hrv = s.get("hrv") or {}
    lines.append(f"Heart: resting {_fmt(heart.get('resting_bpm'), ' bpm')}, "
                 f"range {_fmt(heart.get('min_bpm'))}-{_fmt(heart.get('max_bpm'), ' bpm')}; "
                 f"HRV last night {_fmt(hrv.get('last_night_avg_ms'), ' ms')} "
                 f"({_fmt(hrv.get('status'))}, weekly {_fmt(hrv.get('weekly_avg_ms'), ' ms')})")
    bb = s.get("body_battery") or {}
    lines.append(f"Body battery: high {_fmt(bb.get('highest'))}, low {_fmt(bb.get('lowest'))}, "
                 f"at wake {_fmt(bb.get('at_wake'))}, charged {_fmt(bb.get('charged'))}, "
                 f"drained {_fmt(bb.get('drained'))}")
    spo2 = s.get("spo2") or {}
    resp = s.get("respiration") or {}
    lines.append(f"SpO2: avg {_fmt(spo2.get('avg'), '%')}, lowest {_fmt(spo2.get('lowest'), '%')}; "
                 f"respiration waking {_fmt(resp.get('avg_waking'))}, sleep {_fmt(resp.get('avg_sleep'))}")
    im = s.get("intensity_minutes") or {}
    lines.append(f"Intensity minutes: moderate {_fmt(im.get('moderate'))}, "
                 f"vigorous {_fmt(im.get('vigorous'))} (goal {_fmt(im.get('goal'))})")
    ready = s.get("training_readiness")
    if ready:
        lines.append(f"Training readiness: {_fmt(ready.get('score'))} ({_fmt(ready.get('level'))}), "
                     f"sleep factor {_fmt(ready.get('sleep_score'))}")
    for reading in s.get("weigh_ins") or []:
        lines.append(f"Weigh-in: {_fmt(reading.get('weight_lb'), ' lb')} "
                     f"({_fmt(reading.get('weight_kg'), ' kg')}), "
                     f"body fat {_fmt(reading.get('body_fat_pct'), '%')}, "
                     f"muscle {_fmt(reading.get('muscle_mass_lb'), ' lb')}, "
                     f"BMI {_fmt(reading.get('bmi'))}")
    acts = s.get("activities") or []
    if acts:
        lines.append("")
        lines.append("Activities:")
        for act in acts:
            lines.append(f"- {_fmt(act.get('name'))} ({_fmt(act.get('type'))}) "
                         f"{_fmt(act.get('start_local'))}: {_fmt(act.get('duration_min'), ' min')}, "
                         f"{_fmt(act.get('distance_m'), ' m')}, "
                         f"avg HR {_fmt(act.get('avg_hr'))}, {_fmt(act.get('calories'), ' kcal')}")
    if not s.get("complete"):
        lines.append("")
        lines.append("Note: this day is incomplete. The summary or the sleep record is missing.")
    return "\n".join(lines) + "\n"


def render_week_md(rollup: dict) -> str:
    lines = [f"Garmin week: {rollup.get('start')} to {rollup.get('end')}", ""]
    lines.append(f"Days with data: {_fmt(rollup.get('days_with_data'))} of 7")
    lines.append(f"Steps: avg {_fmt(rollup.get('steps_avg'))} per day, total {_fmt(rollup.get('steps_total'))}")
    lines.append(f"Sleep: score avg {_fmt(rollup.get('sleep_score_avg'))}, "
                 f"duration avg {_fmt(rollup.get('sleep_duration_min_avg'), ' min')}")
    lines.append(f"Stress avg: {_fmt(rollup.get('stress_avg'))}; "
                 f"resting HR avg: {_fmt(rollup.get('resting_hr_avg'), ' bpm')}; "
                 f"HRV avg: {_fmt(rollup.get('hrv_last_night_avg_ms'), ' ms')}")
    lines.append(f"Activities: {_fmt(rollup.get('activity_count'))} "
                 f"({_fmt(rollup.get('activity_minutes_total'), ' min')} total) "
                 f"{rollup.get('activities_by_type') or {}}")
    if rollup.get("weight_last_lb") is not None:
        lines.append(f"Weight: {_fmt(rollup.get('weight_first_lb'), ' lb')} to "
                     f"{_fmt(rollup.get('weight_last_lb'), ' lb')} "
                     f"(change {_fmt(rollup.get('weight_change_lb'), ' lb')})")
    lines.append("")
    for s in rollup.get("days") or []:
        sleep = s.get("sleep") or {}
        stress = s.get("stress") or {}
        steps = s.get("steps") or {}
        lines.append(f"- {s.get('date')}: steps {_fmt(steps.get('total'))}, "
                     f"sleep {_fmt(sleep.get('score'))} / {_fmt(sleep.get('duration_min'), ' min')}, "
                     f"stress avg {_fmt(stress.get('avg'))}, "
                     f"activities {len(s.get('activities') or [])}")
    return "\n".join(lines) + "\n"
