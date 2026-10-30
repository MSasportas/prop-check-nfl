"""make_fixtures.py: rebuilds fixtures/<name>/{slate,record,status,rules}.json (schema 2, docs/SPEC.md) for the page.
Run from the repo root:  python fixtures/make_fixtures.py
Test data only: invented players, prices and results. Never read by the timer. Open the page with ?fixture=<name>.
`fixture` and `fixture_now` are the only keys that are not in SPEC.md; the page reads fixture_now (as "now") only with ?fixture=.
rules.json comes from scripts/emit.rules_doc(config), so it is the real shape.
"""
import json
import os
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import common  # noqa: E402
import emit    # noqa: E402

OUT = os.path.join(ROOT, "fixtures")
CFG = common.load_config()
NOUN = {"rec": "receptions", "rec_yds": "receiving yards", "rush_yds": "rushing yards", "rush_att": "carries",
        "pass_yds": "passing yards", "pass_td": "passing TDs"}
LABEL = {"rec": "Receptions", "rec_yds": "Receiving yards", "rush_yds": "Rushing yards", "rush_att": "Carries",
         "pass_yds": "Passing yards", "pass_td": "Passing TDs"}


def price_txt(p):
    return ("+%d" % p) if p > 0 else str(p)


def time_txt(iso):
    h, m = int(iso[11:13]), iso[14:16]
    return "%d:%s %s" % (h % 12 or 12, m, "PM" if h >= 12 else "AM")


# ---------------------------------------------------------------- games
def game(slate_wk, away, home, day, hhmm, off, roof="outdoors", **kw):
    st = {"started": False, "final": False, "score": None, "settled": "injury_report", "props_state": "ok",
          "props_at": None, "note": None, "picks": 0}
    st.update(kw)
    return {"id": "2026_%02d_%s_%s" % (slate_wk, away, home), "away": away, "home": home,
            "kickoff": "%sT%s:00%s" % (day, hhmm, off), "day": day, "roof": roof, **st}


# ---------------------------------------------------------------- picks
def pick(wk, g, team, player, short, pos, market, side, line, price, gap, nb, fev, reason, settled="injury_report",
         state="open", read_at=None, weather=None, price_final=False, pulled=None, graded=None, rank=None, history=None, home=None):
    home = (team == g["home"]) if home is None else home
    opp = g["away"] if home else g["home"]
    pid = "00-00%05d" % (zlib.crc32(player.encode()) % 99999)
    bet = "%s %s %s" % (side.capitalize(), line, NOUN[market])
    r = {"id": "2026-W%02d:%s:%s:%s:FG:%s:%s" % (wk, g["id"], pid, market, line, side), "era": "gg1", "rank": rank if state == "open" else None,
         "state": state, "game_id": g["id"], "day": g["day"], "kickoff": g["kickoff"],
         "game": ("%s vs %s" if home else "%s @ %s") % (team, opp), "player": player, "player_short": short, "player_id": pid,
         "pos": pos, "team": team, "opp": opp, "home": home, "market": market, "market_label": LABEL[market], "side": side,
         "line": line, "bet": bet, "book": "Hard Rock", "price": price, "read_at": read_at, "read_pull": "R",
         "vs": "%+.1f%% vs %d books (Pinnacle in)" % (gap, nb), "gap_pct": gap, "n_books": nb, "pinnacle": True,
         "final_ev_pct": fev, "reason": reason, "settled": settled,
         "settled_text": "Inactives confirmed" if settled == "inactives" else "Injury report in",
         "flags": {"price_final": price_final, "weather": weather or {"state": "checked", "text": "54°F, wind 6 mph", "wind_mph": 6}},
         "pulled": pulled, "graded": graded,
         "history": history or [{"at": read_at, "text": "Posted at %s" % price_txt(price)}]}
    return r


DOME = {"state": "dome", "text": "Indoors", "wind_mph": None}
NOWX = {"state": "not_checked", "text": None, "wind_mph": None}


def hist_extra(p, extra):
    p["history"] = p["history"] + extra
    return p


# ---------------------------------------------------------------- status
def pool(left=640, keys=6, live=4, shared=1, days=14, low=False, empty=False):
    return {"credits_left": left, "keys": keys, "keys_live": live, "keys_shared": shared, "days_left": days, "low": low, "empty": empty}


