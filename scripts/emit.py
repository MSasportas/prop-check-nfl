"""emit.py: the page's files, exactly as /home/claude/work/ux/CONTRACT.md (v1) describes them.

  data/slate.json                built from the Record (so page and Record cannot disagree) + this run's facts
  data/rules.json                values + the "How picks are made" sentences generated from the values
  data/research/index.json       games and the player search list
  data/research/<game_id>.json   one per game this week (lines, injuries, inactives, defense, players + logs)
Plain words, no em dashes, Eastern times, null for missing.
"""
from datetime import timedelta

import common
import engine
import injuries
import model
import nflrows
import odds
import record
import sched
import teams

STAT_KEYS = ["pass_att", "pass_cmp", "pass_yds", "pass_td", "pass_int", "rush_att", "rush_yds", "targets", "rec",
             "rec_yds", "long_rec", "long_rush", "td"]


def statline(r, pre):
    g = lambda k: r.get(pre + k, 0) or 0
    return {"pass_att": g("pa"), "pass_cmp": g("pc"), "pass_yds": g("py"), "pass_td": g("ptd"), "pass_int": g("pi"),
            "rush_att": g("ra"), "rush_yds": g("ry"), "targets": g("tg"), "rec": g("rc"), "rec_yds": g("rcy"),
            "long_rec": g("lr"), "long_rush": g("lu"), "td": g("rt") + g("rct") + g("otd")}


def clean(s):
    return s.replace("—", ",").replace("–", "-") if isinstance(s, str) else s


# ---------------------------------------------------------------- slate.json
def history(it, cfg):
    out = [{"at": it["shown_at"], "text": "Posted at %s" % common.fmt_price(record.first_price(it))}]
    last_state = it["states"][0]["state"] if it["states"] else None
    for s in it["states"][1:]:
        if s["state"] != last_state:
            out.append({"at": s["at"], "text": engine.SETTLED_TEXT.get(s["state"], s["state"])})
            last_state = s["state"]
    for p in it["prices"][1:]:
        out.append({"at": p["at"], "text": "Price now %s" % common.fmt_price(p["price"])})
    if it["pulled"]:
        out.append({"at": it["pulled"]["at"], "text": "Pulled. %s" % it["pulled"]["reason"]})
    if it["locked_at"]:
        out.append({"at": it["locked_at"], "text": "Locked"})
    out.sort(key=lambda h: h["at"])
    return out


def pick_doc(it, rank, live_now, cfg):
    sh, g = it["shown"], it["graded"]
    lv = live_now.get(it["id"])
    started = it["locked_at"] is not None or it["graded"] is not None
    return {
        "id": it["id"], "rank": rank if (not started and not it["pulled"]) else None, "game_id": sh["game_id"],
        "day": sh["start"][:10], "kickoff": sh["start"], "player": sh.get("player"),
        "player_short": sh.get("player_short"), "player_id": sh.get("player_id"), "pos": sh.get("pos"),
        "team": sh["team"], "opp": sh["opp"], "home": sh["home"], "market": sh["market"], "period": sh["period"],
        "side": sh["side"], "line": sh["line"], "bet": sh["bet"], "book": "Hard Rock",
        "price": record.last_price(it),
        "price_at": common.iso(lv["price_at"]) if lv else it["prices"][-1]["at"],
        "first_price": record.first_price(it), "first_price_at": it["prices"][0]["at"],
        "grade_price": record.grade_price(it), "p": it["p"], "breakeven": it["breakeven"],
        "p_pct": it.get("p_pct") if it.get("p_pct") is not None else engine.pct_floor(it["p"]),
        "breakeven_pct": it.get("breakeven_pct") if it.get("breakeven_pct") is not None else engine.pct_round(it["breakeven"]),
        "reason": clean(it.get("reason")), "settled": it["state"], "state": "locked" if started else "open",
        "thin": bool(sh.get("thin")),
        "pulled": {"at": it["pulled"]["at"], "reason": it["pulled"]["reason"]} if it["pulled"] else None,
        "graded": {"result": g["result"], "actual": g.get("actual_text"), "units": g.get("units")} if g else None,
        "history": history(it, cfg),
    }


