"""watch.py: behind-the-scenes research tracks (TEAM.md section 4, "Behind the scenes"). Ported from Prop Check NBA
(scripts/watch.py + engine.solo_moves). Research only: NEVER a pick on the Picks tab, never the Record, never era gg1.
This module does not import record's append functions and writes only under data/watch/.

Two tracks, both on Hard Rock's full-game main lines of the same six markets as the live rule, both priced at the live
window (gg.T price_gate .. max_price), both graded at the price frozen when they were flagged (flat 1 unit):

  pin_solo   "Pinnacle moves alone". Between two consecutive usable reads a -> b of one game (R, W or C taken at or after the
             final injury report counts in, before kickoff, asking the other books): Hard Rock quotes the same line on BOTH
             reads; Pinnacle quotes the same line on both and its de-vigged chance moves >= min_move (2 pts) toward a side;
             the median move toward that side of the other books (same line, both reads, >= others_min of them, hold 0-15%,
             the national 'hardrockbet' key never) is < others_max (2 pts); AND Hard Rock's price on that side is at or
             better than Pinnacle's NEW fair price (Pinnacle's chance x Hard Rock payout - 1 >= min_ev_vs_pin, 0.0).
             Candidate = that side at Hard Rock's price on read b. Same eligibility vetoes as the live engine
             (engine.pre_check: injury status, QB, stats behind, wind, eligibility, room change, key teammate out).
  pin_judge  "Pinnacle as the price judge". The newest read of an unstarted game: Hard Rock's price has EV >= min_ev (+2%)
             against Pinnacle's de-vigged fair on the same line, price in the window, eligible. First flag per
             player / market / game.

Every Pinnacle move (pin_solo) and every side at EV >= min_ev (pin_judge) is logged as a `cand` with the gate that stopped
it (or none = flagged), so the page can say how often each option would have fired.

Files (all under data/watch/): events.jsonl (append-only, guarded the same way as data/gg/events.jsonl: the old bytes must be
an exact prefix of the new ones) and summary.json (rebuilt from the log every run; docs/SPEC.md section 10).
Event types: pair (a pair of reads was scanned), scan (a read was scanned), cand, flag, close (CLV), graded.
"""
import json
import os
import statistics

import common
import gg
import guard_record
import injuries
import odds

EPS = 1e-9
VERSION = 1
SOLO, JUDGE = "pin_solo", "pin_judge"
TRACKS = (SOLO, JUDGE)

GATES = {
    "hr_line": "Hard Rock did not quote this line on both reads",
    "hr_feed": "Hard Rock price failed the feed check (stale, hold or one side)",
    "books": "fewer than 4 other books on this line",
    "others_moved": "the other books moved too",
    "worse_than_fair": "Hard Rock price worse than Pinnacle's fair price",
    "price_window": "Hard Rock price outside the price window",
    "whole_line": "whole-number line",
    "ineligible": "blocked by the live eligibility rules",
    "late": "read too old to flag",
    "superseded": "a newer read of the game existed",
    "already_flagged": "already flagged this game",
}


# ---------------------------------------------------------------- config
def W(cfg):
    return (cfg.get("ops") or {}).get("watch") or {}


def tcfg(cfg, track):
    return (W(cfg).get("tracks") or {}).get(track) or {}


def enabled(cfg):
    return bool(W(cfg).get("active"))


def track_on(cfg, track, slate):
    t = tcfg(cfg, track)
    return enabled(cfg) and bool(t.get("active")) and str(slate) >= str(t.get("from_slate") or "")


def read_pulls(cfg):
    return list(W(cfg).get("reads") or ["R", "W", "C"])


# ---------------------------------------------------------------- the log
def events_path():
    return common.dpath("watch", "events.jsonl")


def summary_path():
    return common.dpath("watch", "summary.json")


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
    """Append-only: the old bytes must stay an exact prefix of the new ones, then one atomic write."""
    if not events:
        return 0
    old = read_raw()
    add = "".join(common.dumps(e, False) + "\n" for e in events).encode("utf-8")
    why = guard_record.check(old, old + add)
    if why:
        raise RuntimeError("watch log guard: " + why)
    common.write_bytes(events_path(), old + add)
    return len(events)


