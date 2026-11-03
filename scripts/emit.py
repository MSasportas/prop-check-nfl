"""emit.py: the page's files. The shape is documented in docs/SPEC.md (slate.json schema 2).

  data/slate.json   built from the Record (so page and Record cannot disagree) + this run's facts
  data/rules.json   values + the "How picks are made" sentences generated from the config
Plain words, no em dashes, Eastern times, null for missing. No parlays, no 1st half, no research documents.
"""
from datetime import timedelta

import common
import engine
import gg
import injuries
import odds
import record
import sched
import watch

SCHEMA = 2


def clean(s):
    return s.replace("\u2014", ",").replace("\u2013", "-") if isinstance(s, str) else s


# ---------------------------------------------------------------- slate.json
def history(it, cfg):
    out = [{"at": it["shown_at"], "text": "Posted at %s" % common.fmt_price(record.first_price(it))}]
    last_state = it["states"][0]["state"] if it["states"] else None
    for st in it["states"][1:]:
        if st["state"] != last_state:
            out.append({"at": st["at"], "text": engine.SETTLED_TEXT.get(st["state"], st["state"])})
            last_state = st["state"]
    if it["pulled"]:
        out.append({"at": it["pulled"]["at"], "text": "Pulled. %s" % it["pulled"]["reason"]})
    if it["locked_at"]:
        out.append({"at": it["locked_at"], "text": "Locked"})
    out.sort(key=lambda h: h["at"])
    return out


def vs_text(sh):
    """'+3.4% vs 5 books (Pinnacle in)'; for a Pinnacle-moves pick 'Pinnacle moved +3 pts · Pinnacle -126, Hard Rock -120'."""
    if sh.get("method") == "pin_solo":
        mv = round(100 * (sh["pin_after"] - sh["pin_before"]), 1)
        if sh.get("price_test") == "posted" and sh.get("pin_price") is not None:
            return "Pinnacle moved %+g pts · Pinnacle %s, %s %s" % (mv, common.fmt_price(sh["pin_price"]),
                                                                sh.get("book") or "Hard Rock", common.fmt_price(sh["price"]))
        if sh.get("price_test") == "fair" and sh.get("pin_fair_price") is not None:
            return "Pinnacle moved %+g pts · Pinnacle fair %s, %s %s" % (mv, common.fmt_price(sh["pin_fair_price"]),
                                                                     sh.get("book") or "Hard Rock", common.fmt_price(sh["price"]))
        return "Pinnacle %+g pts, other books %+g" % (mv, round(100 * (sh["others_move"] or 0.0), 1))
    return "%+.1f%% vs %d books (Pinnacle in)" % (100 * sh["gap_ev"], sh["n_books"])


def weather_flag(inp, gid):
    w = (inp.weather or {}).get(gid) or {}
    if w.get("roof") in ("dome", "closed", "indoors"):
        return {"state": "dome", "text": "Indoors"}
    if w.get("wind_mph") is not None:
        return {"state": "checked", "text": w.get("text"), "wind_mph": w.get("wind_mph")}
    return {"state": "not_checked", "text": None, "wind_mph": None}