def slate_doc(wk, now, gen, games, picks, off, status, banners=(), empty=None, valid_until=None, next_check=None):
    return {"schema": 2, "era": "gg1", "season": 2026, "week": wk, "label": "Week %d" % wk, "generated_at": gen, "status_at": gen,
            "valid_until": valid_until, "next_check": next_check, "status": status, "banners": list(banners), "empty": empty,
            "games": games, "picks": picks, "fixture": True, "fixture_now": now}


def status_obj(odds_state="ok", odds_at=None, next_at=None, inj=("ok", None), ina=("ok", None, None), pl=None, run_ok=True, run_at=None,
               failed=None, stats=("ok", 12, None)):
    return {"odds": {"state": odds_state, "at": odds_at, "next_at": next_at},
            "injuries": {"state": inj[0], "at": inj[1]},
            "inactives": {"state": ina[0], "at": ina[1], "note": ina[2]},
            "stats": {"state": stats[0], "through_week": stats[1], "at": stats[2]},
            "pool": pl or pool(), "last_run": {"ok": run_ok, "at": run_at, "failed_step": failed}}


def status_json(wk, finished, ok=True, last_ok=None, failed=None, odds=None):
    return {"ok": ok, "season": 2026, "week": wk, "slate": "2026-W%02d" % wk, "started_at": finished, "status_at": finished,
            "finished_at": finished, "last_ok_at": last_ok or finished, "failed_step": failed,
            "steps": [{"step": "rules", "status": "skipped", "note": "unchanged"}, {"step": "odds", "status": "failed" if failed == "odds" else "ok", "note": "fixture"},
                      {"step": "slate", "status": "ok", "note": "fixture"}],
            "sources": {"odds": "ok", "espn_injuries": "ok"}, "odds": odds or {"state": "ok"},
            "live_page": {"checked_at": finished, "http": 200, "is_page": True}}


# ---------------------------------------------------------------- record
def entry(p, result=None, actual=None, units=None, pulled=None):
    away, home = (p["opp"], p["team"]) if p["home"] else (p["team"], p["opp"])
    state = "pulled" if pulled else ("graded" if result else "locked")
    return {"id": p["id"], "kind": "single", "era": "gg1", "state": state, "date": p["day"], "game": "%s at %s" % (away, home), "player": p["player_short"],
            "team": p["team"], "market": p["market"], "period": "FG", "bet": p["bet"], "price": p["price"], "result": result,
            "actual": actual, "units": units, "pulled": ({k: v for k, v in pulled.items() if k in ("at", "reason", "if_bet")} if pulled else None),
            "legs": []}


def units_for(price, result):
    if result == "W":
        return round(price / 100.0 if price > 0 else 100.0 / -price, 4)
    return -1.0 if result == "L" else 0.0


def break_even(price):
    return -price / (-price + 100.0) if price < 0 else 100.0 / (price + 100.0)


def row(key, label, ents):
    w = sum(1 for e in ents if e["result"] == "W")
    l = sum(1 for e in ents if e["result"] == "L")
    need = [break_even(e["price"]) for e in ents if e["result"] in ("W", "L")]
    return {"key": key, "label": label, "w": w, "l": l, "push": 0, "void": sum(1 for e in ents if e["result"] == "VOID"),
            "n": w + l, "units": round(sum(e["units"] or 0 for e in ents if e["result"]), 4),
            "hit_rate": round(w / float(w + l), 4) if w + l else None,
            "needed": round(sum(need) / len(need), 4) if need else None}


def week_doc(wk, ents):
    counted = [e for e in ents if not e["pulled"]]
    return {"week": wk, "slate": "2026-W%02d" % wk, "label": "Week %d" % wk, "graded": all(e["result"] is not None for e in ents),
            "w": sum(1 for e in counted if e["result"] == "W"), "l": sum(1 for e in counted if e["result"] == "L"),
            "units": round(sum(e["units"] or 0 for e in counted), 4), "entries": ents}


def summary(weeks):
    every = [e for w in weeks for e in w["entries"]]
    singles = [e for e in every if not e["pulled"]]
    rows = []
    for m in ("rec", "rec_yds", "rush_yds", "rush_att", "pass_yds", "pass_td"):
        sub = [e for e in singles if e["market"] == m and e["result"]]
        if sub:
            rows.append(row("%s|FG" % m, LABEL[m], sub))
    pulled = [e for e in every if e["pulled"] and e["result"]]
    if pulled:
        rows.append(row("pulled", "Pulled", pulled))
    head = row("headline", "Singles", singles)
    head.pop("key"), head.pop("label")
    return {"headline": head, "rows": rows}


