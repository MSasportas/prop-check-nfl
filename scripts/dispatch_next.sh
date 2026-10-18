#!/usr/bin/env bash
# dispatch_next.sh: start the next link of the timer chain (workflow_dispatch of run.yml on the branch).
# GitHub lets GITHUB_TOKEN start a workflow_dispatch run (unlike pushes). Tries 4 times with backoff.
# Skipped when config/stop_timer exists on origin (the off switch). Needs GH_TOKEN; never prints it.
set -u
cd "$(dirname "$0")/.."
BRANCH="${GITHUB_REF_NAME:-main}"
WORKFLOW="${DISPATCH_WORKFLOW:-run.yml}"
git fetch -q origin "$BRANCH" 2>/dev/null
if git cat-file -e "origin/$BRANCH:config/stop_timer" 2>/dev/null; then
  echo "config/stop_timer is set: not starting a next run (the hourly restarter will also stand down)"
  exit 0
fi
LINK=$(( ${PC_RUNNER_LINK:-0} + 1 ))
CHAIN="${PC_CHAIN_STARTED_FINAL:-${PC_RUNNER_CHAIN_STARTED:-$(TZ=America/New_York date '+%Y-%m-%dT%H:%M:%S%:z')}}"
for attempt in 1 2 3 4; do
  if gh workflow run "$WORKFLOW" --ref "$BRANCH" -f chain_link="$LINK" -f chain_started_at="$CHAIN"; then
    echo "next run requested (chain link $LINK, chain started $CHAIN)"
    exit 0
  fi
  echo "dispatch attempt $attempt failed"
  sleep $((attempt * ${DISPATCH_BACKOFF:-15}))
done
echo "could not start the next run; the hourly schedule will restart the chain"
exit 1
