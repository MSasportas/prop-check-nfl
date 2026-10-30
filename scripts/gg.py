"""gg.py: the Gap + Guard engine, the ONLY thing that decides what is a pick (ported from Prop Check NHL's paper.py).

A flag is one side of one Hard Rock main line (full game, one of ops.gap_guard.markets) whose price beats the other books'
consensus by enough (STEP 1, the gap) and that our model does not contradict (STEP 2, the guard: the model can only veto,
never create). A flag is frozen at the price of the first read that passes. engine.py turns each flag into a pick in the
Record (shown event, era "gg1") and looks after it afterwards (pulls, locks); nothing else creates a pick.

Everything is in EV% = fair_prob x Hard Rock decimal payout - 1, on unrounded floats, every threshold inclusive (>=).
  STEP 1 (gap) passes iff ALL: (a) EV of Hard Rock's price against the market fair >= gap_ev (+3.0%); (b) >= books_min (4)
    OTHER books priced the same player / line with both sides (Pinnacle is one of them); (c) market fair = median of those
    books' proportionally de-vigged fair probability for the side (even count: mean of the two middle values); (d) Pinnacle
    present with both sides AND Pinnacle's own fair EV on Hard Rock's price >= pin_ev_min (0.0%, sign check only);
    (e) a half-point line (whole-number lines can push and are skipped); (f) Hard Rock price price_gate .. max_price
    (-150 .. +120). A book counts only if it returned fresh, two-sided prices with a sane hold (other_lines()).
    The national "hardrockbet" key is the same operator as hardrockbet_fl and never counts as an other book.
  STEP 2 (guard): final = market_weight x market_fair + model_weight x model side chance, where the model side chance is the
    model's chance for the side minus the DS shrink for that market/side (minus jump_penalty on an over after a role jump).
    Passes iff final x decimal - 1 >= final_ev (+2.0%). No model for him = no pick.
Reads: only R (after the final injury report counts as in) and C (pre-kickoff) reads that asked other books can flag, and
only a read taken at or after injuries.report_counts_at(game): nothing is ever a pick before the final injury report. A new
pick comes from the NEWEST such read of the game (and only while it is fresh, max_read_age_min). Later reads can only PULL.
Research log data/gg/events.jsonl (append-only, never the Record): veto (passed the gap, stopped by the model), capped, and
close (the closing consensus on the C read, for CLV). This module never imports record's append functions and never writes
under data/record/.
"""
import json
import os
from datetime import timedelta

import common
import guard_record
import injuries
import odds

EPS = 1e-9
VERSION = 1
PINNACLE = "pinnacle"
ERA = "gg1"


# ---------------------------------------------------------------- config
def P(cfg):
    return (cfg.get("ops") or {}).get("gap_guard") or {}


def T(cfg):
    return P(cfg).get("thresholds") or {}


def era(cfg):
    return P(cfg).get("era") or ERA


def active(cfg, slate):
    p = P(cfg)
    return bool(p.get("active")) and str(slate) >= str(p.get("live_from") or "")


def market_names(cfg):
    """Our market names the Gap + Guard rule picks: ops.gap_guard.markets that are picks=true in ops.markets."""
    ms = cfg["ops"]["markets"]
    return [m for m in P(cfg).get("markets", []) if (ms.get(m) or {}).get("picks")]


# ---------------------------------------------------------------- the log (research only)
def events_path():
    return common.dpath("gg", "events.jsonl")