def pick_doc(it, rank, inp, price_final):
    sh, g = it["shown"], it["graded"]
    started = it["locked_at"] is not None or it["graded"] is not None
    ps = sh.get("method") == "pin_solo"
    state = "pulled" if it["pulled"] else ("locked" if started else "open")
    pf = bool(price_final.get(sh["game_id"])) and state == "open"
    return {
        "id": it["id"], "era": it["era"], "rank": rank if state == "open" else None, "state": state,
        "game_id": sh["game_id"], "day": sh["start"][:10], "kickoff": sh["start"],
        "game": ("%s vs %s" if sh["home"] else "%s @ %s") % (sh["team"], sh["opp"]),
        "player": sh.get("player"), "player_short": sh.get("player_short"), "player_id": sh.get("player_id"),
        "pos": sh.get("pos"), "team": sh["team"], "opp": sh["opp"], "home": sh["home"],
        "market": sh["mkey"], "market_label": sh.get("market_label") or engine.market_label(inp.cfg, sh["market"], sh["period"]),
        "side": sh["side"], "line": sh["line"], "bet": sh["bet"], "book": sh.get("book") or "Hard Rock",
        "book_key": sh.get("book_key") or "hardrock", "link": sh.get("link"),
        "price": record.first_price(it), "read_at": sh.get("read_at") or it["shown_at"], "read_pull": sh.get("read_pull"),
        "method": sh.get("method") or "gap_guard",
        "vs": vs_text(sh), "gap_pct": None if ps else round(100 * sh["gap_ev"], 1), "n_books": sh["n_books"], "pinnacle": True,
        "final_ev_pct": None if ps else round(100 * sh["final_ev"], 1),
        "pin_solo": ({"window": sh.get("window"), "pin_move_pts": round(100 * (sh["pin_after"] - sh["pin_before"]), 1),
                      "others_move_pts": round(100 * (sh["others_move"] or 0.0), 1), "n_others": sh["n_others"],
                      "fair_price": sh.get("pin_fair_price"), "pin_price": sh.get("pin_price"), "price_test": sh.get("price_test"), "ev_vs_fair_pct": round(100 * sh["ev_vs_pin"], 1)}
                     if ps else None),
        "reason": clean(it.get("reason")), "settled": it["state"],
        "settled_text": engine.SETTLED_TEXT.get(it["state"], it["state"]),
        "flags": {"price_final": pf, "weather": weather_flag(inp, sh["game_id"])},
        "pulled": ({"at": it["pulled"]["at"], "reason": it["pulled"]["reason"], "code": it["pulled"].get("code"),
                    "if_bet": it["pulled"].get("if_bet"), "now_price": it["pulled"].get("now_price")}
                   if it["pulled"] else None),
        "graded": {"result": g["result"], "actual": g.get("actual_text"), "units": g.get("units")} if g else None,
        "history": history(it, inp.cfg),
    }


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
    started = sched.started(g, now, cache)
    if started and props_state != "ok":
        props_state, note = "closed", None          # kicked off: no "props not posted yet" or "next check" talk
    return {"id": g["id"], "away": g["away"], "home": g["home"], "kickoff": g["start"], "day": g["date"],
            "roof": g.get("roof") or None, "started": started, "final": bool(final),
            "score": score, "settled": settled, "props_state": props_state,
            "props_at": common.iso(gv["props_at"]) if gv.get("props_at") else None, "note": note, "picks": picks_count}


def build_slate(inp, events, extra, facts):
    """slate.json (docs/SPEC.md, schema 2): this week's new-era picks, best first within each game day."""
    cfg, now = inp.cfg, inp.now
    items = record.fold(events)
    cur = gg.era(cfg)
    eras = (cur,) + tuple(watch.ps_eras(cfg))           # Gap + Guard picks, and Pinnacle-moves picks (Hard Rock and DraftKings)
    week = {i: it for i, it in items.items() if it["slate"] == inp.slate and it["kind"] == "single" and it["era"] in eras}
    live = extra["live"] if extra else {}
    by_day = {}
    for i in live:
        if i in week:
            by_day.setdefault(week[i]["shown"]["start"][:10], []).append(i)
    ranks = {}
    for d, ids in by_day.items():
        ids.sort(key=lambda i: engine.rank_key(live[i]["final_ev"], live[i]["settled"], i))
        for n, i in enumerate(ids):
            ranks[i] = n + 1
    price_final = facts.get("price_final") or {}
    picks = [pick_doc(it, ranks.get(i), inp, price_final) for i, it in week.items()]
    picks.sort(key=lambda p: (p["day"], p["rank"] or 999, p["kickoff"], p["id"]))
    games = []
    for g in sorted(inp.games.values(), key=lambda x: (x["start"], x["id"])):
        st = min((inp.settled(g, t) for t in (g["away"], g["home"])), key=lambda s: engine.SETTLED_RANK[s])
        n = sum(1 for p in picks if p["game_id"] == g["id"] and p["state"] != "pulled")
        ps, note = facts.get("props", {}).get(g["id"], ("none_yet", None))
        games.append(game_doc(g, now, inp.cache, inp.view, st, n, ps, note))
    banners = list(facts.get("banners", []))
    open_picks = [p for p in picks if p["state"] == "open"]
    empty = None if open_picks else empty_state(inp, games, facts)
    season, week_no = facts.get("season"), facts.get("week")
    return {"schema": SCHEMA, "era": cur, "methods": {"gap_guard": {"era": cur, "live": True},
                                                      "pin_solo": {"era": watch.ps_era(cfg), "live": watch.pin_live(cfg, inp.slate)},
                                                      "draftkings": {"era": watch.dk_era(cfg),
                                                                     "live": watch.pin_live(cfg, inp.slate) and watch.dk_live(cfg)}},
            "season": season, "week": week_no,
            "label": "Week %d" % week_no if week_no else "This week",
            "generated_at": common.iso(now), "status_at": common.iso(now), "valid_until": facts.get("valid_until"),
            "next_check": facts.get("next_check"), "status": facts["status"], "banners": banners, "empty": empty,
            "games": games, "picks": picks}