def parlay_doc(it, items):
    sh, g = it["shown"], it["graded"]
    legs = [items[l] for l in sh["legs"] if l in items]
    started = it["locked_at"] is not None or g is not None
    kick = min((l["shown"]["start"] for l in legs), default=sh.get("start"))
    pulled = None
    if it["pulled"]:
        pulled = {"at": it["pulled"]["at"], "reason": it["pulled"]["reason"],
                  "legs": it["pulled"].get("legs") or [l["id"] for l in legs if l["pulled"]]}
    return {"id": it["id"], "legs": sh["legs"], "n": len(sh["legs"]), "price": record.last_price(it),
            "price_at": it["prices"][-1]["at"], "first_price": record.first_price(it),
            "first_price_at": it["prices"][0]["at"], "grade_price": record.grade_price(it),
            "p": it["p"], "breakeven": it["breakeven"],
            "p_pct": it.get("p_pct") if it.get("p_pct") is not None else engine.pct_floor(it["p"]),
            "breakeven_pct": it.get("breakeven_pct") if it.get("breakeven_pct") is not None else engine.pct_round(it["breakeven"]),
            "kickoff": kick, "state": "locked" if started else "open", "pulled": pulled,
            "graded": {"result": g["result"], "actual": g.get("actual_text"), "units": g.get("units")} if g else None}


def game_doc(g, now, cache, view, settled, picks_count, props_state, note):
    lv = sched.live(cache, g["id"])
    final = sched.is_final(g, cache)
    score = None
    if final:
        a = lv.get("away_score") if lv.get("away_score") is not None else g.get("away_score")
        h = lv.get("home_score") if lv.get("home_score") is not None else g.get("home_score")
        if a is not None and h is not None:
            score = {"away": a, "home": h}
    gv = view.get(g["id"]) or {}
    return {"id": g["id"], "away": g["away"], "home": g["home"], "kickoff": g["start"], "day": g["date"],
            "roof": g.get("roof") or None, "started": sched.started(g, now, cache), "final": bool(final),
            "score": score, "settled": settled, "props_state": props_state,
            "props_at": common.iso(gv["props_at"]) if gv.get("props_at") else None,
            "lines_at": common.iso(gv["lines_at"]) if gv.get("lines_at") else None, "note": note,
            "picks": picks_count}


def build_slate(inp, events, extra, facts):
    """slate.json (CONTRACT section 1)."""
    cfg, now = inp.cfg, inp.now
    items = record.fold(events)
    week = {i: it for i, it in items.items() if it["slate"] == inp.slate}
    live = extra["live"] if extra else {}
    # rank within each day, best first, open picks only
    by_day = {}
    for i, x in live.items():
        by_day.setdefault(week[i]["shown"]["start"][:10] if i in week else "", []).append(i)
    ranks = {}
    for d, ids in by_day.items():
        ids.sort(key=lambda i: engine.rank_key(live[i]["p"], live[i]["b"], live[i]["settled"]) + (i,))
        for n, i in enumerate(ids):
            ranks[i] = n + 1
    picks = [pick_doc(it, ranks.get(i), live, cfg) for i, it in week.items() if it["kind"] == "single"]
    picks.sort(key=lambda p: (p["day"], p["rank"] or 999, p["kickoff"], p["id"]))
    parlays = [parlay_doc(it, items) for i, it in week.items() if it["kind"] == "parlay"]
    parlays.sort(key=lambda p: (p["state"] != "open", p["pulled"] is not None, -(p["p"] - p["breakeven"]), p["id"]))

    games = []
    for g in sorted(inp.games.values(), key=lambda x: (x["start"], x["id"])):
        st = min((inp.settled(g, t) for t in (g["away"], g["home"])), key=lambda s: engine.SETTLED_RANK[s])
        n = sum(1 for p in picks if p["game_id"] == g["id"] and not p["pulled"])
        ps, note = facts.get("props", {}).get(g["id"], ("none_yet", None))
        games.append(game_doc(g, now, inp.cache, inp.view, st, n, ps, note))

    banners = list(facts.get("banners", []))
    open_picks = [p for p in picks if p["state"] == "open" and not p["pulled"]]
    empty = None
    if not open_picks:
        empty = empty_state(inp, games, facts)
    season, week_no = facts.get("season"), facts.get("week")
    valid_until = facts.get("valid_until")
    return {"season": season, "week": week_no, "label": "Week %d" % week_no if week_no else "This week",
            "generated_at": common.iso(now), "status_at": common.iso(now), "valid_until": valid_until,
            "next_check": facts.get("next_check"), "status": facts["status"], "banners": banners, "empty": empty,
            "games": games, "picks": picks, "parlays": parlays}