# ---------------------------------------------------------------- the live switch (ops.pin_solo.live, OFF by default)
def ps(cfg):
    return (cfg.get("ops") or {}).get("pin_solo") or {}


def ps_era(cfg):
    return ps(cfg).get("era") or "ps1"


def pin_live(cfg, slate):
    """True only when Matt flipped ops.pin_solo.live AND the pin_solo watch track is on for this slate (same gates). Anything
    less and the method makes no pick."""
    return bool(ps(cfg).get("live")) and track_on(cfg, SOLO, slate)


# ---------------------------------------------------------------- reads
def usable(f, game, cfg):
    """A read a watch track may use: R, W or C, asked the other books and got Hard Rock back, before kickoff, taken at or
    after the final injury report counts as in."""
    if f.get("pull") not in read_pulls(cfg) or "others" not in f or not f.get("book"):
        return False
    at = common.parse_iso(f["at"])
    return at < common.parse_iso(game["start"]) and at >= injuries.report_counts_at(game, cfg)


def usable_reads(reads, game, cfg):
    return sorted((f for f in reads if usable(f, game, cfg)), key=lambda f: f["at"])


def window_label(a, b):
    return "%s→%s" % (a["pull"], b["pull"])


def _pin(oth, key):
    return ((oth.get(key) or {}).get("pairs") or {}).get(gg.PINNACLE)


def _fair_over(pair):
    return odds.fair_two_sided(pair[0], pair[1])


# ---------------------------------------------------------------- shared row pieces
def _eligible(inp, eng, g, mname, pid, line, pair, side):
    """-> reason string or None. The live engine's pre-price vetoes, exactly."""
    team = pair.get("team")
    if team not in (g["away"], g["home"]):
        return "player not on either team"
    if team in inp.behind:
        return "stats behind for %s" % team
    settled = inp.settled(g, team)
    pre = eng.pre_check(inp, g, mname, pid, line, pair, side, settled)
    return pre["reason"]


def _base(g, f, mname, pid, line, side, pair, price):
    return {"slate": g.get("slate") or f.get("slate"), "game_id": g["id"], "pid": pid, "player": pair.get("name"),
            "team": pair.get("team"), "market": mname, "line": line, "side": side, "price": price,
            "read": f["pull"], "read_at": f["at"]}


def _flag_id(track, slate, gid, pid, mname):
    return "%s:%s:%s:%s:%s" % (track, slate, gid, pid, mname)


def _cand_id(track, slate, gid, a_at, b_at, pid, mname, line, side):
    return "%s:%s:%s:%s:%s:%s:%s:%s:%s" % (track, slate, gid, a_at or "-", b_at, pid, mname, gg.fmt_line(line), side)