PLAYERS = [  # invented: (name, short, pos, market, side, line, price)
    ("Terrell Voss", "T. Voss", "WR", "rec", "over", 5.5, -125), ("Dalton Pryce", "D. Pryce", "RB", "rush_att", "under", 17.5, -115),
    ("Marcus Hale", "M. Hale", "QB", "pass_yds", "under", 241.5, -110), ("Jerome Castile", "J. Castile", "TE", "rec_yds", "under", 44.5, -120),
    ("Andre Whitlock", "A. Whitlock", "RB", "rush_yds", "over", 62.5, 105), ("Cade Brennan", "C. Brennan", "QB", "pass_td", "under", 1.5, -135),
    ("Devon Okafor", "D. Okafor", "WR", "rec_yds", "over", 58.5, -110), ("Lamar Tibbs", "L. Tibbs", "WR", "rec", "under", 4.5, 100),
    ("Ray Ostrander", "R. Ostrander", "RB", "rush_yds", "under", 48.5, -115), ("Wes Calloway", "W. Calloway", "TE", "rec", "over", 3.5, -140),
    ("Nico Barrera", "N. Barrera", "QB", "pass_yds", "over", 262.5, -105), ("Eli Granger", "E. Granger", "RB", "rush_att", "over", 14.5, 110)]

REASONS = [
    "He has gone under this number in 5 of his last 6 games, and this defense gives up few catches to receivers.",
    "His carries have dropped each week and the team is a big favorite, so a backup should see more of the game.",
    "The opposing pass rush has been getting home, and his last three games stayed under this number.",
    "Hard Rock is a step above the other books here, and his target share is down since the new receiver arrived.",
    "The other defense is allowing a lot on the ground and he has cleared this number in 4 of his last 5.",
    "This offense runs through the ground game and the weather is cold, so fewer passing scores.",
    "He has gone over this number in 4 of his last 5 and the opponent is weak against his type of receiver.",
    "He only gets a short role in this offense, and the number is high for what he has been doing.",
    "The matchup is tough for the run and he has gone under this number in 4 of 6.",
    "He is a first read on third down and this defense has given up a lot to tight ends.",
    "The passing offense is healthy and the opponent has given up big yardage through the air.",
    "Heavy early-down role with the lead back out, and the other defense has struggled to stop the run."]


def mk(wk, g, i, **kw):
    n, s, pos, mkt, side, line, pr = PLAYERS[i % len(PLAYERS)]
    team = kw.pop("team", g["home"])
    return pick(wk, g, team, n, s, pos, mkt, side, line, kw.pop("price", pr), kw.pop("gap", 3.4 + (i % 5) * 0.5), kw.pop("nb", 5 + i % 4),
                kw.pop("fev", 2.4 + (i % 4) * 0.4), kw.pop("reason", REASONS[i % len(REASONS)]), **kw)