def empty_state(inp, games, facts):
    wk = facts.get("week")
    label = "Week %d" % wk if wk else "this week"
    if not games:
        return {"code": "off_week", "text": "No NFL games this week.", "sub": None}
    if all(g["started"] for g in games):
        return {"code": "all_started", "text": "Every %s game has started." % label,
                "sub": "Week %d picks start showing up Tuesday." % (wk + 1) if wk else None}
    nxt = facts.get("next_check")
    sub = "Next check %s." % common.fmt_time(common.parse_iso(nxt)) if nxt else None
    if not any(g["props_state"] == "ok" for g in games if not g["started"]):
        return {"code": "no_picks_yet", "text": "No picks yet for %s. Hard Rock usually posts player props early in "
                                                "the week." % label, "sub": sub}
    return {"code": "no_picks_yet", "text": "No picks right now. Nothing cleared the bar at Hard Rock's prices.",
            "sub": sub}


# ---------------------------------------------------------------- rules.json
def rules_doc(cfg):
    v = cfg["values"]
    mk = cfg["ops"]["markets"]
    fg = [m["label"].lower() for k, m in mk.items() if m.get("picks") and m.get("period") == "FG"]
    h1 = [m["label"].replace("1H ", "").lower() for k, m in mk.items() if m.get("picks") and m.get("period") == "1H"]
    text = [
        "Every pick needs a real Hard Rock price of %s or better, up to %s. Plus money is fine." % (
            common.fmt_price(v["price_gate"]), common.fmt_price(v["max_price"])),
        "Full game markets we pick: %s." % ", ".join(fg),
        "1st half markets we pick: %s." % ", ".join(h1) if h1 else "No 1st half markets are picked right now.",
        "Our chance mixes our model (%d%%) with Hard Rock's own price (%d%%). The model's number is trimmed a little first, more for markets it reads less well." % (
            round(v["model_weight"] * 100), round(v["market_weight"] * 100)),
        "We only pick when our model gives at least %d%%, our chance is at least %d%%, and it beats the price's "
        "the price needs (what it takes to break even) by %d points early, %d once the injury report is in, %d once inactives are in." % (
            round(v["min_model"] * 100), round(v["min_p"] * 100), round(v["margin_early"] * 100),
            round(v["margin_injury"] * 100), round(v["margin_inactives"] * 100)),
        "No pick on a player listed questionable or worse at any point this week, or who missed his team's last game "
        "until the injury report clears him.",
        "No overs on a player with fewer than %d recent games, a new team, a big role change, or a new starting QB." %
        v["elig"]["min_games_over"],
        "No passing or deep-catch overs in wind of %d mph or more at kickoff." % (v.get("weather") or {}).get("wind_mph", 15),
        "At most %d pick per player, %d per team and %d per game." % (v["max_per_player"], v["max_per_team"],
                                                                     v["max_per_game"]),
        "1st half spreads show DraftKings numbers for reference. They are never picks.",
        "A pick is pulled if the player is ruled out or inactive, the price goes past %s, or the line moves or comes "
        "down." % common.fmt_price(v["price_gate"]),
        "Parlays are %d or %d legs from the top picks, each leg in a different game." % (v["parlay_min_legs"],
                                                                                         v["parlay_max_legs"]),
        "Picks lock at kickoff. Results come from the official stats once the game is final.",
        "The Record grades every pick at the worst Hard Rock price shown, 1 unit each. Pulled picks are graded too "
        "and shown as pulled.",
    ]
    vals = dict(v)
    vals.setdefault("min_sample", v.get("min_sample", 30))
    ui = dict(cfg["ui"])
    ui.setdefault("picks_open_per_day", 6)
    return {"version": cfg["version"], "updated": cfg["updated"], "values": vals, "text": [clean(t) for t in text],
            "ui": ui}


