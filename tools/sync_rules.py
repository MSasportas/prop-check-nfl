#!/usr/bin/env python3
"""tools/sync_rules.py SHARP_RULES.json: take the Sharp's rules (values, ui, ops) and keep only the Builder's
ops keys the Sharp file does not have (timer cadences, cache ages). Writes config/rules.json. Used by chats only."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "rules.json")
BUILDER_OPS = ["_builder", "book", "h1_book", "rollover_weekday", "schedule_refresh_hours", "stats_check_min",
               "roster_every_hours", "injuries_every_min", "inactives_oldest_min", "weather_every_min",
               "grade_wait_hours", "game_cap_factor", "final_report_note", "record_weeks"]


def main(src):
    sharp = json.load(open(src))
    mine = json.load(open(CFG))
    for k in BUILDER_OPS:
        if k in mine.get("ops", {}) and k not in sharp["ops"]:
            sharp["ops"][k] = mine["ops"][k]
    for k, m in sharp["ops"].get("markets", {}).items():
        old = mine.get("ops", {}).get("markets", {}).get(k, {})
        if "bet_noun" not in m and old.get("bet_noun"):
            m["bet_noun"] = old["bet_noun"]
    sharp["ui"].setdefault("picks_open_per_day", 6)
    with open(CFG, "w") as f:
        f.write(json.dumps(sharp, indent=1) + "\n")
    print("config/rules.json synced from %s (version %s, updated %s)" % (src, sharp.get("version"), sharp.get("updated")))


if __name__ == "__main__":
    main(sys.argv[1])
