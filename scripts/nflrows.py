#!/usr/bin/env python3
"""nflrows.py  Prop Check NFL: nflverse play-by-play -> compact per-player-per-game rows (stdlib only).

Full game (FG, includes OT) and 1st half (1H = quarters 1-2, game_half == 'Half1') for every stat.
The same rows are the model's history AND the grader's actuals (see actual()).

USAGE
  python3 nflrows.py build OUT.json.gz PBP.csv.gz [PBP2.csv.gz ...] [--roster R.csv ...] [--snaps S.csv ...]
          [--games games.csv]
  python3 nflrows.py check OUT.json.gz STATS_PLAYER_WEEK.csv [...]     # % exact match vs nflverse weekly stats
  python3 nflrows.py show OUT.json.gz GAME_ID [PLAYER_ID]              # print actuals (grading helper)

COUNTING CONVENTIONS (chosen to match nflverse stats_player_week; see REPORT.md for match rates)
  * Only plays with play_type in pass / run / qb_kneel / qb_spike are counted. play_type 'no_play'
    (penalty-nullified, timeouts, etc.) never counts. Plays WITH an accepted penalty that still stand
    (play_type pass/run) DO count, same as the official book.
  * Two-point tries (two_point_attempt == 1) never count for any stat (they are not attempts, yards or TDs).
  * Pass attempts = complete + incomplete + interception (spikes count, sacks do not).
    Passing yards = passing_yards column (gross, sack yards NOT subtracted; lateral yards after the catch
    are included, as the official book does).
  * Sacks are kept separately (sk) and are not pass attempts or rushes.
  * Carries = rush_attempt == 1 (designed runs, QB scrambles AND kneels: the official book counts
    kneels as carries with their negative yards).
  * Laterals: the original receiver/rusher gets yards to the point of the lateral; the lateral receiver
    / rusher gets the rest (lateral_receiving_yards / lateral_rushing_yards). Laterals do not add a
    reception or carry for the second player.
  * Receiving/rushing TD goes to td_player_id when that player is the receiver/rusher (or lateral one).
    A teammate recovering a fumble in the end zone is NOT a rushing/receiving TD (it goes to otd).
  * otd = any other TD a player scores (kick/punt return, fumble recovery). Books' "anytime TD"
    generally counts these, so the grader's anytime TD = rt + rct + otd. The MODEL predicts rt + rct only.
  * Longest reception / rush (lr, lu) = longest single-play gain credited to the player
    (receiving_yards on the play, or rushing_yards), lateral yards excluded; 0 with no catch/carry.
  * Rows exist for every skill player (QB/RB/WR/TE/FB) who took an offensive snap (snap counts file)
    or recorded any stat, so games with zero targets are real zero rows, not missing rows.

OUTPUT (gzip JSON, compact):
  {"v":1, "cols":[...], "players":{gsis:[name,pos]}, "rows":[[...],...], "teams":[tcols], "trows":[[...]],
   "games":{gid:{season,week,stype,date,home,away,spread,total,hs,as,hs1,as1}}}
  load(path) returns dict(players, rows=list of dicts, team_rows=list of dicts, games).
"""
import csv
import gzip
import json
import sys
from collections import defaultdict

STATS = ["pa", "pc", "py", "ptd", "pi", "sk", "ra", "ry", "rt", "tg", "rc", "rcy", "rct", "lr", "lu", "otd"]
# pa pass att, pc completions, py pass yds, ptd pass TD, pi INT, sk sacks taken, ra carries, ry rush yds,
# rt rush TD, tg targets, rc receptions, rcy rec yds, rct rec TD, lr longest rec, lu longest rush, otd other TD
META = ["gid", "pid", "team", "opp", "home", "season", "week", "stype", "date", "snap", "tplays", "tplays_h"]
COLS = META + STATS + ["h_" + s for s in STATS]
TSTATS = ["pts", "opp_pts", "plays", "db", "pa", "ra", "py", "ry", "sk",
          "tg_WR", "rc_WR", "rcy_WR", "tg_TE", "rc_TE", "rcy_TE", "tg_RB", "rc_RB", "rcy_RB", "ra_RB", "ry_RB",
          "td_rush", "td_rec"]
