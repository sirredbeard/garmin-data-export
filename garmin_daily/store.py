"""The local store. Plain JSON on disk, nothing else.

Layout under the data directory (data/ by default):

    daily/2026-11-05.json            raw API responses for one day
    daily/2026-11-05.summary.json    the small extracted summary
    activities/24932680521.json      one activity, summary plus detail
    activities/index.json            every stored activity, newest first
    range/weigh_ins.json             normalized weigh-ins, by date
    range/body_composition.json      raw range response, by date
    range/steps.json                 daily step totals, by date
    range/resting_heart_rate.json    daily resting heart rate, by date
    range/hrv.json                   HRV summaries, by date
    range/max_metrics.json           VO2 max and fitness age, by date
    range/blood_pressure.json        blood pressure summaries, raw
    sections/profile.json            slow-changing sections, for export
    summaries/2026-11-05.md          one page, human and agent readable
    summaries/latest.json            newest complete day's summary
    summaries/latest.md              same day, as markdown
    summaries/week.json              rollup of the last 7 complete days
    summaries/week.md                same rollup, as markdown
    manifest.json                    what is stored, and when it was fetched
    state.json                       last sync run: when, what, any errors

Summaries are separate files on purpose. A raw day runs to hundreds of
kilobytes of minute-level time series. A summary is about two kilobytes
and holds every number a daily check-in needs. An agent should read
summaries by default and open raw files only for a specific question
the summary does not answer.

All writes are atomic: write to a temp file in the same directory, then
rename. A sync killed halfway through leaves the old file or the new
one, never a truncated one.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, default=str, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.daily_dir = self.root / "daily"
        self.activities_dir = self.root / "activities"
        self.range_dir = self.root / "range"
        self.sections_dir = self.root / "sections"
        self.summaries_dir = self.root / "summaries"

    # -- daily raw and summary -------------------------------------------
    def day_path(self, day: date) -> Path:
        return self.daily_dir / f"{day.isoformat()}.json"

    def summary_path(self, day: date) -> Path:
        return self.daily_dir / f"{day.isoformat()}.summary.json"

    def has_day(self, day: date) -> bool:
        return self.day_path(day).exists()

    def put_day(self, day: date, raw: dict) -> None:
        write_json(self.day_path(day), raw)

    def get_day(self, day: date) -> Optional[dict]:
        data = read_json(self.day_path(day))
        return data if isinstance(data, dict) else None

    def put_summary(self, day: date, summary: dict) -> None:
        write_json(self.summary_path(day), summary)

    def get_summary(self, day: date) -> Optional[dict]:
        data = read_json(self.summary_path(day))
        return data if isinstance(data, dict) else None

    def stored_days(self) -> list[date]:
        days = []
        if self.daily_dir.exists():
            for path in self.daily_dir.glob("????-??-??.json"):
                try:
                    days.append(date.fromisoformat(path.stem))
                except ValueError:
                    continue
        return sorted(days)

    # -- activities --------------------------------------------------------
    def activity_path(self, activity_id: Any) -> Path:
        return self.activities_dir / f"{activity_id}.json"

    def has_activity_detail(self, activity_id: Any) -> bool:
        data = read_json(self.activity_path(activity_id))
        return isinstance(data, dict) and data.get("detail") is not None

    def put_activity(self, activity_id: Any, data: dict) -> None:
        write_json(self.activity_path(activity_id), data)

    def get_activity(self, activity_id: Any) -> Optional[dict]:
        data = read_json(self.activity_path(activity_id))
        return data if isinstance(data, dict) else None

    def activity_index(self) -> list[dict]:
        data = read_json(self.activities_dir / "index.json")
        return data if isinstance(data, list) else []

    def put_activity_index(self, index: list[dict]) -> None:
        write_json(self.activities_dir / "index.json", index)

    # -- range data --------------------------------------------------------
    def put_range(self, kind: str, by_date: dict) -> None:
        """Merge a by-date mapping into range/<kind>.json. New values win."""
        path = self.range_dir / f"{kind}.json"
        existing = read_json(path)
        merged = existing if isinstance(existing, dict) else {}
        merged.update(by_date)
        write_json(path, dict(sorted(merged.items())))

    def get_range(self, kind: str) -> dict:
        data = read_json(self.range_dir / f"{kind}.json")
        return data if isinstance(data, dict) else {}

    # -- sections (slow-changing, used by the bulk export) -----------------
    def put_section(self, name: str, data: Any) -> None:
        write_json(self.sections_dir / f"{name}.json", {"fetched_at": utc_now(), "data": data})

    def get_section(self, name: str) -> Optional[dict]:
        data = read_json(self.sections_dir / f"{name}.json")
        return data if isinstance(data, dict) else None

    # -- summaries, manifest, state ----------------------------------------
    def put_summary_md(self, day: date, text: str) -> None:
        write_text(self.summaries_dir / f"{day.isoformat()}.md", text)

    def put_latest(self, summary: dict, markdown: str) -> None:
        write_json(self.summaries_dir / "latest.json", summary)
        write_text(self.summaries_dir / "latest.md", markdown)

    def put_week(self, rollup: dict, markdown: str) -> None:
        write_json(self.summaries_dir / "week.json", rollup)
        write_text(self.summaries_dir / "week.md", markdown)

    def manifest(self) -> dict:
        data = read_json(self.root / "manifest.json")
        return data if isinstance(data, dict) else {}

    def put_manifest(self, manifest: dict) -> None:
        write_json(self.root / "manifest.json", manifest)

    def put_state(self, state: dict) -> None:
        write_json(self.root / "state.json", state)

    def state(self) -> dict:
        data = read_json(self.root / "state.json")
        return data if isinstance(data, dict) else {}
