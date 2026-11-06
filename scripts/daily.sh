#!/usr/bin/env bash
# Daily Garmin sync for the ventunoq board.
#
# Pulls the last three days (today, yesterday, and one more to catch a
# late watch sync), rebuilds the summaries, and appends to the log in
# the data directory. Run it from cron or the systemd timer in deploy/.
# Exit code comes from the sync: 0 clean, 3 partial. Either way the
# store holds whatever was fetched, and the next run repairs the rest.
set -uo pipefail

cd "$(dirname "$0")/.."
DATA_DIR="${GARMIN_DATA_DIR:-$PWD/data}"
mkdir -p "$DATA_DIR/logs"
LOG="$DATA_DIR/logs/sync-$(date +%F).log"

.venv/bin/python -m garmin_daily --data "$DATA_DIR" sync --days 3 >>"$LOG" 2>&1
code=$?
echo "$(date -Is) sync exit $code" >>"$LOG"
exit $code
