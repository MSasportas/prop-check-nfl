"""roster.py: who is on each team this week (nflverse weekly rosters, refreshed daily) -> data/state/roster.json.

{gsis_id: {first, last, name, team, pos, espn_id, status}} for QB RB WR TE FB. A player who has rows for a team
this season but is not in the roster file (signed this week) is added from the stats store by run.py.
The odds feed's name is matched with names.Matcher on both the full name and the football (short) name.
"""
import csv
import io
from datetime import timedelta

import common
import teams

URL = "https://github.com/nflverse/nflverse-data/releases/download/weekly_rosters/roster_weekly_%d.csv"
SKILL = {"QB", "RB", "WR", "TE", "FB", "HB"}


def path():
    return common.dpath("state", "roster.json")


def load():
    return (common.read_json(path(), {}) or {}).get("players", {})


def parse(text, season):
    rows = [r for r in csv.DictReader(io.StringIO(text)) if r.get("season") == str(season)]
    if not rows:
        return {}
    last = {}                                                # each team's own latest week (bye teams lag one)
    for r in rows:
        if (r.get("week") or "").isdigit():
            t = teams.norm_code(r.get("team"))
            last[t] = max(last.get(t, 0), int(r["week"]))
    out = {}
    for r in rows:
        if r.get("week") != str(last.get(teams.norm_code(r.get("team")))) or (r.get("position") or "").upper() not in SKILL:
            continue
        if r.get("status") in ("CUT", "RET", "TRD", "TRT"):
            continue
        gs = r.get("gsis_id")
        if not gs:
            continue
        pos = "RB" if r["position"].upper() in ("FB", "HB") else r["position"].upper()
        out[gs] = {"first": r.get("first_name") or "", "last": r.get("last_name") or "", "name": r.get("full_name") or "",
                   "football": r.get("football_name") or "", "team": teams.norm_code(r.get("team")), "pos": pos,
                   "espn_id": r.get("espn_id") or None, "status": r.get("status") or ""}
    return out


def update(now, season, cfg, force=False):
    saved = common.read_json(path(), {}) or {}
    last = common.parse_iso(saved.get("at"))
    if not force and last is not None and now >= last and now - last < timedelta(hours=cfg["ops"].get("roster_every_hours", 12)) \
            and saved.get("players"):
        return {"status": "skipped", "note": "read at %s" % common.fmt_time(last)}, saved.get("players", {})
    body = common.feed_get("nflverse_roster", URL % season, "roster_weekly_%d.csv" % season, timeout=90)
    if body is None:
        return {"status": "failed", "note": "weekly roster did not load" + (", using the saved copy" if saved else "")}, \
            saved.get("players", {})
    players = parse(body.decode("utf-8", "replace"), season)
    if not players:
        return {"status": "failed", "note": "weekly roster had no players for %d" % season}, saved.get("players", {})
    if players != saved.get("players"):
        common.write_json(path(), {"at": common.iso(now), "season": season, "players": players}, pretty=False)
    else:
        saved["at"] = common.iso(now)
        common.write_json(path(), saved, pretty=False)
    return {"status": "ok", "note": "%d skill players" % len(players)}, players
