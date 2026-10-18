#!/usr/bin/env python3
"""cadence.py: how long the timer sleeps before the next cycle (prints whole seconds). Stdlib only, no imports from
the rest of scripts/, so a broken engine never stops the timer.

  every 10 min  on a game day (any kickoff that Eastern date) from 8 AM to 1 AM, and from 3 hours before any kickoff
                to 4 hours after it (late games, finals, grading)
  every 30 min  on other days from 8 AM to 1 AM
  every 60 min  1 AM to 8 AM when no game is near
Cycles land on the :00 / :10 / :20 ... (or :00 / :30, :00) marks, so the 10 AM and 4 PM odds checks are hit.

  python scripts/cadence.py                 sleep for now (PC_NOW honoured)
  python scripts/cadence.py 2026-10-15T19:03:00-04:00
"""
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def kickoffs():
    data = os.environ.get("PC_DATA_DIR") or os.path.join(ROOT, "data")
    try:
        with open(os.path.join(data, "state", "schedule.json")) as f:
            games = json.load(f).get("games", {})
        return [datetime.fromisoformat(g["start"]).astimezone(ET) for g in games.values()]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def step_for(now, kicks):
    if any(k - timedelta(hours=3) <= now <= k + timedelta(hours=4) for k in kicks):
        return 600
    awake = now.hour >= 8 or now.hour < 1
    gameday = any(k.date() == now.date() for k in kicks) or \
        (now.hour < 1 and any(k.date() == (now - timedelta(days=1)).date() for k in kicks))
    if awake and gameday:
        return 600
    if awake:
        return 1800
    return 3600


def sleep_for(now, kicks):
    step = step_for(now, kicks)
    secs = now.hour * 3600 + now.minute * 60 + now.second
    return (secs // step + 1) * step - secs


def main(argv):
    if argv:
        now = datetime.fromisoformat(argv[0]).astimezone(ET)
    elif os.environ.get("PC_NOW"):
        now = datetime.fromisoformat(os.environ["PC_NOW"].replace("Z", "+00:00")).astimezone(ET)
    else:
        now = datetime.now(ET)
    print(sleep_for(now, kickoffs()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