# ---------------------------------------------------------------- pin_solo
def solo_rows(a, b, g, inp, eng, cfg):
    """Every Pinnacle move >= min_move between reads a -> b of one game with the first gate that stopped it (None = qualifies
    so far). Returns (rows, n_pin_lines): n_pin_lines = Hard Rock lines Pinnacle also quoted on both reads."""
    ps = tcfg(cfg, SOLO)
    t = gg.T(cfg)
    v = cfg["values"]
    rows, n_lines = [], 0
    for mname in gg.market_names(cfg):
        ha = gg.hr_market(a, mname, cfg) or {}
        hb = gg.hr_market(b, mname, cfg) or {}
        oa = gg.other_lines(a, mname, cfg) or {}
        ob = gg.other_lines(b, mname, cfg) or {}
        hr_pids = {k[0] for k in ha} | {k[0] for k in hb}
        for key in sorted(set(oa) & set(ob), key=lambda k: (str(k[0]), k[1])):
            pid, line = key
            pa, pb = _pin(oa, key), _pin(ob, key)
            if pa is None or pb is None or pid not in hr_pids:
                continue
            fa, fb = _fair_over(pa), _fair_over(pb)
            if key in hb and key in ha:
                n_lines += 1
            if abs(fb - fa) < ps["min_move"] - EPS:
                continue
            side = "over" if fb > fa else "under"
            sgn = 1.0 if side == "over" else -1.0
            mv = []
            for book, pr in (ob.get(key) or {}).get("pairs", {}).items():
                prev = ((oa.get(key) or {}).get("pairs") or {}).get(book)
                if book == gg.PINNACLE or prev is None:
                    continue
                mv.append(sgn * (_fair_over(pr) - _fair_over(prev)))
            pin_a = fa if side == "over" else 1.0 - fa
            pin_b = fb if side == "over" else 1.0 - fb
            pair = hb.get(key) or ha.get(key) or next((p for k, p in hb.items() if k[0] == pid), None) \
                or next((p for k, p in ha.items() if k[0] == pid), None) or {}
            price = (hb.get(key) or {}).get(side)
            r = _base(g, b, mname, pid, line, side, pair, price)
            pa_hr = (ha.get(key) or {}).get(side)
            r.update(prev_read=a["pull"], prev_at=a["at"], window=window_label(a, b), price_before=pa_hr,
                     pin_before=round(pin_a, 4), pin_after=round(pin_b, 4),
                     pin_price_after=pb[0] if side == "over" else pb[1],
                     others_move=round(statistics.median(mv), 4) if mv else None, n_others=len(mv),
                     ev_vs_pin=round(gg.ev(pin_b, price), 4) if price is not None else None)
            why = None
            if key not in hb or key not in ha:
                why = "hr_line"
            elif hb[key]["why"] is not None or price is None:
                why = "hr_feed"
            elif len(mv) < ps["others_min"]:
                why = "books"
            elif statistics.median(mv) >= ps["others_max"] - EPS:
                why = "others_moved"
            elif gg.ev(pin_b, price) < ps.get("min_ev_vs_pin", 0.0) - EPS:
                why = "worse_than_fair"
            elif not gg.inside_gate(price, t):
                why = "price_window"
            elif ps.get("skip_whole_lines") and gg.whole_line(line):
                why = "whole_line"
            else:
                bad = _eligible(inp, eng, g, mname, pid, line, pair, side)
                if bad:
                    why = "ineligible"
                    r["detail"] = bad
            r["gate"] = why
            rows.append(r)
    return rows, n_lines