# ================================================================= scenarios
def build_normal():
    """Week 13, Sunday Dec 6 at 1:20 PM: 1 PM games under way (1 locked), 6 open picks over Sunday and Monday, 1 pulled."""
    wk, off, now = 13, "-05:00", "2026-12-06T13:20:00-05:00"
    g = {}
    g["tnf"] = game(wk, "BAL", "CLE", "2026-12-03", "20:15", off, started=True, final=True, score={"away": 17, "home": 23}, props_state="closed", settled="inactives")
    g["atl"] = game(wk, "ATL", "CAR", "2026-12-06", "13:00", off, started=True, props_at="2026-12-06T11:32:00-05:00", settled="inactives", picks=1)
    g["hou"] = game(wk, "HOU", "TEN", "2026-12-06", "13:00", off, started=True, props_state="closed", settled="inactives")
    g["det"] = game(wk, "DET", "MIN", "2026-12-06", "13:00", off, roof="dome", started=True, props_at="2026-12-06T11:32:00-05:00", settled="inactives")
    g["sea"] = game(wk, "SEA", "DEN", "2026-12-06", "16:05", off, props_at="2026-12-06T11:32:00-05:00", picks=2)
    g["dal"] = game(wk, "DAL", "PHI", "2026-12-06", "16:25", off, props_at="2026-12-06T11:32:00-05:00", picks=2)
    g["buf"] = game(wk, "BUF", "KC", "2026-12-06", "16:25", off, props_state="none_yet", note="No Hard Rock player props yet. Next check 2:30 PM.")
    g["gb"] = game(wk, "GB", "PIT", "2026-12-06", "20:20", off, props_state="not_pulled", note="Not priced yet: saving odds credits.")
    g["lar"] = game(wk, "LAR", "SF", "2026-12-07", "20:15", off, props_at="2026-12-06T11:32:00-05:00", picks=2)
    rd = "2026-12-06T11:32:00-05:00"
    locked = mk(wk, g["atl"], 0, state="locked", read_at=rd, settled="inactives", price_final=False,
                history=[{"at": rd, "text": "Posted at -125"}, {"at": "2026-12-06T11:44:00-05:00", "text": "Inactives confirmed"}, {"at": "2026-12-06T13:00:00-05:00", "text": "Locked"}])
    pulled = mk(wk, g["sea"], 5, state="pulled", read_at=rd, price=-135,
                pulled={"at": "2026-12-06T12:50:00-05:00", "reason": "Hard Rock moved the line to 2.5.", "code": "line_moved",
                        "if_bet": "If you already bet it at -135: your bet stands at that number. Hard Rock no longer offers this line.", "now_price": None},
                history=[{"at": rd, "text": "Posted at -135"}, {"at": "2026-12-06T12:50:00-05:00", "text": "Pulled. Hard Rock moved the line to 2.5."}])
    o = [mk(wk, g["sea"], 1, rank=3, read_at=rd, team="DEN", weather=NOWX),
         mk(wk, g["dal"], 2, rank=1, gap=5.6, nb=8, fev=4.1, read_at=rd, team="PHI"),
         mk(wk, g["dal"], 3, rank=2, read_at=rd, team="DAL", price=-120),
         mk(wk, g["sea"], 4, rank=4, read_at=rd, team="SEA", price=105, nb=4),
         mk(wk, g["lar"], 6, rank=1, read_at=rd, team="LAR", weather=DOME),
         mk(wk, g["lar"], 7, rank=2, read_at=rd, team="SF", price=100, gap=3.0, fev=2.1)]
    picks = [locked, pulled] + o
    picks.sort(key=lambda p: (p["day"], p["rank"] or 999, p["kickoff"], p["id"]))
    sl = slate_doc(wk, now, "2026-12-06T13:10:00-05:00", list(g.values()), picks, off,
                   status_obj("ok", rd, "2026-12-06T14:30:00-05:00", ("ok", "2026-12-04T16:12:00-05:00"), ("ok", "2026-12-06T11:44:00-05:00", "Posted for the 1:00 PM games."),
                              run_at="2026-12-06T13:10:00-05:00"),
                   valid_until="2026-12-08T06:00:00-05:00", next_check="2026-12-06T14:30:00-05:00")
    cur = on_record(picks)
    return sl, cur, "2026-12-06T13:10:00-05:00"


TEAMS = [("ATL", "CAR"), ("HOU", "TEN"), ("DET", "MIN"), ("SEA", "DEN"), ("DAL", "PHI"), ("BUF", "KC"), ("GB", "PIT"), ("LAR", "SF")]


def history_weeks(upto, seed=7, loss_bias=0.45):
    """Graded weeks 8..upto for the record (invented results)."""
    import random
    rnd = random.Random(seed)
    weeks = []
    for wk in range(8, upto + 1):
        ents = []
        for k in range(rnd.randint(4, 6)):
            n, s, pos, mkt, side, line, pr = PLAYERS[(wk * 3 + k) % len(PLAYERS)]
            day = "2026-%s" % (("11-%02d" % (1 + 7 * (wk - 8))) if wk < 13 else "12-06")
            res = "W" if rnd.random() > loss_bias else "L"
            a = int(line) + (1 if (res == "W") == (side == "over") else 0)
            ents.append({"id": "2026-W%02d:g%d:p%d:%s:FG:%s:%s" % (wk, k, k, mkt, line, side), "kind": "single", "era": "gg1", "state": "graded", "date": day,
                         "game": "%s at %s" % TEAMS[(wk + k) % len(TEAMS)], "player": s, "team": TEAMS[(wk + k) % len(TEAMS)][0], "market": mkt, "period": "FG",
                         "bet": "%s %s %s" % (side.capitalize(), line, NOUN[mkt]), "price": pr, "result": res,
                         "actual": "had %d" % (a if res == "W" and side == "over" else max(a - 1, 0)), "units": units_for(pr, res),
                         "pulled": None, "legs": []})
        if wk % 2 == 0:  # one pulled, graded, apart
            e = dict(ents[0])
            e.update(id=e["id"] + ":x", state="pulled", player="K. Mercer", result="L", units=-1.0, actual="had 8",
                     pulled={"at": "%sT12:00:00-05:00" % e["date"], "reason": "Listed questionable."})
            ents.append(e)
        weeks.append(week_doc(wk, ents))
    return weeks