# ---------------------------------------------------------------- research
def lines_list(gv, g):
    out = []
    lines = (gv or {}).get("lines") or {}

    def spread(key, period, book):
        lm = lines.get(key)
        if not lm:
            return
        pair = odds.main_spread(lm["outcomes"], g["away"], g["home"])
        if not pair:
            return
        rows = list(pair)
        fav = min(rows, key=lambda o: o["point"])
        dog = [o for o in rows if o is not fav][0]
        out.append({"kind": "spread", "period": period, "book": book, "team": fav["side"], "line": fav["point"],
                    "price": fav["price"], "other_team": dog["side"], "other_line": dog["point"],
                    "other_price": dog["price"], "at": lm["at"]})

    def total(key, period, book):
        lm = lines.get(key)
        if not lm:
            return
        pair = odds.main_total(lm["outcomes"])
        o, u = pair if pair else (None, None)
        if o and u:
            out.append({"kind": "total", "period": period, "book": book, "line": o["point"], "over": o["price"],
                        "under": u["price"], "at": lm["at"]})

    spread("spreads", "FG", "Hard Rock")
    total("totals", "FG", "Hard Rock")
    ml = lines.get("h2h")
    if ml:
        a = next((o["price"] for o in ml["outcomes"] if o["side"] == g["away"]), None)
        h = next((o["price"] for o in ml["outcomes"] if o["side"] == g["home"]), None)
        if a is not None and h is not None:
            out.append({"kind": "moneyline", "period": "FG", "book": "Hard Rock", "away": a, "home": h, "at": ml["at"]})
    spread("spreads_h1@draftkings", "1H", "DraftKings")
    if not any(x["kind"] == "total" and x["period"] == "1H" for x in out):
        total("totals_h1", "1H", "Hard Rock")
    if not any(x["kind"] == "total" and x["period"] == "1H" for x in out):
        total("totals_h1@draftkings", "1H", "DraftKings")
    for key, period in (("team_totals", "FG"), ("team_totals_h1", "1H")):
        lm = lines.get(key)
        if not lm:
            continue
        for t in (g["away"], g["home"]):
            pair = odds.main_total(lm["outcomes"], "%s over" % t, "%s under" % t)
            o, u = pair if pair else (None, None)
            if o and u:
                out.append({"kind": "team_total", "period": period, "book": "Hard Rock", "team": t, "line": o["point"],
                            "over": o["price"], "under": u["price"], "at": lm["at"]})
    return out


DEF_KEYS = [("rec", "WR", "rc_WR"), ("rec_yds", "WR", "rcy_WR"), ("rec", "TE", "rc_TE"), ("rec_yds", "TE", "rcy_TE"),
            ("rec", "RB", "rc_RB"), ("rec_yds", "RB", "rcy_RB"), ("rush_yds", "RB", "ry_RB"), ("rush_att", "RB", "ra_RB"),
            ("pass_yds", "QB", "py"), ("pass_att", "QB", "pa")]


def defense_table(store, season, before):
    """{team: [rows]} what each defense allowed this season before `before` (CONTRACT: rank 1 = fewest)."""
    rows = [t for t in store.team_rows if t["season"] == season and t["date"] < before and t["stype"] == "REG"]
    allowed = {}
    for r in rows:
        for m, pos, col in DEF_KEYS:
            allowed.setdefault((m, pos, "FG"), {}).setdefault(r["opp"], []).append(r[col])
            allowed.setdefault((m, pos, "1H"), {}).setdefault(r["opp"], []).append(r["h_" + col])
    out = {}
    for (m, pos, per), by in allowed.items():
        avg = {t: sum(v) / float(len(v)) for t, v in by.items() if v}
        if not avg:
            continue
        order = sorted(avg, key=lambda t: (avg[t], t))
        lg = sum(avg.values()) / len(avg)
        for i, t in enumerate(order):
            out.setdefault(t, []).append({"market": m, "pos": pos, "period": per, "allowed_pg": round(avg[t], 1),
                                          "league_pg": round(lg, 1), "rank": i + 1, "of": len(order),
                                          "games": len(by[t])})
    return out


