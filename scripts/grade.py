"""grade.py: results once each game is final. Appends "graded" events; never edits one.

Sources, in order of trust
  nflverse  the season's rows (nflrows.py on nflverse play-by-play). Grades everything, FG and 1H. Usually posted
            the morning after a game.
  espn_box  ESPN game summary box score, right after the final. FG only, for the stats the box shows directly:
            passing (C/ATT, YDS, TD, INT), rushing (CAR, YDS, TD, LONG), receiving (REC, YDS, TD, LONG), return
            TDs. Verified equal to nflverse on TB@DAL (Oct 8 2026). 1H stats are not in the box: 1H waits for
            nflverse. An anytime TD "no TD found" in the box waits for nflverse too (a fumble-recovery TD by an
            offensive player is not in the box), unless the game is ops.grade_wait_hours old.
Each graded event says which source graded it. When nflverse later disagrees with an ESPN grade, the event is
NOT changed (append-only); status.json lists it under grade_checks for the team to look at.

Did not play -> VOID, but only with proof: he is on the team's inactive list, or the game's snap counts are in
(nflverse) and he has none. Otherwise a missing row may be "played, no stats": wait for nflverse snaps; after
ops.grade_wait_hours a missing row with snap data still absent is graded VOID with a note.
Postponed or cancelled -> VOID. A whole-number tie with the line -> PUSH. Graded at the worst price shown.
"""
from datetime import timedelta

import common
import nflrows
import record
import sched

SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=%s"
BOX_MARKETS = {"pass_yds", "pass_att", "pass_cmp", "pass_td", "pass_int", "rush_yds", "rush_att", "rec", "rec_yds",
               "rush_rec_yds", "pass_rush_yds", "anytime_td", "long_rec", "long_rush"}


def result_for(side, line, actual):
    if actual == line:
        return "PUSH"
    return "W" if (actual > line) == (side == "over") else "L"