def retired_block():
    ents = []
    plan = [("2026-10-18", "ARI at LA", "K. Bourne", "Under 16.5 longest reception yards", -115, "W", "had 15"),
            ("2026-10-18", "ARI at LA", "J. Brooks", "Under 20.5 receiving yards", -115, "L", "had 30"),
            ("2026-10-18", "ARI at LA", "B. Corum", "Under 9.5 carries", -115, "W", "had 9"),
            ("2026-10-18", "BAL at CLE", "L. Jackson", "Over 1st half 118.5 passing yards", -110, "L", "had 96"),
            ("2026-10-22", "NE at CHI", "R. Stevenson", "Under 7.5 receiving yards (1st half)", -110, "L", "had 12")]
    for i, (d, g, pl, bet, pr, res, act) in enumerate(plan):
        ents.append({"id": "2026-W06:r%d" % i, "kind": "single", "era": "v1", "state": "graded", "date": d, "game": g, "player": pl, "team": "AAA", "market": "rec_yds",
                     "period": "FG", "bet": bet, "price": pr, "result": res, "actual": act, "units": units_for(pr, res), "pulled": None, "legs": []})
    wk = week_doc(6, ents)
    return {"label": "Retired rules (Oct 18-22)", "note": "Picks and parlays from the first rules (blended chance, 1st half, parlays). Kept exactly as shown. Not part of the current record.",
            "summary": summary([wk]), "weeks": [wk]}


def on_record(picks):
    """Only started (locked/graded) or pulled picks are on the Record; open ones are not there yet."""
    return [entry(x, pulled=x["pulled"]) for x in picks if x["state"] != "open"]


def record_doc(season_weeks, current_entries, current_wk, gen, retired=True, older_paths=(), keep=4):
    weeks = list(season_weeks)
    if current_entries and current_wk is not None:
        weeks.append(week_doc(current_wk, current_entries))
    weeks.sort(key=lambda w: w["week"], reverse=True)
    shown, older = weeks[:keep], weeks[keep:]
    rec = {"generated_at": gen, "season": 2026, "era": "gg1", "summary": summary(weeks), "weeks": shown,
           "older": ["record/%s.json" % w["slate"] for w in older], "retired": retired_block() if retired else None}
    return rec, {w["slate"]: {"weeks": [w]} for w in older}


def write(name, sl, rec, status, older=None):
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    for fn, doc in (("slate.json", sl), ("record.json", rec), ("status.json", status), ("rules.json", emit.rules_doc(CFG))):
        with open(os.path.join(d, fn), "w") as f:
            json.dump(doc, f, indent=1, ensure_ascii=False)
            f.write("\n")
    for slate, doc in (older or {}).items():
        os.makedirs(os.path.join(d, "record"), exist_ok=True)
        with open(os.path.join(d, "record", "%s.json" % slate), "w") as f:
            json.dump(doc, f, indent=1)
            f.write("\n")


