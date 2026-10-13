#!/usr/bin/env python3
"""make_world.py: build tests/fixtures/world/ (run once by a chat; the output is committed). Real data, trimmed:
  games.csv                 nflverse games.csv, 2026 weeks 5-8 only
  roster_weekly_2026.csv    nflverse weekly rosters, SEA DEN HOU JAX only
  espn_injuries.json        ESPN injuries (Oct 13 2026 probe), SEA DEN HOU JAX only, links/headshots dropped
  odds/                     events.json, markets_<game>.json, odds_2026_06_SEA_DEN.json (Hard Rock + DraftKings probe)
  espn_summary_tb_dal.json  ESPN summary of TB at DAL (Oct 8 2026), box score only
Usage: python tests/make_world.py SCRATCH_DIR PROBE_DIR"""
import csv
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures", "world")
TEAMS = {"SEA", "DEN", "HOU", "JAX"}
ESPN_IDS = {"26", "7", "34", "30"}


def main(scratch, probe):
    os.makedirs(os.path.join(OUT, "odds"), exist_ok=True)
    with open(os.path.join(scratch, "games.csv")) as f:
        rows = list(csv.DictReader(f))
    keep = [r for r in rows if r["season"] == "2026" and 5 <= int(r["week"]) <= 8]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(keep)
    open(os.path.join(OUT, "games.csv"), "w").write(buf.getvalue())
    with open(os.path.join(scratch, "rw26.csv")) as f:
        rows = list(csv.DictReader(f))
    keep = [r for r in rows if r["team"] in TEAMS and r["position"] in ("QB", "RB", "WR", "TE", "FB")]
    last = {}
    for r in keep:
        last[r["team"]] = max(last.get(r["team"], 0), int(r["week"]))
    keep = [r for r in keep if int(r["week"]) == last[r["team"]]]
    cols = ["season", "team", "position", "status", "full_name", "first_name", "last_name", "gsis_id", "espn_id",
            "football_name", "week", "game_type"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(keep)
    open(os.path.join(OUT, "roster_weekly_2026.csv"), "w").write(buf.getvalue())
    inj = json.load(open(os.path.join(probe, "espn_injuries.json")))
    teams = []
    for t in inj["injuries"]:
        if t["id"] not in ESPN_IDS:
            continue
        out = []
        for i in t["injuries"]:
            a = i["athlete"]
            out.append({"id": i.get("id"), "status": i.get("status"), "date": i.get("date"),
                        "shortComment": (i.get("shortComment") or "")[:80], "type": i.get("type"),
                        "details": i.get("details"),
                        "athlete": {"displayName": a.get("displayName"), "position": a.get("position"),
                                    "team": {"id": a["team"]["id"], "abbreviation": a["team"]["abbreviation"]},
                                    "links": [l for l in a.get("links", []) if "/player/_/id/" in l.get("href", "")][:1]}})
        teams.append({"id": t["id"], "displayName": t["displayName"], "injuries": out})
    json.dump({"timestamp": inj["timestamp"], "injuries": teams}, open(os.path.join(OUT, "espn_injuries.json"), "w"))
    rep = json.load(open(os.path.join(probe, "report.json")))
    json.dump([{"id": e[0], "away_team": e[1], "home_team": e[2], "commence_time": e[3]} for e in rep["odds_events"]],
              open(os.path.join(OUT, "odds", "events.json"), "w"))
    for src, gid in (("markets_Sea_Den.json", "2026_06_SEA_DEN"), ("markets_Hou_Jac.json", "2026_06_HOU_JAX")):
        js = json.load(open(os.path.join(probe, src)))
        for b in js["bookmakers"]:
            b["markets"] = [{"key": m["key"]} for m in b["markets"]]
        json.dump(js, open(os.path.join(OUT, "odds", "markets_%s.json" % gid), "w"))
    hr = json.load(open(os.path.join(probe, "odds_event0_hardrock.json")))
    gm = json.load(open(os.path.join(probe, "odds_event0_game.json")))
    hr["bookmakers"][0]["markets"] += [m for b in gm["bookmakers"] if b["key"] == "hardrockbet_fl" for m in b["markets"]]
    hr["bookmakers"].append([b for b in gm["bookmakers"] if b["key"] == "draftkings"][0])
    json.dump(hr, open(os.path.join(OUT, "odds", "odds_2026_06_SEA_DEN.json"), "w"))
    s = json.load(open(os.path.join(probe, "espn_summary_final.json")))
    json.dump({"header": s["header"], "boxscore": {"players": s["boxscore"]["players"]}},
              open(os.path.join(OUT, "espn_summary_tb_dal.json"), "w"))
    print("world written to", OUT)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