def player_log(store, pid, team, season, before, n=10):
    rows = store.player_rows(pid, before=before)
    by_gid = {r["gid"]: r for r in rows}
    out = []
    for t in store.team_games.get(team, []):
        if t["season"] != season or t["date"] >= before or t["stype"] != "REG":
            continue
        r = by_gid.get(t["gid"])
        if r is None:
            out.append({"season": season, "week": t["week"], "opp": t["opp"], "home": bool(t["home"]), "played": False})
        else:
            out.append({"season": season, "week": r["week"], "opp": r["opp"], "home": bool(r["home"]), "played": True,
                        "snap_pct": int(round(r["snap"] * 100)) if r.get("snap") is not None else None,
                        "fg": statline(r, ""), "h1": statline(r, "h_")})
    # games for another team earlier this season, then last season's tail
    this_team = {t["gid"] for t in store.team_games.get(team, [])}
    other = [r for r in rows if r["season"] == season and r["gid"] not in this_team]
    for r in other:
        out.append({"season": season, "week": r["week"], "opp": r["opp"], "home": bool(r["home"]), "played": True,
                    "snap_pct": int(round(r["snap"] * 100)) if r.get("snap") is not None else None,
                    "fg": statline(r, ""), "h1": statline(r, "h_")})
    out.sort(key=lambda x: (x["season"], x["week"]))
    need = n - len(out)
    prev = [r for r in rows if r["season"] == season - 1 and r["stype"] == "REG"][-need:] if need > 0 else []
    pre = [{"season": r["season"], "week": r["week"], "opp": r["opp"], "home": bool(r["home"]), "played": True,
            "snap_pct": int(round(r["snap"] * 100)) if r.get("snap") is not None else None,
            "fg": statline(r, ""), "h1": statline(r, "h_")} for r in prev]
    return list(reversed(pre + out))[:n]


def team_players(store, roster, team, season, before, with_lines, k=11):
    """QB/RB/WR/TE for a team ordered by role (chances this season), plus anyone with a Hard Rock line."""
    score = {}
    for t in store.team_games.get(team, []):
        if t["season"] != season or t["date"] >= before:
            continue
        for pid, r in store.by_game.get(t["gid"], {}).items():
            if r["team"] == team:
                score[pid] = score.get(pid, 0.0) + r["tg"] + r["ra"] + r["pa"] * 0.8
    on = {pid for pid, p in (roster or {}).items() if p.get("team") == team}
    # moved teams: only keep players still on this roster (when the roster is known)
    pids = [pid for pid in sorted(score, key=lambda p: -score[p]) if not on or pid in on][:k]
    for pid in with_lines:
        if pid not in pids:
            pids.append(pid)
    return pids