def _i(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def read_box(summary, espn_to_gsis):
    """ESPN summary -> {gsis: row-like dict with FG stats} or None. Players without a gsis id are skipped."""
    try:
        teams_ = summary["boxscore"]["players"]
    except (KeyError, TypeError):
        return None
    out = {}
    for t in teams_ or []:
        for st in t.get("statistics", []) or []:
            keys = st.get("keys") or []
            for a in st.get("athletes", []) or []:
                pid = espn_to_gsis.get(str((a.get("athlete") or {}).get("id")))
                if not pid:
                    continue
                r = out.setdefault(pid, dict.fromkeys(nflrows.STATS, 0))
                vals = dict(zip(keys, a.get("stats") or []))
                n = st.get("name")
                if n == "passing":
                    c, _, att = (vals.get("completions/passingAttempts") or "0/0").partition("/")
                    r.update(pc=_i(c), pa=_i(att), py=_i(vals.get("passingYards")), ptd=_i(vals.get("passingTouchdowns")),
                             pi=_i(vals.get("interceptions")))
                elif n == "rushing":
                    r.update(ra=_i(vals.get("rushingAttempts")), ry=_i(vals.get("rushingYards")),
                             rt=_i(vals.get("rushingTouchdowns")), lu=_i(vals.get("longRushing")))
                elif n == "receiving":
                    r.update(rc=_i(vals.get("receptions")), rcy=_i(vals.get("receivingYards")),
                             rct=_i(vals.get("receivingTouchdowns")), lr=_i(vals.get("longReception")),
                             tg=_i(vals.get("receivingTargets")))
                elif n in ("kickReturns", "puntReturns"):
                    r["otd"] += _i(vals.get("kickReturnTouchdowns") or vals.get("puntReturnTouchdowns"))
                elif n == "interceptions":
                    r["otd"] += _i(vals.get("interceptionTouchdowns"))
    return out


def espn_final(summary):
    try:
        st = summary["header"]["competitions"][0]["status"]["type"]
        return bool(st.get("completed")) and st.get("state") == "post"
    except (KeyError, IndexError, TypeError):
        return False


def actual_value(row, market, period):
    if row is None:
        return None
    if "h_" + "pa" not in row and period == "1H":
        return None
    return nflrows.actual(row, market, period)


def actual_text(market, period, val):
    if market == "anytime_td":
        return ("scored" if val else "no TD") + (" in the 1st half" if period == "1H" else "")
    return "had %d" % val


def grade(events, now, cfg, cache, store, ina, roster):
    """Returns (new events, notes, checks). Appends nothing itself.
    cache: schedule cache. store: stats.Store. ina: inactives state. roster: gsis -> player (espn_id)."""
    v, ops = cfg["values"], cfg["ops"]
    stake = v["stake"]
    at = common.iso(now)
    wait = timedelta(hours=ops.get("grade_wait_hours", 36))
    items = record.fold(events)
    new, notes = [], []
    by_game = {}
    for it in items.values():
        if it["kind"] == "single" and it["graded"] is None:
            by_game.setdefault(it["shown"]["game_id"], []).append(it)
    espn_to_gsis = {p.get("espn_id"): pid for pid, p in (roster or {}).items() if p.get("espn_id")}
    games = (cache or {}).get("games", {})
    for gid, its in sorted(by_game.items()):
        g = games.get(gid)
        start = common.parse_iso(its[0]["shown"]["start"])
        if g is None:
            notes.append("game %s not in the schedule" % gid)
            continue
        if now < start and not sched.is_postponed(g, cache):
            continue
        postponed = sched.is_postponed(g, cache)
        in_pbp = store.has_game(gid)
        final = in_pbp or sched.is_final(g, cache)
        box, box_rows = None, None
        if not postponed and not final:
            continue
        if not postponed and not in_pbp and g.get("espn"):
            box = common.feed_json("espn_summary", SUMMARY_URL % g["espn"], "espn_summary_%s.json" % g["espn"],
                                   timeout=30, retries=1)
            if box is not None and espn_final(box):
                box_rows = read_box(box, espn_to_gsis)
            else:
                box = None
        old = now - start >= wait
        game_rows = store.by_game.get(gid, {}) if in_pbp else {}
        snaps_in = any(r.get("snap") is not None for r in game_rows.values())
        for it in sorted(its, key=lambda x: x["id"]):
            sh = it["shown"]
            market, period = sh["market"].split("|")
            pre = []
            if record.is_open(it):
                pre.append({"t": "locked", "id": it["id"], "at": at, "state": it["state"]})
            price = record.worst_price(it)

            def void(text, note=None):
                ev = {"t": "graded", "id": it["id"], "at": at, "result": "VOID", "actual": None, "actual_text": text,
                      "price": price, "units": 0.0, "source": "schedule" if postponed else src}
                if note:
                    ev["note"] = note
                return pre + [ev]

            src = "nflverse" if in_pbp else "espn_box"
            if postponed:
                src = "schedule"
                new += void("game postponed")
                continue
            team_ina = ((ina or {}).get(gid) or {}).get(sh["team"]) or {}
            was_inactive = any(x.get("pid") == sh["player_id"] for x in team_ina.get("players", []))
            row = None
            if in_pbp:
                row = game_rows.get(sh["player_id"])
                if row is None:
                    if was_inactive or snaps_in:
                        new += void("did not play")
                    elif old:
                        new += void("did not play", "no row and no snap counts after %d hours" % (wait.total_seconds() // 3600))
                    else:
                        notes.append("%s: waiting for snap counts to confirm %s did not play" % (gid, sh["player"]))
                    continue
            else:
                if period != "FG" or market not in BOX_MARKETS or box_rows is None:
                    continue                                # wait for nflverse
                row = box_rows.get(sh["player_id"])
                if row is None:
                    if was_inactive:
                        new += void("did not play")
                    continue                                # no box line: played with no stats, or did not play
            val = actual_value(row, market, period)
            if val is None:
                continue
            if src == "espn_box" and market == "anytime_td" and val == 0 and not old:
                continue                                    # a fumble-recovery TD is not in the box: wait
            res = result_for(sh["side"], sh["line"], val)
            new += pre + [{"t": "graded", "id": it["id"], "at": at, "result": res, "actual": val,
                           "actual_text": actual_text(market, period, val), "price": price,
                           "units": record.single_units(res, price, stake), "source": src}]

    # parlays: graded when every leg is graded
    items = record.fold(events + new)
    for it in sorted(items.values(), key=lambda x: x["id"]):
        if it["kind"] != "parlay" or it["graded"] is not None:
            continue
        legs = [items.get(l) for l in it["shown"]["legs"]]
        if any(l is None or l["graded"] is None for l in legs):
            continue
        snap = record.parlay_snapshot(it)
        leg_price = lambda l: snap["legs"][l["id"]] if snap and l["id"] in snap["legs"] else l["graded"]["price"]
        res, price, units = record.grade_parlay([(l["graded"]["result"], leg_price(l)) for l in legs], stake)
        hits = sum(1 for l in legs if l["graded"]["result"] == "W")
        counted = sum(1 for l in legs if l["graded"]["result"] in ("W", "L"))
        pre = []
        if record.is_open(it):
            pre.append({"t": "locked", "id": it["id"], "at": at, "state": it["state"]})
        new += pre + [{"t": "graded", "id": it["id"], "at": at, "result": res, "actual": hits,
                       "actual_text": "%d of %d legs hit" % (hits, counted) if counted else "no legs counted",
                       "price": price if price is not None else record.last_price(it), "units": units,
                       "legs": [{"id": l["id"], "result": l["graded"]["result"]} for l in legs]}]
    return new, notes


def cross_check(events, store, days=14):
    """ESPN-graded picks whose nflverse result differs (the Record is never changed; this is for the team)."""
    out = []
    for it in record.fold(events).values():
        g = it["graded"]
        if it["kind"] != "single" or not g or g.get("result") in ("VOID",) or not store.has_game(it["shown"]["game_id"]):
            continue
        # graded events keep their source in the raw log; fold() keeps only known keys, so look it up
        out.append(it)
    raw = {e["id"]: e for e in events if e["t"] == "graded"}
    checks = []
    for it in out:
        ev = raw.get(it["id"]) or {}
        if ev.get("source") != "espn_box":
            continue
        sh = it["shown"]
        market, period = sh["market"].split("|")
        row = store.by_game.get(sh["game_id"], {}).get(sh["player_id"])
        val = nflrows.actual(row, market, period) if row else None
        if val is None:
            continue
        res = result_for(sh["side"], sh["line"], val)
        if res != ev.get("result"):
            checks.append({"id": it["id"], "espn": ev.get("actual"), "nflverse": val, "graded": ev.get("result"),
                           "nflverse_result": res})
    return checks