TCOLS = ["gid", "team", "opp", "home", "season", "week", "stype", "date"] + TSTATS + ["h_" + s for s in TSTATS]
SKILL = {"QB", "RB", "WR", "TE", "FB", "HB"}
POS_MAP = {"FB": "RB", "HB": "RB"}
COUNTED = {"pass", "run", "qb_kneel", "qb_spike"}


def _i(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def _blank():
    return dict.fromkeys(STATS, 0)


def parse_pbp(path, acc, games, names):
    """Accumulate one pbp file. acc[(gid,pid)] = {'fg':stats,'h':stats,'team':..}"""
    f = gzip.open(path, "rt", newline="") if path.endswith(".gz") else open(path, newline="")
    rd = csv.reader(f)
    h = next(rd)
    ix = {k: i for i, k in enumerate(h)}
    last = {}
    for r in rd:
        g = lambda k: r[ix[k]]  # noqa: E731
        gid = g("game_id")
        half = g("game_half")
        gm = games.setdefault(gid, dict(season=_i(g("season")), week=_i(g("week")), stype=g("season_type"),
                                        date=g("game_date"), home=g("home_team"), away=g("away_team"),
                                        hs1=0, as1=0, hs=0, as_=0))
        hs, as_ = g("total_home_score"), g("total_away_score")
        if hs != "" and as_ != "":
            if half == "Half1":
                gm["hs1"], gm["as1"] = _i(hs), _i(as_)
            gm["hs"], gm["as_"] = _i(hs), _i(as_)
        pt = g("play_type")
        team = g("posteam")
        opp = g("defteam")
        h1 = half == "Half1"

        def add(pid, name, k, v, tm=team):
            if not pid:
                return
            names.setdefault(pid, name)
            a = acc.get((gid, pid))
            if a is None:
                a = acc[(gid, pid)] = dict(fg=_blank(), h=_blank(), team=tm)
            if k in ("lr", "lu"):
                a["fg"][k] = max(a["fg"][k], v)
                if h1:
                    a["h"][k] = max(a["h"][k], v)
            else:
                a["fg"][k] += v
                if h1:
                    a["h"][k] += v

        # non-offensive TDs scored by anyone (returns, fumble recoveries): otd
        tdp = g("td_player_id")
        if tdp and g("two_point_attempt") != "1":
            off_td = (pt in ("pass", "run") and g("td_team") == team and
                      tdp in (g("receiver_player_id"), g("rusher_player_id"), g("lateral_receiver_player_id"),
                              g("lateral_rusher_player_id"), g("passer_player_id")))
            if not off_td and pt not in ("no_play",):
                add(tdp, g("td_player_name"), "otd", 1, tm=g("td_team"))
        if pt not in COUNTED or g("two_point_attempt") == "1":
            continue
        last[gid] = 1
        sack = g("sack") == "1"
        passer, rusher, rcv = g("passer_player_id"), g("rusher_player_id"), g("receiver_player_id")
        lrcv, lrus = g("lateral_receiver_player_id"), g("lateral_rusher_player_id")
        if g("pass_attempt") == "1" and passer:
            if sack:
                add(passer, g("passer_player_name"), "sk", 1)
            else:
                add(passer, g("passer_player_name"), "pa", 1)
                if g("complete_pass") == "1":
                    add(passer, g("passer_player_name"), "pc", 1)
                    add(passer, g("passer_player_name"), "py", _i(g("passing_yards")))
                if g("interception") == "1":
                    add(passer, g("passer_player_name"), "pi", 1)
                if g("pass_touchdown") == "1":
                    add(passer, g("passer_player_name"), "ptd", 1)
        if rcv and not sack:
            add(rcv, g("receiver_player_name"), "tg", 1)
            if g("complete_pass") == "1":
                y = _i(g("receiving_yards"))
                add(rcv, g("receiver_player_name"), "rc", 1)
                add(rcv, g("receiver_player_name"), "rcy", y)
                add(rcv, g("receiver_player_name"), "lr", y)
            if lrcv:
                add(lrcv, g("lateral_receiver_player_name"), "rcy", _i(g("lateral_receiving_yards")))
            if g("pass_touchdown") == "1" and tdp in (rcv, lrcv):
                add(tdp, g("td_player_name"), "rct", 1)
        if g("rush_attempt") == "1" and rusher:
            y = _i(g("rushing_yards"))
            add(rusher, g("rusher_player_name"), "ra", 1)
            add(rusher, g("rusher_player_name"), "ry", y)
            add(rusher, g("rusher_player_name"), "lu", y)
            if lrus:
                add(lrus, g("lateral_rusher_player_name"), "ry", _i(g("lateral_rushing_yards")))
            if g("rush_touchdown") == "1" and tdp in (rusher, lrus):
                add(tdp, g("td_player_name"), "rt", 1)
    f.close()


def read_rosters(paths):
    """gsis -> (pos, name) per season, and pfr_id -> gsis."""
    pos, pfr = {}, {}
    for p in paths:
        with open(p, newline="") as fh:
            rr = list(csv.DictReader(fh))
        for r in rr:
            gs = r.get("gsis_id")
            if not gs:
                continue
            pos[(r["season"], gs)] = (r["position"], r["full_name"], r["team"])
            pos[gs] = (r["position"], r["full_name"], r["team"])
            if r.get("pfr_id"):
                pfr[r["pfr_id"]] = gs
    return pos, pfr


def read_snaps(paths, pfr):
    """(gid, gsis) -> (offense_pct, team, position)"""
    out = {}
    for p in paths:
        with open(p, newline="") as fh:
            rr = list(csv.DictReader(fh))
        for r in rr:
            gs = pfr.get(r["pfr_player_id"])
            if gs and _i(r["offense_snaps"]) > 0:
                out[(r["game_id"], gs)] = (round(float(r["offense_pct"] or 0), 3), r["team"], r["position"])
    return out


def build(pbp_paths, roster_paths=(), snap_paths=(), games_csv=None):
    acc, games, names = {}, {}, {}
    for p in pbp_paths:
        parse_pbp(p, acc, games, names)
    pos, pfr = read_rosters(roster_paths)
    snaps = read_snaps(snap_paths, pfr)
    # snap-only skill players: real zero rows
    for (gid, gs), (pct, tm, sp) in snaps.items():
        if gid in games and (gid, gs) not in acc and (sp in SKILL or pos.get(gs, ("",))[0] in SKILL):
            acc[(gid, gs)] = dict(fg=_blank(), h=_blank(), team=tm)
    if games_csv:
        with open(games_csv, newline="") as fh:
            grows = list(csv.DictReader(fh))
        for r in grows:
            if r["game_id"] in games:
                gm = games[r["game_id"]]
                gm["spread"] = float(r["spread_line"]) if r["spread_line"] else None   # + = home favored
                gm["total"] = float(r["total_line"]) if r["total_line"] else None
                gm["gametime"] = r["gametime"]
    players, rows = {}, []
    for (gid, pid), a in acc.items():
        gm = games[gid]
        season = str(gm["season"])
        pp = pos.get((season, pid)) or pos.get(pid) or ("", "", "")
        position = pp[0]
        if not position and (gid, pid) in snaps:
            position = snaps[(gid, pid)][2]
        position = POS_MAP.get(position, position)
        fg, hh = a["fg"], a["h"]
        if position not in ("QB", "RB", "WR", "TE") and not (fg["pa"] or fg["ra"] or fg["tg"] or fg["rt"] or fg["rct"]):
            continue      # defensive/special teams only (otd from returns on defenders): skip
        team = a["team"]
        opp = gm["away"] if team == gm["home"] else gm["home"]
        players[pid] = [pp[1] or names.get(pid, ""), position]
        sn = snaps.get((gid, pid))
        rows.append(dict(gid=gid, pid=pid, team=team, opp=opp, home=int(team == gm["home"]), season=gm["season"],
                         week=gm["week"], stype=gm["stype"], date=gm["date"], snap=sn[0] if sn else None,
                         tplays=0, tplays_h=0, **fg, **{"h_" + k: v for k, v in hh.items()}))
    # team-game rows
    trow = {}
    for gid, gm in games.items():
        for side in ("home", "away"):
            tm = gm[side]
            o = gm["away"] if side == "home" else gm["home"]
            t = dict(gid=gid, team=tm, opp=o, home=int(side == "home"), season=gm["season"], week=gm["week"],
                     stype=gm["stype"], date=gm["date"])
            for pre in ("", "h_"):
                for k in TSTATS:
                    t[pre + k] = 0
            t["pts"], t["opp_pts"] = (gm["hs"], gm["as_"]) if side == "home" else (gm["as_"], gm["hs"])
            t["h_pts"], t["h_opp_pts"] = (gm["hs1"], gm["as1"]) if side == "home" else (gm["as1"], gm["hs1"])
            trow[(gid, tm)] = t
    for r in rows:
        t = trow.get((r["gid"], r["team"]))
        if t is None:
            continue
        p = players[r["pid"]][1]
        for pre in ("", "h_"):
            t[pre + "pa"] += r[pre + "pa"]
            t[pre + "sk"] += r[pre + "sk"]
            t[pre + "ra"] += r[pre + "ra"]
            t[pre + "py"] += r[pre + "py"]
            t[pre + "ry"] += r[pre + "ry"]
            t[pre + "td_rush"] += r[pre + "rt"]
            t[pre + "td_rec"] += r[pre + "rct"]
            if p in ("WR", "TE", "RB"):
                t[pre + "tg_" + p] += r[pre + "tg"]
                t[pre + "rc_" + p] += r[pre + "rc"]
                t[pre + "rcy_" + p] += r[pre + "rcy"]
            if p == "RB":
                t[pre + "ra_RB"] += r[pre + "ra"]
                t[pre + "ry_RB"] += r[pre + "ry"]
    for t in trow.values():
        for pre in ("", "h_"):
            t[pre + "db"] = t[pre + "pa"] + t[pre + "sk"]
            t[pre + "plays"] = t[pre + "pa"] + t[pre + "sk"] + t[pre + "ra"]
    for r in rows:
        t = trow.get((r["gid"], r["team"]))
        if t:
            r["tplays"], r["tplays_h"] = t["plays"], t["h_plays"]
    for gm in games.values():
        gm["as"] = gm.pop("as_")
    rows.sort(key=lambda r: (r["date"], r["gid"], r["pid"]))
    return dict(players=players, rows=rows, team_rows=sorted(trow.values(), key=lambda t: (t["date"], t["gid"], t["team"])),
                games=games)


def dump(data, path):
    out = dict(v=1, cols=COLS, players=data["players"], tcols=TCOLS,
               rows=[[r[c] for c in COLS] for r in data["rows"]],
               trows=[[t[c] for c in TCOLS] for t in data["team_rows"]], games=data["games"])
    with gzip.open(path, "wt") as f:
        json.dump(out, f, separators=(",", ":"))


def load(path):
    with gzip.open(path, "rt") as f:
        d = json.load(f)
    cols, tcols = d["cols"], d["tcols"]
    return dict(players=d["players"], rows=[dict(zip(cols, r)) for r in d["rows"]],
                team_rows=[dict(zip(tcols, r)) for r in d["trows"]], games=d["games"])


# ---------------------------------------------------------------- grading
# market -> function(row, prefix) -> actual value. prefix "" = FG, "h_" = 1H.
MARKETS = {
    "pass_yds": lambda r, p: r[p + "py"], "pass_att": lambda r, p: r[p + "pa"], "pass_cmp": lambda r, p: r[p + "pc"],
    "pass_td": lambda r, p: r[p + "ptd"], "pass_int": lambda r, p: r[p + "pi"],
    "rush_yds": lambda r, p: r[p + "ry"], "rush_att": lambda r, p: r[p + "ra"],
    "rec": lambda r, p: r[p + "rc"], "rec_yds": lambda r, p: r[p + "rcy"],
    "rush_rec_yds": lambda r, p: r[p + "ry"] + r[p + "rcy"], "pass_rush_yds": lambda r, p: r[p + "py"] + r[p + "ry"],
    "anytime_td": lambda r, p: r[p + "rt"] + r[p + "rct"] + r[p + "otd"],
    "long_rec": lambda r, p: r[p + "lr"], "long_rush": lambda r, p: r[p + "lu"],
}


def actual(row, market, period="FG"):
    """Actual stat for grading. period 'FG' (incl OT) or '1H'. Anytime TD counts rush+rec+other TDs."""
    return MARKETS[market](row, "h_" if period == "1H" else "")


def grade(row, market, period, line, side="over"):
    """'win' / 'loss' / 'push' for a posted line. row None (player did not play) -> 'void' (books void DNPs)."""
    if row is None:
        return "void"
    a = actual(row, market, period)
    if a == line:
        return "push"
    return "win" if (a > line) == (side == "over") else "loss"


# ---------------------------------------------------------------- check vs nflverse weekly
CHECK = [("pa", "attempts"), ("pc", "completions"), ("py", "passing_yards"), ("ptd", "passing_tds"),
         ("pi", "passing_interceptions"), ("sk", "sacks_suffered"), ("ra", "carries"), ("ry", "rushing_yards"),
         ("rt", "rushing_tds"), ("tg", "targets"), ("rc", "receptions"), ("rcy", "receiving_yards"),
         ("rct", "receiving_tds")]


def check(data, stat_paths):
    idx = {(r["gid"], r["pid"]): r for r in data["rows"]}
    res = {k: [0, 0] for k, _ in CHECK}
    miss = defaultdict(list)
    for p in stat_paths:
        with open(p, newline="") as fh:
            stat_rows = list(csv.DictReader(fh))
        for s in stat_rows:
            if s["game_id"] not in data["games"]:
                continue
            if not any(_i(s[c]) for _, c in CHECK):
                continue
            r = idx.get((s["game_id"], s["player_id"]))
            for k, c in CHECK:
                v = _i(s[c])
                if r is None and v == 0:
                    continue
                res[k][1] += 1
                if r is not None and r[k] == v:
                    res[k][0] += 1
                elif len(miss[k]) < 5:
                    miss[k].append((s["game_id"], s["player_display_name"], v, r[k] if r else None))
    return res, miss


def main(argv):
    cmd = argv[1]
    if cmd == "build":
        out = argv[2]
        pbp, ros, sn, gcsv, mode = [], [], [], None, "pbp"
        for a in argv[3:]:
            if a in ("--roster", "--snaps", "--games"):
                mode = a[2:]
                continue
            if mode == "games":
                gcsv = a
            else:
                {"pbp": pbp, "roster": ros, "snaps": sn}[mode].append(a)
        d = build(pbp, ros, sn, gcsv)
        dump(d, out)
        print("rows", len(d["rows"]), "team rows", len(d["team_rows"]), "games", len(d["games"]))
    elif cmd == "check":
        d = load(argv[2])
        res, miss = check(d, argv[3:])
        for k, (a, n) in res.items():
            print("%-4s %6d / %6d  %.2f%%" % (k, a, n, 100.0 * a / max(n, 1)), miss.get(k, [])[:3])
    elif cmd == "show":
        d = load(argv[2])
        for r in d["rows"]:
            if r["gid"] == argv[3] and (len(argv) < 5 or r["pid"] == argv[4]):
                print(d["players"][r["pid"]], {k: r[k] for k in COLS if r[k]})


if __name__ == "__main__":
    main(sys.argv)