def build_research(inp, roster, slate_doc, facts):
    """-> (index doc, {game_id: game doc})."""
    cfg, now, store = inp.cfg, inp.now, inp.store
    season = inp.season
    picks_by_player = {}
    for p in slate_doc["picks"]:
        if p.get("player_id"):
            picks_by_player.setdefault(p["player_id"], []).append(p["id"])
    f2m = {}
    for k, m in cfg["ops"]["markets"].items():
        if m.get("feed_key"):
            f2m[k] = (m.get("model") or k, m.get("period", "FG"))
    weather = inp.weather
    games_out, files, players_idx = [], {}, []
    for g in sorted(inp.games.values(), key=lambda x: (x["start"], x["id"])):
        gv = inp.view.get(g["id"]) or {}
        lines = lines_list(gv, g)
        # player lines (main only)
        plines = {}
        for mkey, mv in (gv.get("props") or {}).items():
            if mkey not in f2m or mv.get("gone") or not mv.get("posted"):
                continue
            market, period = f2m[mkey]
            for (pid, line), pr in mv["pairs"].items():
                plines.setdefault(pid, []).append({"market": market, "period": period, "line": line,
                                                   "over": pr.get("over"), "under": pr.get("under"),
                                                   "at": common.iso(mv["at"])})
        n_props = sum(len(v) for v in plines.values())
        # injuries / inactives
        inj_list = []
        for key, p in ((inp.inj or {}).get("players") or {}).items():
            if p.get("team") not in (g["away"], g["home"]) or p.get("status") in (None, "", "Active"):
                continue
            st = p["status"]
            st = {"Injured Reserve": "IR", "Physically Unable to Perform": "PUP", "Suspension": "Suspended"}.get(st, st)
            inj_list.append({"team": p["team"], "player": p.get("name"), "player_id": None if key.startswith("name:") else key,
                             "pos": p.get("pos"), "status": st, "detail": p.get("detail")})
        order = {"Out": 0, "Doubtful": 1, "Questionable": 2}
        inj_list.sort(key=lambda x: (order.get(x["status"], 3), x["pos"] not in ("QB", "RB", "WR", "TE"), x["player"] or ""))
        ina_rec = (inp.ina or {}).get(g["id"]) or {}
        posted = all((ina_rec.get(t) or {}).get("confirmed") for t in (g["away"], g["home"]))
        ina_list = [{"team": t, "player": x.get("name"), "player_id": x.get("pid"), "pos": x.get("pos")}
                    for t in (g["away"], g["home"]) for x in (ina_rec.get(t) or {}).get("players", [])]
        ina_at = max([x.get("confirmed_at") for x in ina_rec.values() if isinstance(x, dict) and x.get("confirmed_at")] or [None],
                     key=lambda x: x or "")
        defense = defense_table(store, season, g["date"])
        players = []
        for team in (g["away"], g["home"]):
            opp = g["home"] if team == g["away"] else g["away"]
            with_lines = [pid for pid in plines if (inp.people.get(pid) or {}).get("team") == team or
                          any(True for (mk, mv) in (gv.get("props") or {}).items()
                              for (pp, _), pr in mv.get("pairs", {}).items() if pp == pid and pr.get("team") == team)]
            for pid in team_players(store, roster, team, season, g["date"], with_lines):
                pos = inp.pos(pid)
                if pos not in ("QB", "RB", "WR", "TE"):
                    continue
                full, short = inp.name_parts(pid, (store.players.get(pid) or [pid])[0])
                stt = inp.status(g, team, pid, full)
                det = (((inp.inj or {}).get("players") or {}).get(pid) or {}).get("detail")
                players.append({"id": pid, "name": full, "short": short, "pos": pos, "team": team, "opp": opp,
                                "status": stt, "status_detail": det if stt and stt != "Inactive" else None,
                                "lines": sorted(plines.get(pid, []), key=lambda x: (x["market"], x["period"], x["line"])),
                                "log": player_log(store, pid, team, season, g["date"]),
                                "picks": picks_by_player.get(pid, [])})
                players_idx.append([pid, full, pos, team, g["id"], len(plines.get(pid, []))])
        w = weather.get(g["id"]) or {}
        roof = g.get("roof") or None
        wdoc = None
        if w and w.get("roof") != "dome" and roof not in ("dome", "closed"):
            wdoc = {"temp_f": int(round(w["temp_f"])) if w.get("temp_f") is not None else None,
                    "wind_mph": int(round(w["wind_mph"])) if w.get("wind_mph") is not None else None,
                    "rain_pct": int(round(w["rain_pct"])) if w.get("rain_pct") is not None else None,
                    "text": w.get("text"), "at": w.get("at")}
        inj_state = "down" if facts.get("injuries_down") else ("ok" if (inp.inj or {}).get("at") else "not_yet")
        doc = {"id": g["id"], "generated_at": common.iso(now), "season": season, "week": g["week"], "away": g["away"],
               "home": g["home"], "kickoff": g["start"], "stadium": g.get("stadium"), "roof": roof, "weather": wdoc,
               "lines": lines,
               "injuries": {"state": inj_state, "at": (inp.inj or {}).get("at"), "list": inj_list},
               "inactives": {"posted": bool(posted), "at": ina_at, "list": ina_list},
               "defense": {g["away"]: defense.get(g["away"], []), g["home"]: defense.get(g["home"], [])},
               "players": players}
        files[g["id"]] = doc
        sp = next((x for x in lines if x["kind"] == "spread" and x["period"] == "FG"), None)
        tot = next((x for x in lines if x["kind"] == "total" and x["period"] == "FG"), None)
        games_out.append({"id": g["id"], "away": g["away"], "home": g["home"], "kickoff": g["start"], "day": g["date"],
                          "roof": roof, "spread": {"team": sp["team"], "line": sp["line"]} if sp else None,
                          "total": tot["line"] if tot else None, "file": "research/%s.json" % g["id"],
                          "updated_at": common.iso(now), "started": sched.started(g, now, inp.cache),
                          "final": bool(sched.is_final(g, inp.cache)),
                          "picks": sum(1 for p in slate_doc["picks"] if p["game_id"] == g["id"] and not p["pulled"]),
                          "props": n_props})
    seen, idx = set(), []
    for row in players_idx:
        if row[0] not in seen:
            seen.add(row[0])
            idx.append(row)
    index = {"generated_at": common.iso(now), "season": season, "week": slate_doc.get("week"), "games": games_out,
             "player_cols": ["id", "name", "pos", "team", "game_id", "lines"], "players": idx}
    return index, files
