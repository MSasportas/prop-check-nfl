"""sched.py: the schedule (data/state/schedule.json) and live game state.

Two sources:
  nflverse games.csv   every game of the season: date, kickoff (Eastern), teams, ESPN id, roof, stadium,
                       final scores. Re-read every ops.schedule_refresh_hours (it is ~2 MB).
  ESPN scoreboard      live state (pre / in / post), finals and postponements, read only for dates that have a
                       game about to start, under way or waiting for its final.

The SLATE is one NFL week: it starts Tuesday 5:00 AM Eastern (after Monday night) and runs 7 days.
Its id is "<season>-W<week>" (e.g. 2026-W06) taken from the games in it.
Kickoff lock uses the scheduled time (the game is locked at the scheduled kickoff even if it starts late).
"""
import csv
import io
from datetime import datetime, timedelta

import common
import teams

GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=%s"


def path():
    return common.dpath("state", "schedule.json")


# ---------------------------------------------------------------- the slate (one NFL week)
def slate_anchor(now, cfg):
    """The Tuesday 5:00 AM Eastern that opened the current slate."""
    ops = cfg["ops"]
    n = now.astimezone(common.ET)
    wd = ops.get("rollover_weekday", 1)                      # Monday = 0, Tuesday = 1
    d = n.date() - timedelta(days=(n.weekday() - wd) % 7)
    a = datetime(d.year, d.month, d.day, ops["rollover_hour"], 0, tzinfo=common.ET)
    if a > n:
        a -= timedelta(days=7)
    return a


def slate_games(cache, now, cfg):
    a = slate_anchor(now, cfg)
    b = a + timedelta(days=7)
    out = [g for g in (cache or {}).get("games", {}).values()
           if a <= common.parse_iso(g["start"]) < b]
    return sorted(out, key=lambda g: (g["start"], g["id"]))


def slate_id(cache, now, cfg):
    gs = slate_games(cache, now, cfg)
    if gs:
        return "%d-W%02d" % (gs[0]["season"], gs[0]["week"])
    return "wk-%s" % slate_anchor(now, cfg).date().isoformat()