# ---------------------------------------------------------------- pin_judge
def judge_rows(f, g, inp, eng, cfg):
    """Every side of every Hard Rock main line on read f whose EV against Pinnacle's fair on the same line is >= min_ev, with
    the first gate that stopped it."""
    jc = tcfg(cfg, JUDGE)
    t = gg.T(cfg)
    rows = []
    for mname in gg.market_names(cfg):
        hb = gg.hr_market(f, mname, cfg) or {}
        of = gg.other_lines(f, mname, cfg) or {}
        for key, pair in sorted(hb.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
            pid, line = key
            pin = _pin(of, key)
            if pin is None or pair["over"] is None or pair["under"] is None:
                continue
            fo = _fair_over(pin)
            for side in ("over", "under"):
                price = pair[side]
                pf = gg.side_fair(fo, side)
                evp = gg.ev(pf, price)
                if evp < jc["min_ev"] - EPS:
                    continue
                r = _base(g, f, mname, pid, line, side, pair, price)
                r.update(pin_fair=round(pf, 4), pin_ev=round(evp, 4), pin_price=pin[0] if side == "over" else pin[1])
                why = None
                if pair["why"] is not None:
                    why = "hr_feed"
                elif not gg.inside_gate(price, t):
                    why = "price_window"
                elif jc.get("skip_whole_lines") and gg.whole_line(line):
                    why = "whole_line"
                else:
                    bad = _eligible(inp, eng, g, mname, pid, line, pair, side)
                    if bad:
                        why = "ineligible"
                        r["detail"] = bad
                r["gate"] = why
                rows.append(r)
    return rows


# ---------------------------------------------------------------- the step
def _flag_event(track, now, g, r, cfg, eng):
    ev = {"t": "flag", "track": track, "id": _flag_id(track, r["slate"], g["id"], r["pid"], r["market"]),
          "at": common.iso(now), "v": VERSION, "slate": r["slate"], "game_id": g["id"], "start": g["start"],
          "pid": r["pid"], "player": r["player"], "team": r["team"],
          "opp": g["home"] if r["team"] == g["away"] else g["away"], "home": r["team"] == g["home"],
          "market": r["market"], "line": r["line"], "side": r["side"], "price": r["price"],
          "bet": eng.bet_text(r["side"], r["line"], r["market"], cfg), "read": r["read"], "read_at": r["read_at"]}
    for k in ("prev_read", "prev_at", "window", "pin_before", "pin_after", "pin_price_after", "others_move",
              "n_others", "ev_vs_pin", "pin_fair", "pin_ev", "pin_price"):
        if k in r:
            ev[k] = r[k]
    return ev


def _cand_event(track, now, g, r, a_at, cid):
    e = {"t": "cand", "track": track, "id": cid, "at": common.iso(now), "v": VERSION, "slate": r["slate"],
         "game_id": g["id"], "pid": r["pid"], "player": r["player"], "team": r["team"], "market": r["market"],
         "line": r["line"], "side": r["side"], "price": r["price"], "read": r["read"], "read_at": r["read_at"],
         "gate": r["gate"]}
    for k in ("window", "pin_before", "pin_after", "others_move", "n_others", "ev_vs_pin", "pin_fair", "pin_ev",
              "detail"):
        if r.get(k) is not None:
            e[k] = r[k]
    return e


def scan(inp, eng, by_game, events, cfg, now):
    """New watch events for this run (nothing is written). by_game: gg.reads_by_game(). events: the watch log so far."""
    out = []
    done_ids = {e["id"] for e in events if e["t"] in ("pair", "scan")}
    flagged = {e["id"] for e in events if e["t"] == "flag"}
    solo_ps, jc = tcfg(cfg, SOLO), tcfg(cfg, JUDGE)
    for gid, g in sorted(inp.games.items(), key=lambda kv: (kv[1]["start"], kv[0])):
        if inp.started(g):
            continue
        slate = inp.slate
        g = dict(g, slate=slate)
        reads = usable_reads(by_game.get(gid, []), g, cfg)
        if not reads:
            continue
        newest = reads[-1]
        fresh = lambda f, age: (now - common.parse_iso(f["at"])).total_seconds() <= age * 60   # noqa: E731
        # ---- pin_solo: every consecutive pair of reads
        if track_on(cfg, SOLO, slate):
            for a, b in zip(reads, reads[1:]):
                pid_ = "pair:%s:%s:%s:%s" % (SOLO, slate, gid, b["at"])
                if pid_ in done_ids:
                    continue
                rows, n_lines = solo_rows(a, b, g, inp, eng, cfg)
                gap = (common.parse_iso(b["at"]) - common.parse_iso(a["at"])).total_seconds() / 60.0
                out.append({"t": "pair", "track": SOLO, "id": pid_, "at": common.iso(now), "v": VERSION, "slate": slate,
                            "game_id": gid, "window": window_label(a, b), "a_at": a["at"], "b_at": b["at"],
                            "gap_min": round(gap, 1), "pin_lines": n_lines, "moves": len(rows)})
                rows.sort(key=lambda r: (-abs(r["pin_after"] - r["pin_before"]), str(r["pid"]), r["market"]))
                for r in rows:
                    fid = _flag_id(SOLO, slate, gid, r["pid"], r["market"])
                    if r["gate"] is None:
                        if b is not newest:
                            r["gate"] = "superseded"
                        elif not fresh(b, solo_ps.get("max_age_min", 60)):
                            r["gate"] = "late"
                        elif fid in flagged:
                            r["gate"] = "already_flagged"
                    out.append(_cand_event(SOLO, now, g, r, a["at"],
                                           _cand_id(SOLO, slate, gid, a["at"], b["at"], r["pid"], r["market"], r["line"],
                                                    r["side"])))
                    if r["gate"] is None:
                        flagged.add(fid)
                        out.append(_flag_event(SOLO, now, g, r, cfg, eng))
        # ---- pin_judge: every read, first flag per player / market / game
        if track_on(cfg, JUDGE, slate):
            for f in reads:
                sid = "scan:%s:%s:%s:%s" % (JUDGE, slate, gid, f["at"])
                if sid in done_ids:
                    continue
                rows = judge_rows(f, g, inp, eng, cfg)
                out.append({"t": "scan", "track": JUDGE, "id": sid, "at": common.iso(now), "v": VERSION, "slate": slate,
                            "game_id": gid, "read": f["pull"], "read_at": f["at"], "sides": len(rows)})
                rows.sort(key=lambda r: (-r["pin_ev"], str(r["pid"]), r["market"]))
                for r in rows:
                    fid = _flag_id(JUDGE, slate, gid, r["pid"], r["market"])
                    if r["gate"] is None:
                        if f is not newest:
                            r["gate"] = "superseded"
                        elif not fresh(f, jc.get("max_age_min", 60)):
                            r["gate"] = "late"
                        elif fid in flagged:
                            r["gate"] = "already_flagged"
                    out.append(_cand_event(JUDGE, now, g, r, None,
                                           _cand_id(JUDGE, slate, gid, None, f["at"], r["pid"], r["market"], r["line"],
                                                    r["side"])))
                    if r["gate"] is None:
                        flagged.add(fid)
                        out.append(_flag_event(JUDGE, now, g, r, cfg, eng))
    return out


# ---------------------------------------------------------------- closing value (CLV vs Pinnacle's closing fair)
def closes(events, by_game, games, cfg, now):
    """For each flag with no close yet: the first C read after the flag's own read, Pinnacle's fair on the same line, and the
    flag's price against it (EV). Pinnacle off the line on that read = clv None. A flag made on the C read never gets one."""
    have = {e["id"] for e in events if e["t"] == "close"}
    out = []
    for fl in events:
        if fl["t"] != "flag" or fl["id"] in have:
            continue
        g = games.get(fl["game_id"])
        if g is None:
            continue
        cs = [f for f in usable_reads(by_game.get(fl["game_id"], []), g, cfg)
              if f["pull"] == "C" and f["at"] > fl["read_at"]]
        if not cs:
            continue
        c = cs[-1]
        pin = _pin(gg.other_lines(c, fl["market"], cfg) or {}, (fl["pid"], fl["line"]))
        fair = gg.side_fair(_fair_over(pin), fl["side"]) if pin is not None else None
        out.append({"t": "close", "track": fl["track"], "id": fl["id"], "at": common.iso(now), "v": VERSION,
                    "read_at": c["at"], "close_fair": None if fair is None else round(fair, 4),
                    "clv": None if fair is None else round(gg.ev(fair, fl["price"]), 4)})
    return out


# ---------------------------------------------------------------- grading (the Record's own grading code)
def grade_new(events, cfg, now, cache, store, ina, roster):
    """Grade flags whose game is over by calling grade.grade() on synthetic shown events (the same code, the same box-score
    / nflverse sources, pushes and voids as in the Record), never touching the Record. Returns watch `graded` events."""
    import grade
    graded = {e["id"] for e in events if e["t"] == "graded"}
    synth, track_of = [], {}
    for fl in events:
        if fl["t"] != "flag" or fl["id"] in graded:
            continue
        if now < common.parse_iso(fl["start"]):
            continue
        track_of[fl["id"]] = fl["track"]
        synth.append({"t": "shown", "kind": "single", "id": fl["id"], "at": fl["at"], "slate": fl["slate"],
                      "pick": {"id": fl["id"], "game_id": fl["game_id"], "player_id": fl["pid"], "player": fl["player"],
                               "team": fl["team"], "start": fl["start"], "market": fl["market"], "mkey": fl["market"],
                               "period": "FG", "side": fl["side"], "line": fl["line"], "price": fl["price"],
                               "price_at": fl["read_at"], "state": None}})
    if not synth:
        return []
    new, _ = grade.grade(synth, now, cfg, cache, store, ina, roster)
    out = []
    for e in new:
        if e["t"] == "graded" and e["id"] in track_of:
            out.append(dict(e, track=track_of[e["id"]], v=VERSION))
    return out


# ---------------------------------------------------------------- the summary
def _label(cfg, mname):
    return ((cfg["ops"]["markets"].get(mname) or {}).get("label")) or mname


def _stats(bets):
    w = sum(1 for b in bets if b["result"] == "W")
    l = sum(1 for b in bets if b["result"] == "L")
    be = [odds.break_even(b["price"]) for b in bets if b["result"] in ("W", "L")]
    n = w + l
    hit = round(w / float(n), 4) if n else None
    avg_be = round(sum(be) / len(be), 4) if be else None
    return {"flags": len(bets), "w": w, "l": l, "push": sum(1 for b in bets if b["result"] == "PUSH"),
            "void": sum(1 for b in bets if b["result"] == "VOID"),
            "pending": sum(1 for b in bets if b["result"] is None),
            "units": round(sum(b["units"] or 0.0 for b in bets if b["result"]), 4),
            "hit_rate": hit, "avg_break_even": avg_be,
            "edge_pts": round(hit - avg_be, 4) if hit is not None and avg_be is not None else None}


def build_summary(events, cfg, now, credits=None):
    """data/watch/summary.json from the log (docs/SPEC.md section 10). Pure."""
    flags = {e["id"]: e for e in events if e["t"] == "flag"}
    graded = {}
    for e in events:
        if e["t"] == "graded":
            graded.setdefault(e["id"], e)
    close = {}
    for e in events:
        if e["t"] == "close":
            close.setdefault(e["id"], e)
    keep = int(W(cfg).get("summary_weeks", 6))
    tracks = []
    for tr in TRACKS:
        tc = tcfg(cfg, tr)
        bets = []
        for fid, fl in flags.items():
            if fl["track"] != tr:
                continue
            g = graded.get(fid)
            c = close.get(fid) or {}
            b = {k: fl.get(k) for k in ("id", "slate", "game_id", "start", "player", "team", "opp", "home", "market",
                                        "line", "side", "bet", "price", "read", "read_at", "prev_read", "prev_at", "window",
                                        "pin_before", "pin_after", "others_move", "n_others", "ev_vs_pin", "pin_fair",
                                        "pin_ev", "at")}
            b.update(flagged_at=b.pop("at"), game=("%s vs %s" if fl["home"] else "%s @ %s") % (fl["team"], fl["opp"]),
                     market_label=_label(cfg, fl["market"]),
                     result=g["result"] if g else None, actual=g.get("actual_text") if g else None,
                     units=g.get("units") if g else None, close_fair=c.get("close_fair"), clv=c.get("clv"))
            bets.append(b)
        bets.sort(key=lambda b: (b["start"], b["id"]))
        tot = _stats(bets)
        clvs = [b["clv"] for b in bets if b["clv"] is not None]
        tot["clv"] = {"n": len(clvs), "avg": round(sum(clvs) / len(clvs), 4) if clvs else None,
                      "positive": sum(1 for x in clvs if x > 0)}
        by_market = []
        for m in sorted({b["market"] for b in bets}, key=lambda m: -sum(1 for b in bets if b["market"] == m)):
            sub = [b for b in bets if b["market"] == m]
            d = _stats(sub)
            by_market.append({"market": m, "label": _label(cfg, m), "flags": d["flags"], "w": d["w"], "l": d["l"],
                              "push": d["push"], "void": d["void"], "units": d["units"], "hit_rate": d["hit_rate"]})
        cands = [e for e in events if e["t"] == "cand" and e["track"] == tr]
        gates = {}
        for e in cands:
            gates[e["gate"] or "flagged"] = gates.get(e["gate"] or "flagged", 0) + 1
        distinct = len({(e["game_id"], e["pid"], e["market"], e["line"], e["side"]) for e in cands})
        scanned = sum(1 for e in events if e["t"] == ("pair" if tr == SOLO else "scan") and e["track"] == tr)
        seen = {"reads_scanned": scanned, "candidates": len(cands), "distinct_sides": distinct,
                "would_fire": gates.get("flagged", 0),
                "by_gate": [{"gate": k, "label": GATES.get(k, k), "n": n}
                            for k, n in sorted(((k, n) for k, n in gates.items() if k != "flagged"),
                                               key=lambda kv: (-kv[1], kv[0]))]}
        weeks = []
        for slate in sorted({b["slate"] for b in bets}, reverse=True):
            sub = [b for b in bets if b["slate"] == slate]
            d = _stats(sub)
            wk = {"slate": slate, "label": "Week %d" % int(str(slate).split("W")[-1]) if "W" in str(slate) else slate,
                  "flags": d["flags"], "w": d["w"], "l": d["l"], "push": d["push"], "void": d["void"],
                  "pending": d["pending"], "units": d["units"], "bets": sub if len(weeks) < keep else []}
            weeks.append(wk)
        tracks.append({"track": tr, "label": tc.get("label") or tr, "about": tc.get("about"),
                       "from_slate": tc.get("from_slate"), "active": bool(tc.get("active")), "total": tot,
                       "by_market": by_market, "seen": seen, "weeks": weeks})
    pairs = [e for e in events if e["t"] == "pair"]
    wins = {}
    for e in pairs:
        d = wins.setdefault(e["window"], {"window": e["window"], "pairs": 0, "gap_min": 0.0, "pin_lines": 0, "moves": 0})
        d["pairs"] += 1
        d["gap_min"] += e["gap_min"]
        d["pin_lines"] += e["pin_lines"]
        d["moves"] += e["moves"]
    by_window = []
    for d in sorted(wins.values(), key=lambda d: d["window"]):
        n = float(d["pairs"])
        by_window.append({"window": d["window"], "pairs": d["pairs"], "avg_gap_hours": round(d["gap_min"] / n / 60.0, 1),
                          "avg_pin_lines": round(d["pin_lines"] / n, 1), "avg_moves": round(d["moves"] / n, 1)})
    return {"schema": 1, "updated_at": common.iso(now),
            "note": "Research only. Never on the Picks tab, never in the Record. Promotion to a live rule needs Matt.",
            "reads": {"games_with_pairs": len({e["game_id"] for e in pairs}), "pairs": len(pairs),
                      "by_window": by_window},
            "credits": credits or {"w_reads": 0, "w_credits": 0},
            "tracks": tracks}


def write_summary(events, cfg, now, credits=None):
    doc = build_summary(events, cfg, now, credits)
    old = common.read_json(summary_path()) or {}
    if {k: v for k, v in old.items() if k != "updated_at"} != {k: v for k, v in doc.items() if k != "updated_at"}:
        common.write_json(summary_path(), doc)
        return True
    return False


def w_credits(ledger, now):
    """{"w_reads", "w_credits"} this calendar month from the odds ledger (pull 'W')."""
    month = now.date().isoformat()[:7]
    rows = [e for e in (ledger or {}).get("spent", []) if e.get("pull") == "W" and str(e.get("date", ""))[:7] == month]
    return {"w_reads": len(rows), "w_credits": sum(e.get("cost", 0) for e in rows), "month": month}


# ---------------------------------------------------------------- live picks (only when ops.pin_solo.live)
def fair_price(p):
    """A probability as the American price it is fair at."""
    d = 1.0 / p
    return int(round((d - 1.0) * 100)) if d >= 2.0 else int(round(-100.0 / (d - 1.0)))


def newest_usable(reads, game, cfg):
    us = usable_reads(reads, game, cfg)
    return us[-1] if us else None


def live_candidates(inp, eng, by_game, cfg, now):
    """Pin_solo sides that pass every gate on the NEWEST pair of usable reads of each unstarted game, the newer read being at
    most max_age_min old. Same rows, same gates as the watch track (solo_rows). Sorted biggest Pinnacle move first."""
    ps_c = tcfg(cfg, SOLO)
    out = []
    for gid, g in sorted(inp.games.items(), key=lambda kv: (kv[1]["start"], kv[0])):
        if inp.started(g):
            continue
        reads = usable_reads(by_game.get(gid, []), g, cfg)
        if len(reads) < 2:
            continue
        a, b = reads[-2], reads[-1]
        if (now - common.parse_iso(b["at"])).total_seconds() > ps_c.get("max_age_min", 60) * 60:
            continue
        rows, _ = solo_rows(a, b, g, inp, eng, cfg)
        for r in rows:
            if r["gate"] is None:
                r["g"] = g
                out.append(r)
    out.sort(key=lambda r: (-abs(r["pin_after"] - r["pin_before"]), str(r["pid"]), r["market"]))
    return out


def plain_reason(r, bet):
    """'Pinnacle moved 3 pts toward Over 5.5 receptions; the other books moved 1; Hard Rock is still at -115, better than
    Pinnacle's fair -122.'"""
    g = lambda x: "%g" % round(abs(x) * 100, 1)                 # noqa: E731
    fair = fair_price(r["pin_after"])
    still = r.get("price_before") == r["price"]
    cmp_ = "better than" if (r.get("ev_vs_pin") or 0) > 1e-4 else "level with"
    om = r["others_move"] or 0.0
    others = g(om) if om >= 0 else "%s the other way" % g(om)
    return "Pinnacle moved %s pts toward %s; the other books moved %s; Hard Rock is %s %s, %s Pinnacle's fair %s." % (
        g(r["pin_after"] - r["pin_before"]), bet, others, "still at" if still else "now at",
        common.fmt_price(r["price"]), cmp_, common.fmt_price(fair))
