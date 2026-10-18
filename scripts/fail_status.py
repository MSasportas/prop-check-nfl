#!/usr/bin/env python3
"""fail_status.py: called by the workflow only when a run FAILED. Standard library only and no imports
from the rest of scripts/, so it still works if the engine itself is broken.

Marks data/status.json as failed (keeping what the last good run wrote) and adds the "last update failed"
banner to data/slate.json without touching picks, prices or generated_at. Touches nothing else.

Usage: python scripts/fail_status.py <step name>
"""
import json
import os
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("PC_DATA_DIR") or os.path.join(ROOT, "data")
MARK = "The last update failed"


def read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, indent=1) + "\n")
    os.replace(tmp, path)


def clock(dt):
    h = dt.hour % 12 or 12
    return "%d:%02d %s" % (h, dt.minute, "AM" if dt.hour < 12 else "PM")


def main(argv):
    step = (argv[0] if argv else "run")[:40]
    raw = os.environ.get("PC_NOW")
    now = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET) if raw else datetime.now(ET)
    now = now.replace(microsecond=0)
    st = read(os.path.join(DATA, "status.json")) or {}
    env = os.environ
    if env.get("PC_RUNNER_RUN_ID") or env.get("PC_RUNNER_EVENT"):
        num = lambda k: int(env[k]) if env.get(k, "").isdigit() else None
        st["runner"] = {"event": env.get("PC_RUNNER_EVENT"), "run_id": env.get("PC_RUNNER_RUN_ID"),
                        "iteration": num("PC_RUNNER_ITERATION"), "chain_link": num("PC_RUNNER_LINK"),
                        "chain_started_at": env.get("PC_RUNNER_CHAIN_STARTED") or None,
                        "run_started_at": env.get("PC_RUNNER_RUN_STARTED") or None}
    st["status_at"] = now.isoformat()
    st.update({"ok": False, "failed_step": step, "failed_at": now.isoformat(),
               "note": "The run stopped before finishing. Files from the last good run are still in place."})
    write(os.path.join(DATA, "status.json"), st)
    sl = read(os.path.join(DATA, "slate.json"))
    if isinstance(sl, dict) and "status" in sl:
        sl["status"]["last_run"] = {"ok": False, "at": now.isoformat(), "failed_step": step}
        sl["status_at"] = now.isoformat()                  # tells the page to re-render; generated_at is untouched
        try:
            shown = clock(datetime.fromisoformat(sl["generated_at"]).astimezone(ET))
        except Exception:
            shown = "the last good update"
        text = "%s at %s (%s). Showing %s data." % (MARK, clock(now), step, shown)
        sl["banners"] = [{"level": "stop", "text": text}] + \
                        [b for b in sl.get("banners", []) if not str(b.get("text", "")).startswith(MARK)]
        write(os.path.join(DATA, "slate.json"), sl)
    print("marked the run as failed at step: %s" % step)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