def empty_state(inp, games, facts):
    wk = facts.get("week")
    label = "Week %d" % wk if wk else "this week"
    if not games:
        return {"code": "off_week", "text": "No NFL games this week.", "sub": None}
    if all(g["started"] for g in games):
        return {"code": "all_started", "text": "Every %s game has started." % label,
                "sub": "Week %d picks start showing up after the final injury report." % (wk + 1) if wk else None}
    nxt = facts.get("next_check")
    sub = "Next check %s." % common.fmt_time(common.parse_iso(nxt)) if nxt else None
    if not any(g["props_state"] == "ok" for g in games if not g["started"]):
        return {"code": "no_picks_yet", "text": "No picks yet for %s. Picks only come after the final injury report." % label,
                "sub": sub}
    return {"code": "no_picks_yet", "text": "No picks right now. Nothing beats the other books by enough at Hard Rock's prices.",
            "sub": sub}


# ---------------------------------------------------------------- rules.json
def rules_doc(cfg):
    v, t = cfg["values"], gg.T(cfg)
    mk = cfg["ops"]["markets"]
    picked = [mk[m]["label"].lower() for m in gg.market_names(cfg)]
    text = [
        "Every pick is a full game player prop at Hard Rock (Florida), main line only, priced %s or better and no longer than %s. "
        "No parlays, no 1st half, no alternate lines." % (common.fmt_price(t["price_gate"]), common.fmt_price(t["max_price"])),
        "Markets we pick: %s." % ", ".join(picked),
        "Hard Rock's price must beat what the other books say is fair (their prices with the vig removed, the middle one) by "
        "%.0f%% or more. At least %d other books must quote the same line, and Pinnacle must be one and must not disagree."
        % (100 * t["gap_ev"], t["books_min"]),
        "Then our model gets a say: it can only veto. It counts for %d%% of the final number, and the pick must still be worth "
        "at least %.0f%% on it." % (round(100 * t["model_weight"]), 100 * t["final_ev"]),
        "A pick appears only after the final injury report is in. Its price is frozen at the first check that passes.",
        "No pick on a player listed questionable or worse at any point this week, or who missed his team's last game "
        "until the injury report clears him.",
        "No overs on a player with fewer than %d recent games, a new team, or a new starting QB." % v["elig"]["min_games_over"],
        "No pick either way on a player whose role swings a lot from game to game."
        if v["elig"].get("role_cv_blocks") == "both" else "No overs on a player whose role swings a lot from game to game.",
        "When a receiver or back with a big share of his team's targets or carries joins or leaves a team, that "
        "team's receiving or rushing picks wait %d games." % (v.get("room_change") or {}).get("team_games_after", 2)
        if v.get("room_change") else None,
        "When a teammate with a big share of the targets or carries is out, that room's picks wait for inactives.",
        "No passing or receiving-yard overs in wind of %d mph or more at kickoff." % (v.get("weather") or {}).get("wind_mph", 15),
        "At most %d pick per player." % t["max_per_player"],
        "A pick is pulled if the player is ruled out or inactive, Hard Rock moves the line or takes it down, the price goes "
        "past %s, or the market stops agreeing with it." % common.fmt_price(t["price_gate"]),
        ("Pinnacle-moves picks: we read the prices 24 hours, 3 hours and 70 minutes before kickoff. A pick appears when "
         "Pinnacle's chance moves %g to under %g points toward a side between two reads and Hard Rock's price %s. Tagged "
         "Pinnacle moved; counted on its own line. Still one pick per player. The same rule is also checked at DraftKings: a "
         "DraftKings pick is marked DraftKings and kept on its own Record line."
         % (100 * watch.tcfg(cfg, watch.SOLO).get("min_move", 0.01), 100 * (watch.tcfg(cfg, watch.SOLO).get("max_move") or 1),
            "is at or better than Pinnacle's posted price" if watch.tcfg(cfg, watch.SOLO).get("price_test") == "posted"
            else "beats Pinnacle's fair (no-vig) price"))
        if watch.pin_live(cfg, "9999") else None,
        "Picks lock at kickoff. Results come from the official stats once the game is final.",
        "The Record grades every pick at the Hard Rock price it was posted at, 1 unit each. Pulled picks are graded too and "
        "shown as pulled.",
    ]
    ui = dict(cfg["ui"])
    return {"version": cfg["version"], "updated": cfg["updated"], "values": dict(v), "gap_guard": gg.P(cfg),
            "text": [clean(x) for x in text if x], "ui": ui}