def read_raw():
    try:
        with open(events_path(), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b""


def read_events():
    out = []
    for line in read_raw().decode("utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def append(events):
    """Append events to the research log. Append-only: the old bytes must be an exact prefix of the new ones (the same test
    the Record guard uses), then one atomic write."""
    if not events:
        return 0
    old = read_raw()
    add = "".join(common.dumps(e, False) + "\n" for e in events).encode("utf-8")
    new = old + add
    why = guard_record.check(old, new)
    if why:
        raise RuntimeError("gg log guard: " + why)
    common.write_bytes(events_path(), new)
    return len(events)


def fold(events):
    """{id: {"veto"/"capped"/"close": event}} (first of each kind wins)."""
    items = {}
    for e in events:
        items.setdefault(e["id"], {}).setdefault(e["t"], e)
    return items


# ---------------------------------------------------------------- the arithmetic (pure)
def fmt_line(line):
    return "%g" % line


def ev(prob, price):
    """EV per unit staked of a Hard Rock American price at a fair probability."""
    return prob * odds.decimal(price) - 1.0


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0)


def side_fair(fair_over, side):
    return fair_over if side == "over" else 1.0 - fair_over


def inside_gate(price, t):
    d = odds.decimal(price)
    return odds.decimal(t["price_gate"]) - EPS <= d <= odds.decimal(t["max_price"]) + EPS


def whole_line(line):
    return abs(line - round(line)) < 1e-9


def guard(market_fair, model_side, price, t):
    """Step 2 on numbers: (passes, final probability, final EV). model_side = the model's chance of the side, after shrink."""
    final = t["market_weight"] * market_fair + t["model_weight"] * model_side
    fe = ev(final, price)
    return fe >= t["final_ev"] - EPS, final, fe


def assess(side, line, price, entry, model_side_fn, t, guard_anyway=False):
    """Steps 1 and 2 for one side of one line at Hard Rock's `price`.
    entry: other_lines()'s entry for this player + line (None = no other book has it).
    model_side_fn: () -> the model's chance of THIS side after shrink, or None (only called when step 1 passes, or with
    guard_anyway when only the gap line failed: used to judge a flag already alive).
    Returns a dict with every number (unrounded), `step1`, and `guard` (None when step 1 failed or there is no model)."""
    pairs = (entry or {}).get("pairs") or {}
    fairs = {b: side_fair(odds.fair_two_sided(o, u), side) for b, (o, u) in pairs.items()}
    out = {"side": side, "line": line, "price": price, "n_books": len(fairs), "books": sorted(fairs),
           "market_fair": None, "gap_ev": None, "pin_fair": None, "pin_ev": None, "model_side": None, "final": None,
           "final_ev": None, "step1": False, "guard": None, "fails": []}
    if t.get("skip_whole_lines", True) and whole_line(line):
        out["fails"].append("line")
    if not inside_gate(price, t):
        out["fails"].append("price")
    if len(fairs) < t["books_min"]:
        out["fails"].append("books")
    if fairs:
        out["market_fair"] = median(fairs.values())
        out["gap_ev"] = ev(out["market_fair"], price)
        if out["gap_ev"] < t["gap_ev"] - EPS:
            out["fails"].append("gap")
    else:
        out["fails"].append("gap")
    if PINNACLE not in fairs:
        out["fails"].append("pinnacle")
    else:
        out["pin_fair"] = fairs[PINNACLE]
        out["pin_ev"] = ev(out["pin_fair"], price)
        if out["pin_ev"] < t["pin_ev_min"] - EPS:
            out["fails"].append("pinnacle")
    out["step1"] = not out["fails"]
    if out["step1"] or (guard_anyway and out["market_fair"] is not None):
        ms = model_side_fn()
        if ms is not None:
            ok, final, fe = guard(out["market_fair"], ms, price, t)
            out.update(model_side=ms, final=final, final_ev=fe, guard=ok)
    return out


# ---------------------------------------------------------------- reads
def minutes_to_kick(f, game):
    return (common.parse_iso(game["start"]) - common.parse_iso(f["at"])).total_seconds() / 60.0


def can_flag(f, game, cfg):
    """An R or C read, taken before kickoff and at or after the final injury report counts as in, that asked other books
    and got Hard Rock's book back. A read with no "others" key (Hard Rock-only) is never used."""
    return f.get("pull") in T(cfg)["flag_pulls"] and "others" in f and bool(f.get("book")) \
        and 0 < minutes_to_kick(f, game) and common.parse_iso(f["at"]) >= injuries.report_counts_at(game, cfg)


def hr_market(f, mname, cfg):
    """Hard Rock's main-line pairs for one market on one read, with the feed checks (why = None when usable).
    None when this read did not return the market."""
    feed = cfg["ops"]["markets"][mname]["feed_key"]
    mk = (f.get("props") or {}).get(feed)
    if mk is None or mk.get("alt"):
        return None
    return odds.checked_pairs(mk, common.parse_iso(f["at"]), cfg["values"])


def other_lines(f, mname, cfg):
    """From one read's "others": {(pid, line): {"pairs": {book: (over, under)}, "best": {side: (price, book)}, "stale": n}}.
    Matched players only; a book's market is skipped when its own last_update is stale at read time; a pair counts only with
    both sides and a hold inside cons_hold_min..cons_hold_max. None when the read did not ask other books."""
    if "others" not in f:
        return None
    t, v, ops = T(cfg), cfg["values"], cfg["ops"]
    at = common.parse_iso(f["at"])
    feed = ops["markets"][mname]["feed_key"]
    never = set(ops.get("never_book") or ())
    lo, hi = t.get("cons_hold_min", 0.0), t.get("cons_hold_max", 0.15)
    out = {}
    for book, mks in sorted((f.get("others") or {}).items()):
        if book == ops["book"] or book in never:
            continue
        mk = (mks or {}).get(feed)
        if not mk:
            continue
        pairs = odds.pairs_of(mk.get("outcomes"))
        if odds.is_stale(mk.get("last_update"), at, v, "stale_min_others"):
            for key in pairs:
                out.setdefault(key, {"pairs": {}, "best": {}, "stale": 0})["stale"] += 1
            continue
        for key, p in pairs.items():
            e = out.setdefault(key, {"pairs": {}, "best": {}, "stale": 0})
            for side in ("over", "under"):
                pr = p[side]
                if pr is not None and (side not in e["best"] or odds.decimal(pr) > odds.decimal(e["best"][side][0])):
                    e["best"][side] = (pr, book)
            if p["over"] is not None and p["under"] is not None and lo <= odds.hold(p["over"], p["under"]) <= hi:
                e["pairs"][book] = (p["over"], p["under"])
    return out


def market_fair_in(others, key, side):
    """(median fair for the side, books) from other_lines()'s dict for key (pid, line)."""
    pairs = ((others or {}).get(key) or {}).get("pairs") or {}
    if not pairs:
        return None, 0
    return median([side_fair(odds.fair_two_sided(o, u), side) for o, u in pairs.values()]), len(pairs)


def pick_id(slate, gid, pid, mname, line, side):
    return "%s:%s:%s:%s:%s:%s" % (slate, gid, pid, mname, fmt_line(line), side)


def reads_by_game(fetches, now):
    out = {}
    for f in sorted(fetches or [], key=lambda x: x["at"]):
        if f.get("game_id") is not None and common.parse_iso(f["at"]) <= now:
            out.setdefault(f["game_id"], []).append(f)
    return out


def newest_read(reads, game, cfg):
    """The newest read of this game that can be used to look after a pick (an R or C read with others and Hard Rock)."""
    ok = [f for f in reads if can_flag(f, game, cfg)]
    return ok[-1] if ok else None


# ---------------------------------------------------------------- pulling a flag that is alive
def judge_alive(side, line, price_now, frozen_price, entry, model_side_fn, t):
    """-> (pull (code, sentence) or None, assessment at the current price). The flag keeps its frozen price; a read can only
    take it back. Order (the spec's): price outside the customer range, price moved + gap under the hysteresis line, books,
    Pinnacle, the guard. All judged at Hard Rock's price on this read."""
    a = assess(side, line, price_now, entry, model_side_fn, t, guard_anyway=True)
    n = a["n_books"]
    if not inside_gate(price_now, t):
        if odds.decimal(price_now) < odds.decimal(t["price_gate"]):
            return ("gate", "Hard Rock moved it to %s, past our %s limit." % (
                common.fmt_price(price_now), common.fmt_price(t["price_gate"]))), a
        return ("max_price", "Hard Rock moved it to %s, longer than we take." % common.fmt_price(price_now)), a
    if price_now != frozen_price and a["gap_ev"] is not None and a["gap_ev"] < t["pull_ev"] - EPS:
        return ("gap_gone", "Hard Rock moved it to %s and it no longer beats the other books." % common.fmt_price(price_now)), a
    if n < t["books_min"]:
        return ("books", "Fewer than %d other books quote this line now." % t["books_min"]), a
    if a["pin_fair"] is None:
        return ("pinnacle", "Pinnacle no longer quotes this line."), a
    if a["pin_ev"] < t["pin_ev_min"] - EPS:
        return ("pinnacle", "Pinnacle's price now says Hard Rock's number is no longer better than the market."), a
    if a["guard"] is False:
        return ("guard", "Our model now disagrees with the market on this one."), a
    return None, a


def at_your_price(side, line, frozen_price, entry, model_side_fn, t):
    """For someone who already bet at the frozen price: the final EV and the gap EV of the FROZEN price on this read's market
    (same maths as step 2). None when no other book prices the line."""
    a = assess(side, line, frozen_price, entry, model_side_fn, t, guard_anyway=True)
    return {"yours_ev": a.get("final_ev"), "yours_gap_ev": a.get("gap_ev")}


def if_bet_text(code, frozen_price, now_price, yours, status=None):
    """One plain sentence for someone who already bet the pick before it was pulled. No cash-out advice."""
    fp = common.fmt_price(frozen_price)
    if code in ("inactive", "injury"):
        who = "He is %s." % (("listed " + status.lower()) if status and code == "injury" else "inactive")
        return "If you already bet it at %s: %s Hard Rock normally voids a prop if the player does not play; check your slip." % (fp, who)
    if code == "wind":
        return "If you already bet it at %s: it stands. The wind forecast got worse, which hurts passing and receiving overs." % fp
    if code in ("line_moved", "line_gone", "market_gone"):
        return "If you already bet it at %s: your bet stands at that number. Hard Rock no longer offers this line." % fp
    ev_ = (yours or {}).get("yours_ev")
    if ev_ is None:
        part = "we cannot price it against the market any more"
    elif ev_ >= 0:
        part = "against the market now it is still a fair-or-better bet (%+.1f%%)" % (100 * ev_)
    else:
        part = "against the market now it is worse than fair (%+.1f%%)" % (100 * ev_)
    nowp = "Hard Rock is now %s; " % common.fmt_price(now_price) if now_price is not None else ""
    return "If you already bet it at %s: %s%s." % (fp, nowp, part)


# ---------------------------------------------------------------- finding new flags
def cap_reason(cand, mine, t, gid, team):
    """mine: gg1 singles of this slate already posted (open, pulled or locked; a line-move pull does not count)."""
    if sum(1 for x in mine if x["pid"] == cand["pid"]) >= t["max_per_player"]:
        return "player"
    if t.get("max_per_team") is not None and sum(1 for x in mine if x["game_id"] == gid and x["team"] == team) >= t["max_per_team"]:
        return "team"
    if t.get("max_per_game") is not None and sum(1 for x in mine if x["game_id"] == gid) >= t["max_per_game"]:
        return "game"
    return None


def find_flags(inp, eng, by_game, posted, log_items, allow_new=True):
    """Everything new this run. inp: engine.Inputs. eng: the engine module (pre-price vetoes and the model's side chance live
    there). by_game: reads_by_game(). posted: ids already in the Record (any state); mine: list of dicts (pid, game_id, team) for
    this slate's era picks that count toward the caps. Returns dict(flags, vetoes, capped, blocked {reason: n}, considered)."""
    cfg, now, slate = inp.cfg, inp.now, inp.slate
    t = T(cfg)
    out = {"flags": [], "vetoes": [], "capped": [], "blocked": {}, "considered": 0}
    if not active(cfg, slate) or not allow_new:
        return out
    mine = list(posted["mine"])
    passed = []
    for gid, g in sorted(inp.games.items(), key=lambda kv: (kv[1]["start"], kv[0])):
        if inp.started(g):
            continue
        f = newest_read(by_game.get(gid, []), g, cfg)
        if f is None:
            continue
        if now - common.parse_iso(f["at"]) > timedelta(minutes=t["max_read_age_min"]):
            out["blocked"]["read too old"] = out["blocked"].get("read too old", 0) + 1
            continue
        for mname in market_names(cfg):
            pairs = hr_market(f, mname, cfg)
            others = other_lines(f, mname, cfg)
            if not pairs or not others:
                continue
            for (pid, line), pair in sorted(pairs.items(), key=lambda kv: (kv[0][0], kv[0][1])):
                if pair["over"] is None or pair["under"] is None or pair["why"] is not None:
                    continue
                team = pair.get("team")
                if team not in (g["away"], g["home"]) or team in inp.behind:
                    continue
                if t.get("skip_whole_lines", True) and whole_line(line):
                    continue
                entry = others.get((pid, line))
                if not entry or len(entry["pairs"]) < t["books_min"]:
                    continue                                    # cheap exit before the model runs
                settled = inp.settled(g, team)
                if settled == "early":
                    continue                                    # nothing before the final injury report counts
                for side in ("over", "under"):
                    i = pick_id(slate, gid, pid, mname, line, side)
                    if i in posted["ids"]:
                        continue
                    state = {}

                    def model_side():
                        pre = eng.pre_check(inp, g, mname, pid, line, pair, side, settled)
                        state["pre"] = pre
                        if pre["reason"]:
                            return None
                        return eng.model_side(inp, pre["mm"], side, mname)

                    a = assess(side, line, pair[side], entry, model_side, t)
                    if not a["step1"]:
                        continue
                    out["considered"] += 1
                    pre = state.get("pre")
                    if pre and pre["reason"]:
                        k = pre["reason"].split(":")[0][:40]
                        out["blocked"][k] = out["blocked"].get(k, 0) + 1
                        continue
                    if a["guard"] is None:
                        out["blocked"]["no model"] = out["blocked"].get("no model", 0) + 1
                        continue
                    a.update(id=i, pid=pid, team=team, name=pair["name"], game=g, mname=mname, read=f, settled=settled,
                             mm=pre["mm"], market_label=cfg["ops"]["markets"][mname]["label"])
                    if not a["guard"]:
                        if "veto" not in log_items.get(i, {}):
                            out["vetoes"].append(a)
                        continue
                    passed.append(a)
    passed.sort(key=lambda a: (-a["final_ev"], a["id"]))
    for a in passed:
        why = cap_reason(a, mine, t, a["game"]["id"], a["team"])
        if why:
            if "capped" not in log_items.get(a["id"], {}):
                a["cap"] = why
                out["capped"].append(a)
            continue
        out["flags"].append(a)
        mine.append({"pid": a["pid"], "game_id": a["game"]["id"], "team": a["team"]})
    return out


# ---------------------------------------------------------------- the research log rows
def log_event(kind, a, now, **extra):
    g, f = a["game"], a["read"]
    e = {"t": kind, "id": a["id"], "at": common.iso(now), "v": VERSION, "slate": a.get("slate"), "game_id": g["id"],
         "pid": a["pid"], "player": a["name"], "team": a["team"], "market": a["mname"], "line": a["line"],
         "side": a["side"], "read_at": f["at"], "read_pull": f["pull"], "price": a["price"],
         "market_fair": a["market_fair"], "n_books": a["n_books"], "books": a["books"], "pin_fair": a["pin_fair"],
         "pin_ev": a["pin_ev"], "gap_ev": a["gap_ev"], "model_side": a["model_side"], "final": a["final"],
         "final_ev": a["final_ev"]}
    e.update(extra)
    return e


def close_events(inp, items, log_items, by_game, cfg, now):
    """The closing consensus (pull C read, before kickoff) for every posted pick and vetoed candidate of a game that has no
    close yet: the market's fair now against the frozen price = CLV. Research only."""
    out = []
    todo = []
    for i, it in items.items():
        sh = it["shown"]
        if it["kind"] == "single" and sh.get("era") == era(cfg) and "close" not in log_items.get(i, {}):
            todo.append((i, sh["game_id"], sh["market"], sh["player_id"], sh["line"], sh["side"], sh["price"],
                         sh.get("read_at") or it["shown_at"]))
    for i, d in log_items.items():
        b = d.get("veto")
        if b and "close" not in d:
            todo.append((i, b["game_id"], b["market"], b["pid"], b["line"], b["side"], b["price"], b["read_at"]))
    seen = set()
    for i, gid, mname, pid, line, side, price, read_at in todo:
        g = inp.games.get(gid)
        if g is None or i in seen:
            continue
        cs = [f for f in by_game.get(gid, []) if f.get("pull") == "C" and can_flag(f, g, cfg)]
        if not cs:
            continue
        c = cs[-1]
        if common.parse_iso(c["at"]) <= common.parse_iso(read_at):
            continue
        fair, n = market_fair_in(other_lines(c, mname, cfg), (pid, line), side)
        clv = None if fair is None else fair * odds.decimal(price) - 1.0
        seen.add(i)
        out.append({"t": "close", "id": i, "at": common.iso(now), "v": VERSION, "game_id": gid, "read_at": c["at"],
                    "close_fair": fair, "close_books": n, "clv": clv})
    return out


# ---------------------------------------------------------------- --explain (reads only, writes nothing)
def explain_rows(inp, eng, by_game):
    """Every Hard Rock main-line side on the newest usable read of each unstarted game with its Gap + Guard numbers and the
    first reason it did not pass. For people: `python scripts/run.py --explain`."""
    cfg, t = inp.cfg, T(inp.cfg)
    rows = []
    for gid, g in sorted(inp.games.items(), key=lambda kv: (kv[1]["start"], kv[0])):
        f = newest_read(by_game.get(gid, []), g, cfg)
        if f is None:
            continue
        for mname in market_names(cfg):
            pairs = hr_market(f, mname, cfg)
            others = other_lines(f, mname, cfg) or {}
            for (pid, line), pair in sorted((pairs or {}).items(), key=lambda kv: kv[1]["name"] or ""):
                for side in ("over", "under"):
                    row = {"player": pair["name"], "team": pair["team"], "market": mname, "line": line, "side": side,
                           "price": pair[side], "read": f["pull"], "n_books": 0, "market_fair": None, "gap_ev": None,
                           "final_ev": None}
                    if pair["over"] is None or pair["under"] is None or pair["why"] is not None:
                        row["verdict"] = "feed check: %s" % (pair["why"] or "one side missing")
                    elif pair["team"] in inp.behind:
                        row["verdict"] = "team excluded: stats behind"
                    else:
                        settled = inp.settled(g, pair["team"])
                        st = {}

                        def ms():
                            pre = eng.pre_check(inp, g, mname, pid, line, pair, side, settled)
                            st["pre"] = pre
                            return None if pre["reason"] else eng.model_side(inp, pre["mm"], side, mname)
                        a = assess(side, line, pair[side], others.get((pid, line)), ms, t, guard_anyway=True)
                        row.update(n_books=a["n_books"], market_fair=a["market_fair"], gap_ev=a["gap_ev"],
                                   final_ev=a["final_ev"])
                        pre = st.get("pre")
                        if pre and pre["reason"]:
                            row["verdict"] = "blocked: " + pre["reason"]
                        elif not a["step1"]:
                            row["verdict"] = "no gap: " + ", ".join(a["fails"])
                        elif a["guard"] is None:
                            row["verdict"] = "no model for him"
                        elif not a["guard"]:
                            row["verdict"] = "vetoed by our model"
                        elif settled == "early":
                            row["verdict"] = "waits for the final injury report"
                        else:
                            row["verdict"] = "PASSES BOTH CHECKS"
                    rows.append(row)
    return rows
