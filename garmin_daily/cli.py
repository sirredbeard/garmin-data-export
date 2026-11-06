"""Command line entry point.

Two kinds of commands:

Sync commands talk to Garmin: login, sync, export. They are the only
ones that need the network or the token cache.

Read commands never touch the network: show, activities, weight,
status, rebuild. They read the store and print JSON. An agent can run
them as often as it wants, at no cost, and parse stdout directly.
Pass --fetch to a read command to sync first, then read.

Every read command prints one JSON object to stdout. Logs go to
stderr. That split is deliberate: stdout stays machine-readable.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from . import __version__
from .client import authenticate, friendly_login_error, RateLimiter
from . import client as client_module
from .store import Store

log = logging.getLogger("garmin_daily")

EXIT_OK = 0
EXIT_AUTH = 2
EXIT_PARTIAL = 3


def _parse_day(text: str | None, default: date) -> date:
    if text is None:
        return default
    lowered = text.strip().lower()
    today = date.today()
    if lowered == "today":
        return today
    if lowered == "yesterday":
        return today - timedelta(days=1)
    return date.fromisoformat(text.strip())


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, default=str, ensure_ascii=False))


def _store(args) -> Store:
    root = Path(args.data).expanduser()
    return Store(root)


def _api(args):
    if args.delay is not None:
        client_module.limiter = RateLimiter(base_delay=args.delay)
    tokenstore = args.tokenstore or os.getenv("GARMINTOKENS", "~/.garminconnect")
    return authenticate(tokenstore)


def _sync_window(args, start: date, end: date) -> dict:
    from .fetch import sync
    api = _api(args)
    return sync(api, _store(args), start, end, detail=not args.no_detail)


def cmd_login(args) -> int:
    try:
        _api(args)
    except Exception as exc:
        log.error("Login failed: %s", friendly_login_error(exc))
        return EXIT_AUTH
    log.info("Login works. Tokens are cached and later runs need no prompt.")
    return EXIT_OK


def cmd_sync(args) -> int:
    today = date.today()
    if args.date:
        start = end = _parse_day(args.date, today)
    elif args.yesterday:
        start = end = today - timedelta(days=1)
    elif args.today:
        start = end = today
    elif args.from_date or args.to_date:
        end = _parse_day(args.to_date, today)
        start = _parse_day(args.from_date, end - timedelta(days=args.days - 1))
    else:
        end = today
        start = today - timedelta(days=args.days - 1)
    try:
        state = _sync_window(args, start, end)
    except SystemExit:
        raise
    except Exception as exc:
        log.error("Sync failed: %s", friendly_login_error(exc))
        return EXIT_AUTH
    result = {"ok": not state.get("errors"), **state, "api_calls": client_module.limiter.call_count}
    _print_json(result)
    return EXIT_OK if not state.get("errors") else EXIT_PARTIAL


def cmd_show(args) -> int:
    store = _store(args)
    today = date.today()
    if args.week:
        if args.fetch:
            code = cmd_sync(_sync_args(args, days=7))
            if code not in (EXIT_OK, EXIT_PARTIAL):
                return code
        rollup_path = store.summaries_dir / "week.json"
        from .store import read_json
        rollup = read_json(rollup_path)
        if rollup is None:
            _print_json({"error": "No week rollup stored yet. Run a sync first."})
            return EXIT_PARTIAL
        if args.format == "md":
            md_path = store.summaries_dir / "week.md"
            print(md_path.read_text(encoding="utf-8") if md_path.exists() else "")
        else:
            _print_json(rollup)
        return EXIT_OK

    day = _parse_day(args.date, today - timedelta(days=1))
    if args.fetch:
        code = cmd_sync(_sync_args(args, single=day))
        if code not in (EXIT_OK, EXIT_PARTIAL):
            return code
        store = _store(args)
    if args.raw:
        raw = store.get_day(day)
        if raw is None:
            _print_json({"error": f"No raw data stored for {day.isoformat()}. Run a sync that covers it."})
            return EXIT_PARTIAL
        _print_json(raw)
        return EXIT_OK
    summary = store.get_summary(day)
    if summary is None:
        _print_json({"error": f"No summary stored for {day.isoformat()}. Run a sync that covers it."})
        return EXIT_PARTIAL
    if args.format == "md":
        from .summarize import render_day_md
        print(render_day_md(summary), end="")
    else:
        _print_json(summary)
    return EXIT_OK


def _sync_args(args, days: int | None = None, single: date | None = None):
    """Build the argument shape cmd_sync expects, for --fetch on reads."""
    ns = argparse.Namespace(**vars(args))
    ns.date = single.isoformat() if single else None
    ns.yesterday = False
    ns.today = False
    ns.from_date = None
    ns.to_date = None
    ns.days = days or 3
    ns.no_detail = False
    return ns


def cmd_activities(args) -> int:
    store = _store(args)
    if args.fetch:
        code = cmd_sync(_sync_args(args, days=args.days))
        if code not in (EXIT_OK, EXIT_PARTIAL):
            return code
        store = _store(args)
    index = store.activity_index()
    if args.date:
        day = _parse_day(args.date, date.today())
        index = [a for a in index if (a.get("start_local") or "").startswith(day.isoformat())]
    else:
        cutoff = (date.today() - timedelta(days=args.days - 1)).isoformat()
        index = [a for a in index if (a.get("start_local") or "")[:10] >= cutoff]
    _print_json({"count": len(index), "activities": index})
    return EXIT_OK


def cmd_weight(args) -> int:
    store = _store(args)
    if args.fetch:
        code = cmd_sync(_sync_args(args, days=args.days))
        if code not in (EXIT_OK, EXIT_PARTIAL):
            return code
        store = _store(args)
    weigh_ins = store.get_range("weigh_ins")
    cutoff = (date.today() - timedelta(days=args.days - 1)).isoformat()
    flat = []
    for ds in sorted(weigh_ins):
        if ds >= cutoff:
            for reading in weigh_ins[ds]:
                flat.append(reading)
    flat.sort(key=lambda r: r.get("timestamp_gmt") or 0)
    latest = None
    for ds in sorted(weigh_ins, reverse=True):
        if weigh_ins[ds]:
            latest = weigh_ins[ds][-1]
            break
    change = None
    if len(flat) >= 2 and flat[0].get("weight_lb") is not None and flat[-1].get("weight_lb") is not None:
        change = round(flat[-1]["weight_lb"] - flat[0]["weight_lb"], 1)
    _print_json({
        "days": args.days,
        "count": len(flat),
        "latest": latest,
        "change_lb_in_window": change,
        "readings": list(reversed(flat)),
    })
    return EXIT_OK


def cmd_status(args) -> int:
    store = _store(args)
    tokenstore = Path(args.tokenstore or os.getenv("GARMINTOKENS", "~/.garminconnect")).expanduser()
    manifest = store.manifest()
    state = store.state()
    latest = store.get_summary(date.fromisoformat(manifest["latest_complete_day"])) if manifest.get("latest_complete_day") else None
    _print_json({
        "version": __version__,
        "data_dir": str(store.root),
        "tokens_cached": tokenstore.exists(),
        "manifest": manifest,
        "last_sync": state,
        "latest_complete_day": manifest.get("latest_complete_day"),
        "latest_sleep_score": (latest or {}).get("sleep", {}).get("score") if latest else None,
        "latest_steps": (latest or {}).get("steps", {}).get("total") if latest else None,
    })
    return EXIT_OK


def cmd_rebuild(args) -> int:
    from .fetch import rebuild_activity_index, rebuild_summaries
    store = _store(args)
    rebuild_activity_index(store)
    built = rebuild_summaries(store, store.stored_days())
    _print_json({"rebuilt": [s["date"] for s in built], "manifest": store.manifest()})
    return EXIT_OK


def cmd_export(args) -> int:
    from .export import run_export
    try:
        api = _api(args)
    except Exception as exc:
        log.error("Login failed: %s", friendly_login_error(exc))
        return EXIT_AUTH
    out_dir = Path(args.output).expanduser()
    written = run_export(api, _store(args), out_dir, days=args.days,
                         fetch_all=args.all, split=args.split, compact=args.compact)
    _print_json({"files": [str(p) for p in written],
                 "sizes_kb": [round(p.stat().st_size / 1024) for p in written]})
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="garmin-daily",
        description="Garmin Connect data for daily use by a personal agent. "
                    "Sync writes a local JSON store. Read commands print JSON from the store and never use the network.",
    )
    parser.add_argument("--version", action="version", version=f"garmin-daily {__version__}")
    parser.add_argument("--data", default=os.getenv("GARMIN_DATA_DIR", "data"),
                        help="Data directory (default: ./data, or GARMIN_DATA_DIR)")
    parser.add_argument("--tokenstore", default=None,
                        help="Token storage path (default: ~/.garminconnect)")
    parser.add_argument("--delay", type=float, default=None,
                        help="Base delay between API calls in seconds (default: 0.15)")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Log in once and cache tokens, then exit")

    p_sync = sub.add_parser("sync", help="Fetch a window into the store (default: last 3 days)")
    p_sync.add_argument("--days", type=int, default=3, help="Window size ending today (default: 3)")
    p_sync.add_argument("--today", action="store_true", help="Only today")
    p_sync.add_argument("--yesterday", action="store_true", help="Only yesterday")
    p_sync.add_argument("--date", help="Only this date (YYYY-MM-DD)")
    p_sync.add_argument("--from", dest="from_date", help="Window start (YYYY-MM-DD)")
    p_sync.add_argument("--to", dest="to_date", help="Window end (YYYY-MM-DD)")
    p_sync.add_argument("--no-detail", action="store_true",
                        help="Skip the nine detail calls per activity. Faster, list data only")

    p_show = sub.add_parser("show", help="Print one day's summary, or the week rollup")
    p_show.add_argument("date", nargs="?", help="YYYY-MM-DD, today, or yesterday (default: yesterday)")
    p_show.add_argument("--week", action="store_true", help="Rollup of the last 7 complete days")
    p_show.add_argument("--raw", action="store_true", help="Print the raw day instead of the summary. Large")
    p_show.add_argument("--format", choices=["json", "md"], default="json")
    p_show.add_argument("--fetch", action="store_true", help="Sync first, then read")

    p_act = sub.add_parser("activities", help="List stored activities")
    p_act.add_argument("--days", type=int, default=7, help="Window ending today (default: 7)")
    p_act.add_argument("--date", help="Only this date (YYYY-MM-DD)")
    p_act.add_argument("--fetch", action="store_true", help="Sync first, then read")

    p_weight = sub.add_parser("weight", help="Weigh-ins in a window, plus the latest reading")
    p_weight.add_argument("--days", type=int, default=30, help="Window ending today (default: 30)")
    p_weight.add_argument("--fetch", action="store_true", help="Sync first, then read")

    sub.add_parser("status", help="Store coverage, last sync, and token state as JSON")
    sub.add_parser("rebuild", help="Rebuild summaries, index, and manifest from stored raw data. Offline")

    p_export = sub.add_parser("export", help="Bulk text export for long-term analysis (Gemini Notebook and similar)")
    p_export.add_argument("--days", type=int, default=30, help="Days to cover (default: 30)")
    p_export.add_argument("--all", action="store_true", help="Complete history, back to the oldest activity")
    p_export.add_argument("--compact", action="store_true", help="Strip empty fields, single-line JSON")
    p_export.add_argument("--split", action="store_true", help="Split under 500K words per file. Implies --compact")
    p_export.add_argument("--output", default="export", help="Output directory (default: ./export)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("garminconnect").setLevel(logging.WARNING)
    handlers = {
        "login": cmd_login,
        "sync": cmd_sync,
        "show": cmd_show,
        "activities": cmd_activities,
        "weight": cmd_weight,
        "status": cmd_status,
        "rebuild": cmd_rebuild,
        "export": cmd_export,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