def main():
    # wipe the old fixture dirs (schema 1)
    import shutil
    for n in os.listdir(OUT):
        p = os.path.join(OUT, n)
        if os.path.isdir(p):
            shutil.rmtree(p)

    # ---- normal
    sl, cur, gen = build_normal()
    hw = history_weeks(12, seed=11, loss_bias=0.42)
    rec, older = record_doc(hw, cur, 13, gen)
    write("normal", sl, rec, status_json(13, gen), older)

    # ---- fresh: Friday 5:20 PM, right after the final injury report, no inactives
    wk, off, now = 13, "-05:00", "2026-12-04T17:20:00-05:00"
    rd = "2026-12-04T17:08:00-05:00"
    g = [game(wk, "BAL", "CLE", "2026-12-03", "20:15", off, started=True, final=True, score={"away": 20, "home": 24}, props_state="closed", settled="inactives"),
         game(wk, "SEA", "DEN", "2026-12-06", "16:05", off, props_at=rd, picks=1),
         game(wk, "DAL", "PHI", "2026-12-06", "16:25", off, props_at=rd, picks=2),
         game(wk, "ATL", "CAR", "2026-12-06", "13:00", off, props_state="none_yet", note="No Hard Rock player props yet. Next check 9:00 PM."),
         game(wk, "LAR", "SF", "2026-12-07", "20:15", off, settled="early", props_state="none_yet", note="No Hard Rock player props yet. Next check 9:00 PM.")]
    p = [mk(wk, g[2], 2, rank=1, gap=5.6, nb=8, fev=4.1, read_at=rd, team="PHI"), mk(wk, g[2], 3, rank=2, read_at=rd, team="DAL", price=-120),
         mk(wk, g[1], 4, rank=3, read_at=rd, team="DEN", price=105, weather=NOWX)]
    sl = slate_doc(wk, now, "2026-12-04T17:15:00-05:00", g, p, off,
                   status_obj("ok", rd, "2026-12-04T21:00:00-05:00", ("ok", "2026-12-04T16:10:00-05:00"), ("not_yet", None, "Not posted yet. They come out about 90 minutes before kickoff."),
                              pl=pool(left=118, keys=6, live=2, days=9, low=True), run_at="2026-12-04T17:15:00-05:00"),
                   banners=[{"level": "warn", "text": "Odds keys running low: about 9 days of Hard Rock pulls left. Fewer price checks until more keys are added."}],
                   valid_until="2026-12-08T06:00:00-05:00", next_check="2026-12-04T21:00:00-05:00")
    rec, older = record_doc(hw, on_record(p), 13, "2026-12-04T17:15:00-05:00")
    write("fresh", sl, rec, status_json(13, "2026-12-04T17:15:00-05:00"), older)

    # ---- nopicks: Sunday morning, all priced, nothing clears the bar
    now, rd = "2026-12-06T09:40:00-05:00", "2026-12-06T09:05:00-05:00"
    g = [game(wk, a, b, d, t, off, props_at=rd) for a, b, d, t in
         (("ATL", "CAR", "2026-12-06", "13:00"), ("SEA", "DEN", "2026-12-06", "16:05"), ("DAL", "PHI", "2026-12-06", "16:25"), ("LAR", "SF", "2026-12-07", "20:15"))]
    sl = slate_doc(wk, now, "2026-12-06T09:30:00-05:00", g, [], off,
                   status_obj("ok", rd, "2026-12-06T11:30:00-05:00", ("ok", "2026-12-04T16:10:00-05:00"), ("not_yet", None, "Not posted yet. They come out about 90 minutes before kickoff."), run_at="2026-12-06T09:30:00-05:00"),
                   empty={"code": "no_picks_yet", "text": "No picks right now. Nothing beats the other books by enough at Hard Rock's prices.", "sub": "Next check 11:30 AM."},
                   valid_until="2026-12-08T06:00:00-05:00", next_check="2026-12-06T11:30:00-05:00")
    rec, older = record_doc(hw, None, None, "2026-12-06T09:30:00-05:00")
    write("nopicks", sl, rec, status_json(13, "2026-12-06T09:30:00-05:00"), older)

    # ---- allstarted: Monday night, everything has kicked off; graded picks, 1 pulled
    now = "2026-12-07T23:30:00-05:00"
    g = [game(wk, "ATL", "CAR", "2026-12-06", "13:00", off, started=True, final=True, score={"away": 21, "home": 17}, settled="inactives", props_at=rd, picks=1),
         game(wk, "DAL", "PHI", "2026-12-06", "16:25", off, started=True, final=True, score={"away": 13, "home": 27}, settled="inactives", props_at=rd, picks=2),
         game(wk, "LAR", "SF", "2026-12-07", "20:15", off, roof="dome", started=True, final=True, score={"away": 30, "home": 24}, settled="inactives", props_at=rd, picks=1)]
    res = [("W", "had 7", 1.0, 0), ("L", "had 21", -1.0, 1), ("W", "had 3", 1.0, 2)]
    ap = []
    for k, (r, a, _u, gi) in enumerate(res):
        x = mk(wk, g[gi], k, state="locked", read_at=rd, settled="inactives", price_final=True)
        x["graded"] = {"result": r, "actual": a, "units": units_for(x["price"], r)}
        ap.append(x)
    pl = mk(wk, g[1], 5, state="pulled", read_at=rd, price=-135, pulled={"at": "2026-12-06T15:10:00-05:00", "reason": "Inactive today.", "code": "inactive",
            "if_bet": "If you already bet it at -135: He is inactive. Hard Rock normally voids a prop if the player does not play; check your slip.", "now_price": None})
    pl["graded"] = {"result": "VOID", "actual": "did not play", "units": 0.0}
    ap.append(pl)
    sl = slate_doc(wk, now, "2026-12-07T23:20:00-05:00", g, ap, off,
                   status_obj("ok", rd, None, ("ok", "2026-12-04T16:10:00-05:00"), ("ok", "2026-12-07T18:45:00-05:00", "Posted for the 8:15 PM game."), run_at="2026-12-07T23:20:00-05:00"),
                   empty={"code": "all_started", "text": "Every Week 13 game has started.", "sub": "Week 14 picks start showing up after the final injury report."},
                   valid_until="2026-12-08T06:00:00-05:00", next_check=None)
    ce = []
    for x in ap:
        gd = x["graded"]
        ce.append(entry(x, gd["result"], gd["actual"], gd["units"], x["pulled"]))
    rec, older = record_doc(hw, ce, 13, "2026-12-07T23:20:00-05:00")
    write("allstarted", sl, rec, status_json(13, "2026-12-07T23:20:00-05:00"), older)

    # ---- recordempty: Sunday Nov 1 (week 8, go-live), two open picks, no graded picks yet, retired block present
    wk, off = 8, "-05:00"
    now, rd = "2026-11-01T10:20:00-05:00", "2026-11-01T09:48:00-05:00"
    g = [game(wk, "NYJ", "MIA", "2026-11-01", "13:00", off, props_at=rd, picks=1), game(wk, "HOU", "TEN", "2026-11-01", "16:05", off, props_at=rd, picks=1),
         game(wk, "KC", "LV", "2026-11-02", "20:15", off, settled="early", props_state="none_yet", note="No Hard Rock player props yet. Next check 4:00 PM.")]
    p = [mk(wk, g[0], 1, rank=1, read_at=rd, team="MIA"), mk(wk, g[1], 3, rank=1, read_at=rd, team="TEN")]
    sl = slate_doc(wk, now, "2026-11-01T10:15:00-05:00", g, p, off,
                   status_obj("ok", rd, "2026-11-01T16:00:00-05:00", ("ok", "2026-10-30T16:10:00-04:00"), ("not_yet", None, "Not posted yet. They come out about 90 minutes before kickoff."), run_at="2026-11-01T10:15:00-05:00"),
                   valid_until="2026-11-03T06:00:00-05:00", next_check="2026-11-01T16:00:00-05:00")
    rec, older = record_doc([], on_record(p), 8, "2026-11-01T10:15:00-05:00")
    write("recordempty", sl, rec, status_json(8, "2026-11-01T10:15:00-05:00"), older)

    # ---- stale: Friday data on Sunday, the last run failed
    wk, off = 13, "-05:00"
    now = "2026-12-06T11:40:00-05:00"
    rd = "2026-12-04T17:08:00-05:00"
    g = [game(wk, "SEA", "DEN", "2026-12-06", "16:05", off, props_at=rd, picks=1), game(wk, "DAL", "PHI", "2026-12-06", "16:25", off, props_at=rd, picks=2)]
    p = [mk(wk, g[1], 2, rank=1, gap=5.6, nb=8, fev=4.1, read_at=rd, team="PHI"), mk(wk, g[1], 3, rank=2, read_at=rd, team="DAL", price=-120),
         mk(wk, g[0], 4, rank=3, read_at=rd, team="DEN", price=105)]
    sl = slate_doc(wk, now, "2026-12-04T17:15:00-05:00", g, p, off,
                   status_obj("ok", rd, "2026-12-04T21:00:00-05:00", ("ok", "2026-12-04T16:10:00-05:00"), ("not_yet", None, "Not posted yet. They come out about 90 minutes before kickoff."),
                              run_ok=False, run_at="2026-12-06T11:30:00-05:00", failed="odds"),
                   banners=[{"level": "stop", "text": "The last update failed at 11:30 AM (odds). Showing 5:15 PM data."}],
                   valid_until="2026-12-08T06:00:00-05:00", next_check="2026-12-04T21:00:00-05:00")
    rec, older = record_doc(hw, on_record(p), 13, "2026-12-04T17:15:00-05:00")
    write("stale", sl, rec, status_json(13, "2026-12-06T11:30:00-05:00", ok=False, last_ok="2026-12-04T17:15:00-05:00", failed="odds", odds={"state": "feed_down"}), older)

    # ---- poolempty: out of odds keys, shown picks still listed
    now = "2026-12-06T12:10:00-05:00"
    rd = "2026-12-06T09:05:00-05:00"
    g = [game(wk, "SEA", "DEN", "2026-12-06", "16:05", off, props_at=rd, picks=1), game(wk, "DAL", "PHI", "2026-12-06", "16:25", off, props_at=rd, picks=1),
         game(wk, "BUF", "KC", "2026-12-06", "16:25", off, props_state="not_pulled", note="Not priced yet: saving odds credits."),
         game(wk, "LAR", "SF", "2026-12-07", "20:15", off, props_state="not_pulled", note="Not priced yet: saving odds credits.")]
    p = [mk(wk, g[1], 2, rank=1, gap=5.6, nb=8, fev=4.1, read_at=rd, team="PHI"), mk(wk, g[0], 3, rank=2, read_at=rd, team="DEN")]
    sl = slate_doc(wk, now, "2026-12-06T12:05:00-05:00", g, p, off,
                   status_obj("pool_empty", rd, None, ("ok", "2026-12-04T16:10:00-05:00"), ("not_yet", None, "Not posted yet. They come out about 90 minutes before kickoff."),
                              pl=pool(left=0, keys=6, live=0, days=0, low=True, empty=True), run_at="2026-12-06T12:05:00-05:00"),
                   banners=[{"level": "stop", "text": "Out of odds keys: no new Hard Rock prices until more keys are added. No new picks."}],
                   valid_until="2026-12-08T06:00:00-05:00", next_check=None)
    rec, older = record_doc(hw, on_record(p), 13, "2026-12-06T12:05:00-05:00")
    write("poolempty", sl, rec, status_json(13, "2026-12-06T12:05:00-05:00", odds={"state": "pool_empty"}), older)

    # ---- safetypause: Record down 10+, no new picks, shown ones still looked after
    now = "2026-12-06T12:10:00-05:00"
    g = [game(wk, "SEA", "DEN", "2026-12-06", "16:05", off, props_at=rd, picks=1), game(wk, "DAL", "PHI", "2026-12-06", "16:25", off, props_at=rd, picks=0)]
    p = [mk(wk, g[0], 3, rank=1, read_at=rd, team="DEN")]
    sl = slate_doc(wk, now, "2026-12-06T12:05:00-05:00", g, p, off,
                   status_obj("ok", rd, "2026-12-06T14:00:00-05:00", ("ok", "2026-12-04T16:10:00-05:00"), ("not_yet", None, "Not posted yet. They come out about 90 minutes before kickoff."), run_at="2026-12-06T12:05:00-05:00"),
                   banners=[{"level": "warn", "text": "New picks are stopped: the Record is down 10 units or more."}],
                   valid_until="2026-12-08T06:00:00-05:00", next_check="2026-12-06T14:00:00-05:00")
    bad = history_weeks(12, seed=3, loss_bias=0.85)
    rec, older = record_doc(bad, on_record(p), 13, "2026-12-06T12:05:00-05:00")
    write("safetypause", sl, rec, status_json(13, "2026-12-06T12:05:00-05:00"), older)

    # ---- many: 11 open picks on one day (the More button) plus a long name and long bet text
    now, rd = "2026-12-06T10:30:00-05:00", "2026-12-06T09:05:00-05:00"
    g = [game(wk, "SEA", "DEN", "2026-12-06", "13:00", off, props_at=rd, picks=6), game(wk, "DAL", "PHI", "2026-12-06", "16:25", off, props_at=rd, picks=5)]
    p = []
    for i in range(11):
        gg = g[i % 2]
        x = mk(wk, gg, i, rank=i + 1, read_at=rd, team=gg["home"], gap=6.0 - i * 0.25, fev=4.2 - i * 0.15, nb=9 - i % 3)
        p.append(x)
    p[0]["player"] = "Christopher Montgomery-Washington III"
    p[0]["bet"] = "Under 249.5 passing yards"
    p[1]["bet"] = "Over 112.5 receiving yards"
    p[1]["price"] = -150
    p[2]["price"] = 120
    sl = slate_doc(wk, now, "2026-12-06T10:20:00-05:00", g, p, off,
                   status_obj("ok", rd, "2026-12-06T12:00:00-05:00", ("ok", "2026-12-04T16:10:00-05:00"), ("not_yet", None, "Not posted yet. They come out about 90 minutes before kickoff."), run_at="2026-12-06T10:20:00-05:00"),
                   valid_until="2026-12-08T06:00:00-05:00", next_check="2026-12-06T12:00:00-05:00")
    rec, older = record_doc(hw, on_record(p), 13, "2026-12-06T10:20:00-05:00")
    write("many", sl, rec, status_json(13, "2026-12-06T10:20:00-05:00"), older)


if __name__ == "__main__":
    main()