# ---------------------------------------------------------------- games.csv
def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _i(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def parse_games_csv(text, seasons):
    """games.csv text -> {game_id: game} for the given seasons."""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        try:
            season = int(r["season"])
        except (KeyError, ValueError):
            continue
        if season not in seasons:
            continue
        if not r.get("gameday") or not r.get("gametime"):
            continue
        hh, mm = (r["gametime"].split(":") + ["0"])[:2]
        y, mo, d = [int(x) for x in r["gameday"].split("-")]
        start = datetime(y, mo, d, int(hh), int(mm), tzinfo=common.ET)
        away, home = teams.norm_code(r["away_team"]), teams.norm_code(r["home_team"])
        out[r["game_id"]] = {
            "id": r["game_id"], "season": season, "week": _i(r["week"]), "type": r.get("game_type") or "REG",
            "date": r["gameday"], "start": common.iso(start), "away": away, "home": home,
            "espn": r.get("espn") or None, "roof": r.get("roof") or None, "stadium_id": r.get("stadium_id") or None,
            "stadium": r.get("stadium") or None, "neutral": r.get("location") == "Neutral",
            "spread_line": _f(r.get("spread_line")), "total_line": _f(r.get("total_line")),
            "away_score": _i(r.get("away_score")), "home_score": _i(r.get("home_score")),
            "away_qb": r.get("away_qb_id") or None, "home_qb": r.get("home_qb_id") or None,
        }
    return out


def update(now, cfg):
    """Refresh the cache. Returns (cache or None, status dict)."""
    ops = cfg["ops"]
    cache = common.read_json(path()) or {}
    cache.setdefault("games", {})
    cache.setdefault("live", {})
    before = common.dumps(cache, False)
    notes = []
    last = common.parse_iso(cache.get("games_at"))
    season = now.year if now.month >= 3 else now.year - 1
    refresh = last is None or now - last >= timedelta(hours=ops.get("schedule_refresh_hours", 6)) or now < last
    status = "ok"
    if refresh:
        body = common.feed_get("nflverse_games", GAMES_URL, "games.csv", timeout=60)
        if body is None:
            notes.append("games.csv did not load" + (", using the saved copy" if cache["games"] else ""))
            status = "failed"
        else:
            games = parse_games_csv(body.decode("utf-8", "replace"), {season})
            if games:
                old_live = cache.get("live", {})
                cache["games"] = games
                cache["live"] = {k: v for k, v in old_live.items() if k in games}
                cache["games_at"] = common.iso(now)
                cache["season"] = season
            else:
                notes.append("games.csv had no games for %d" % season)
                status = "failed"
    if not cache["games"]:
        return None, {"status": "failed", "note": "; ".join(notes) or "no schedule"}
    n_live = refresh_live(cache, now, cfg)
    if n_live is None:
        notes.append("ESPN scoreboard did not answer")
    elif n_live:
        notes.append("live state for %d game(s)" % n_live)
    if common.dumps(cache, False) != before:
        common.write_json(path(), cache, pretty=False)
    return cache, {"status": status, "note": "; ".join(notes) or "schedule read"}


def _needs_live(g, live, now):
    start = common.parse_iso(g["start"])
    lv = live.get(g["id"]) or {}
    if lv.get("final") or lv.get("postponed"):
        return False
    if g.get("away_score") is not None and g.get("home_score") is not None and now > start + timedelta(hours=3):
        return False                                       # games.csv already has the final
    return start - timedelta(minutes=30) <= now <= start + timedelta(days=3)


def refresh_live(cache, now, cfg):
    """ESPN scoreboard for dates that need it. Returns the number of games updated, or None on failure."""
    want = {}
    for g in cache["games"].values():
        if g.get("espn") and _needs_live(g, cache["live"], now):
            want.setdefault(g["date"].replace("-", ""), []).append(g)
    if not want:
        return 0
    by_espn = {g["espn"]: g for g in cache["games"].values() if g.get("espn")}
    n, failed = 0, False
    for d in sorted(want):
        js = common.feed_json("espn_scoreboard", SCOREBOARD_URL % d, "espn_scoreboard_%s.json" % d)
        if js is None:
            failed = True
            continue
        for e in js.get("events", []) or []:
            g = by_espn.get(str(e.get("id")))
            if g is None:
                continue
            lv = parse_espn_event(e)
            if lv is None:
                continue
            lv["at"] = common.iso(now)
            old = cache["live"].get(g["id"]) or {}
            if {k: v for k, v in old.items() if k != "at"} != {k: v for k, v in lv.items() if k != "at"}:
                cache["live"][g["id"]] = lv
                n += 1
    return None if failed and not n else n


def parse_espn_event(e):
    try:
        c = e["competitions"][0]
        st = c["status"]["type"]
    except (KeyError, IndexError, TypeError):
        return None
    name = (st.get("name") or "").upper()
    out = {"state": st.get("state"), "final": bool(st.get("completed")) and st.get("state") == "post",
           "postponed": "POSTPONED" in name or "CANCEL" in name, "detail": st.get("shortDetail") or st.get("detail"),
           "period": (c.get("status") or {}).get("period")}
    for x in c.get("competitors", []):
        code = teams.code_for_espn(x.get("team", {}).get("abbreviation"), x.get("team", {}).get("id"))
        side = x.get("homeAway")
        if side in ("home", "away"):
            out[side + "_score"] = _i(x.get("score"))
            out[side + "_espn_team"] = code
    return out


# ---------------------------------------------------------------- questions the rest of the code asks
def live(cache, gid):
    return ((cache or {}).get("live") or {}).get(gid) or {}


def started(game, now, cache=None):
    """Kicked off: ESPN says in / post, or the scheduled kickoff has passed."""
    lv = live(cache, game["id"]) if cache else {}
    if lv.get("state") in ("in", "post") and not lv.get("postponed"):
        return True
    return now >= common.parse_iso(game["start"])


def is_final(game, cache):
    lv = live(cache, game["id"])
    if lv.get("final"):
        return True
    return game.get("away_score") is not None and game.get("home_score") is not None


def is_postponed(game, cache):
    return bool(live(cache, game["id"]).get("postponed"))


def games_left_before_reset(cache, now):
    """Games not started yet with kickoff before the 1st of next month (the odds keys reset on the 1st).
    Returns a list of games sorted by kickoff."""
    first = (now.date().replace(day=1) + timedelta(days=32)).replace(day=1)
    reset = datetime(first.year, first.month, 1, tzinfo=common.ET)
    out = [g for g in (cache or {}).get("games", {}).values()
           if now < common.parse_iso(g["start"]) < reset and not is_postponed(g, cache)]
    return sorted(out, key=lambda g: (g["start"], g["id"]))


def game_days(games):
    per = {}
    for g in games:
        per[g["date"]] = per.get(g["date"], 0) + 1
    return sorted(per.items())
