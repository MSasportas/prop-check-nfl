#!/usr/bin/env bash
# loop.sh: runs scripts/cycle.sh again and again for about LOOP_SECONDS (default 3000 = 50 minutes), then exits
# so the workflow can hand over to the next run. One failed cycle never ends the loop.
#
# Cadence (Eastern, from scripts/cadence.py): every 10 minutes on game days 8 AM-1 AM and from 3 hours before any
# kickoff to 4 hours after; every 30 minutes on other days 8 AM-1 AM; hourly overnight. If cadence.py fails, 10 min.
# The loop stops early when the code on origin changed (the next run picks up new scripts / config / workflow), or
# when the file config/stop_timer exists (the off switch).
#
# Exports for run.py's status.json "runner" block: PC_RUNNER_EVENT, PC_RUNNER_RUN_ID, PC_RUNNER_LINK,
# PC_RUNNER_CHAIN_STARTED, PC_RUNNER_RUN_STARTED, PC_RUNNER_ITERATION.
# Tests: LOOP_MAX_ITER (stop after N cycles), LOOP_SLEEP (fixed seconds instead of the cadence).
set -u
cd "$(dirname "$0")/.."
PY="${PC_PYTHON:-python3}"
BRANCH="${GITHUB_REF_NAME:-main}"
LOOP_SECONDS="${LOOP_SECONDS:-3000}"
START=$(date +%s)
export PC_RUNNER_RUN_STARTED="$(TZ=America/New_York date '+%Y-%m-%dT%H:%M:%S%:z')"
export PC_RUNNER_CHAIN_STARTED="${PC_RUNNER_CHAIN_STARTED:-$PC_RUNNER_RUN_STARTED}"
[ -n "${GITHUB_ENV:-}" ] && echo "PC_CHAIN_STARTED_FINAL=$PC_RUNNER_CHAIN_STARTED" >> "$GITHUB_ENV"
code_id() { git rev-parse "origin/$BRANCH:scripts" "origin/$BRANCH:config" "origin/$BRANCH:.github" 2>/dev/null | tr '\n' ' '; }
git fetch -q origin "$BRANCH" 2>/dev/null
CODE="$(code_id)"

if git cat-file -e "origin/$BRANCH:config/stop_timer" 2>/dev/null; then echo "=== config/stop_timer is set: no cycles"; exit 0; fi

i=0
while :; do
  i=$((i + 1))
  export PC_RUNNER_ITERATION=$i
  echo "=== cycle $i (run ${PC_RUNNER_RUN_ID:-local}, link ${PC_RUNNER_LINK:-?})"
  bash scripts/cycle.sh || echo "=== cycle $i failed; the loop carries on"
  if [ -n "${LOOP_MAX_ITER:-}" ] && [ $i -ge "$LOOP_MAX_ITER" ]; then echo "=== stop: $i cycles done"; break; fi
  if git cat-file -e "origin/$BRANCH:config/stop_timer" 2>/dev/null; then echo "=== stop: config/stop_timer is set"; break; fi
  if [ "$(code_id)" != "$CODE" ]; then echo "=== stop: new code on $BRANCH, handing over to a fresh run"; break; fi
  if [ -n "${LOOP_SLEEP:-}" ]; then
    wait_s=$LOOP_SLEEP
  else
    wait_s=$(env -u PC_NOW "$PY" scripts/cadence.py 2>/dev/null) || wait_s=600
    case "$wait_s" in ''|*[!0-9]*) wait_s=600 ;; esac
  fi
  if [ $(( $(date +%s) + wait_s - START )) -gt "$LOOP_SECONDS" ]; then echo "=== stop: time is up, handing over"; break; fi
  echo "=== sleeping ${wait_s}s"
  sleep "$wait_s"
done
exit 0
