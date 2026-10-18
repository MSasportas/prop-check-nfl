"""injuries.py: injury designations, the final injury report and game-day inactives.

Sources
  ESPN injuries (site.api.espn.com .../injuries): the whole league in one answer. Each entry has a status
    (Active, Questionable, Doubtful, Out, Injured Reserve, Suspension...) and details.fantasyStatus. On game day
    ESPN marks each team's inactive players with fantasyStatus "INACTIVE" (status "Out"), dated when the team
    announces them (about 90 minutes before kickoff). Verified on the Oct 12 2026 payload: BUF 7 and LAR 5
    INACTIVE entries dated 22:49-23:00Z for a 00:15Z kickoff.
  nflverse injuries_<season>.csv: the official weekly report (report_status). Used only when ESPN does not answer.

State: data/state/injuries.json (skill players only: QB RB WR TE FB), data/state/inactives.json.

Settledness of a pick (per game, per team; the contract's words):
  early          the final injury report for the game is not in yet
  injury_report  the final report time (values.final_report + final_report_grace_min) has passed and ESPN was
                 read after it
  inactives      the player's team posted its inactives (ops.inactives_min or more INACTIVE entries for the team,
                 any position, dated from ops.inactives_oldest_min before kickoff) and he is not on them
"""
import csv
import io
import os
import re
from datetime import datetime, timedelta

import common
import names
import teams

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
NFLVERSE_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_%d.csv"
SKILL = {"QB", "RB", "WR", "TE", "FB", "HB"}
OUT_STATUSES = {"Out", "Doubtful", "Injured Reserve", "Suspension", "Reserve-Ret", "PUP", "NFI"}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def path():
    return common.dpath("state", "injuries.json")


def inactives_path():
    return common.dpath("state", "inactives.json")


def load():
    return common.read_json(path(), {}) or {}


def load_inactives():
    return common.read_json(inactives_path(), {}) or {}


# ---------------------------------------------------------------- reading ESPN
_ID = re.compile(r"/id/(\d+)/")


def espn_athlete_id(a):
    if a.get("id"):
        return str(a["id"])
    for l in a.get("links", []) or []:
        m = _ID.search(l.get("href", ""))
        if m:
            return m.group(1)
    return None


def parse_espn(js, roster):
    """ESPN injuries answer -> list of dict(pid, espn_id, name, team, pos, status, fantasy, date, comment), every
    position (inactive counts need them all). pid (gsis id) is matched for skill players only: ESPN id via the
    roster, else the name rules in names.py within the team."""
    by_espn = {p.get("espn_id"): pid for pid, p in (roster or {}).items() if p.get("espn_id")}
    matchers = {}
    out = []
    for t in (js or {}).get("injuries", []) or []:
        code = teams.code_for_espn(None, t.get("id"))
        for i in t.get("injuries", []) or []:
            a = i.get("athlete") or {}
            pos = ((a.get("position") or {}).get("abbreviation") or "").upper()
            team = code or teams.code_for_espn((a.get("team") or {}).get("abbreviation"), (a.get("team") or {}).get("id"))
            eid = espn_athlete_id(a)
            pid = by_espn.get(eid) if pos in SKILL else None
            if pid is None and team and pos in SKILL:
                if team not in matchers:
                    matchers[team] = names.Matcher(people_on(roster, team))
                hit = matchers[team].find(a.get("displayName") or "")
                pid = hit["id"] if hit else None
            fs = (((i.get("details") or {}).get("fantasyStatus")) or {}).get("abbreviation")
            out.append({"pid": pid, "espn_id": eid, "name": a.get("displayName"), "team": team, "pos": pos,
                        "status": i.get("status"), "fantasy": fs, "date": _espn_date(i.get("date")),
                        "detail": (i.get("details") or {}).get("type"), "comment": (i.get("shortComment") or "")[:160]})
    return out


def _espn_date(s):
    try:
        return common.iso(common.parse_iso(s.replace("Z", ":00Z") if s and len(s) == 17 else s))
    except (TypeError, ValueError, AttributeError):
        return None


def people_on(roster, team):
    return [dict(p, id=pid) for pid, p in (roster or {}).items() if p.get("team") == team]


# ---------------------------------------------------------------- nflverse fallback
def parse_nflverse(text, roster, season):
    """nflverse injuries csv (official weekly report) -> the same entries as parse_espn. Fallback only."""
    rows = list(csv.DictReader(io.StringIO(text)))
    rows = [r for r in rows if r.get("season") == str(season) and r.get("game_type", "REG") in ("REG", "POST")]
    if not rows:
        return []
    wk = max(int(r["week"]) for r in rows if r.get("week", "").isdigit())
    out = []
    for r in rows:
        if r.get("week") != str(wk):
            continue
        st = r.get("report_status") or ""
        if not st:
            continue
        out.append({"pid": r.get("gsis_id") or None, "espn_id": None, "name": r.get("full_name"),
                    "team": teams.norm_code(r.get("team")), "pos": r.get("position"), "status": st,
                    "fantasy": None, "date": None, "detail": r.get("report_primary_injury") or None, "comment": ""})
    return out


# ---------------------------------------------------------------- the step
def check(now, games, cfg, roster, season, started, force=False, week_key=None):
    """Refresh injuries (and inactives inside their window). Returns a status dict with 'state' and 'inactives'."""
    ops = cfg["ops"]
    st = load()
    last = common.parse_iso(st.get("at"))
    due = force or last is None or now < last or now - last >= timedelta(minutes=_cadence(now, games, ops, cfg))
    for g in games:                                          # the first read after a final report counts it in
        t = report_counts_at(g, cfg)
        if last is not None and last < t <= now and not started.get(g["id"]):
            due = True
    if not due:
        return {"status": "skipped", "note": "read at %s" % common.fmt_time(last), "state": st,
                "inactives": load_inactives()}
    js = common.feed_json("espn_injuries", ESPN_URL, "espn_injuries.json", timeout=40, retries=1)
    entries, source = None, None
    if js is not None:
        entries, source = parse_espn(js, roster), "espn"
    else:
        body = common.feed_get("nflverse_injuries", NFLVERSE_URL % season, "injuries_%d.csv" % season, timeout=60)
        if body is not None:
            entries, source = parse_nflverse(body.decode("utf-8", "replace"), roster, season), "nflverse"
    if entries is None:
        st["last_fail_at"] = common.iso(now)
        common.write_json(path(), st)
        return {"status": "failed", "note": "ESPN injuries and nflverse injuries did not answer", "state": st,
                "inactives": load_inactives()}
    players = {}
    for e in entries:
        skill = (e.get("pos") or "").upper() in SKILL
        if not skill and e.get("status") in (None, "", "Active"):
            continue                                        # healthy non-skill players: nothing to keep
        key = e["pid"] or "name:%s:%s" % (e["team"], names.full_key(e["name"] or ""))
        players[key] = {k: e.get(k) for k in ("name", "team", "pos", "status", "fantasy", "date", "detail", "espn_id")}
        if skill and e.get("status") not in (None, "", "Active"):
            players[key]["comment"] = e.get("comment")
    # designations seen this week stick (Sharp: a player listed Questionable or worse gets no new pick for that
    # game even if he is later active). Reset when the week (slate anchor) changes.
    week = st.get("week") if st.get("week") == week_key else None
    flagged = dict(st.get("flagged") or {}) if week else {}
    for key, p in players.items():
        if p.get("status") not in (None, "", "Active") and (p.get("pos") or "").upper() in SKILL:
            flagged.setdefault(key, {"status": p["status"], "at": common.iso(now)})
    new = {"at": common.iso(now), "source": source, "players": players, "last_fail_at": None, "week": week_key,
           "flagged": flagged}
    if source == "nflverse":
        new["fallback_at"] = common.iso(now)
    common.write_json(path(), new)
    ina = update_inactives(now, games, entries, cfg, started)
    n_out = sum(1 for p in players.values() if p["status"] in OUT_STATUSES)
    n_q = sum(1 for p in players.values() if p["status"] == "Questionable")
    return {"status": "ok", "note": "%s: %d out, %d questionable" % (source, n_out, n_q), "state": new,
            "inactives": ina}


def _cadence(now, games, ops, cfg=None):
    """Minutes between reads: every cycle inside any game's inactives window or right after a final report time,
    else ops.injuries_every_min."""
    win = (cfg or {}).get("values", {}).get("inactives_earliest_min", 100)
    for g in games:
        k = common.parse_iso(g["start"])
        if k - timedelta(minutes=win) <= now <= k:
            return 0
    return ops.get("injuries_every_min", 60)


def update_inactives(now, games, entries, cfg, started):
    """For each game inside its inactives window: has each team posted its inactives, and which skill players
    are on them. A team counts as posted when ESPN lists ops.inactives_min or more INACTIVE players (any
    position) for it, dated between ops.inactives_oldest_min before kickoff and 30 minutes after."""
    ops = cfg["ops"]
    st = load_inactives()
    win = cfg["values"].get("inactives_earliest_min", 100)
    oldest = ops.get("inactives_oldest_min", 180)
    need = cfg["values"].get("inactives_min_per_team", ops.get("inactives_min", 3))
    before = common.dumps(st, False)
    keep = {g["id"] for g in games}
    for gid in list(st):                                     # kept 10 days: the grader uses it to prove a DNP
        k = common.parse_iso((st[gid] or {}).get("_kickoff"))
        if gid not in keep and (k is None or now - k > timedelta(days=10)):
            st.pop(gid)
    for g in games:
        k = common.parse_iso(g["start"])
        if not (k - timedelta(minutes=win) <= now <= k + timedelta(minutes=30)):
            continue
        rec = st.setdefault(g["id"], {})
        rec["_kickoff"] = g["start"]
        for team in (g["away"], g["home"]):
            listed = []
            for e in entries:
                if e["team"] != team or (e.get("fantasy") or "").upper() != "INACTIVE" or not e.get("date"):
                    continue
                d = common.parse_iso(e["date"])
                if k - timedelta(minutes=oldest) <= d <= k + timedelta(minutes=30):
                    listed.append(e)
            confirmed = len(listed) >= need
            t = rec.get(team) or {}
            rec[team] = {"confirmed": confirmed, "count": len(listed),
                         "players": sorted(({"pid": e["pid"], "name": e["name"], "pos": e["pos"]} for e in listed),
                                           key=lambda x: x["name"] or ""),
                         "confirmed_at": (t.get("confirmed_at") or common.iso(now)) if confirmed else None}
    if common.dumps(st, False) != before or not os.path.exists(inactives_path()):
        common.write_json(inactives_path(), st)
    return st


# ---------------------------------------------------------------- questions the engine asks
DAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def final_report_time(game, cfg):
    """When the final injury report for this game is out (Eastern). values.final_report maps the game's weekday
    to "Day HH:MM" (the latest such day before kickoff); values.final_report_overrides maps a game date to
    "YYYY-MM-DD HH:MM" (holiday weeks)."""
    v = cfg["values"]
    k = common.parse_iso(game["start"])
    ov = (v.get("final_report_overrides") or {}).get(k.date().isoformat())
    if ov:
        d, _, hm = ov.partition(" ")
        y, mo, dd = [int(x) for x in d.split("-")]
        hh, mm = [int(x) for x in hm.split(":")]
        return datetime(y, mo, dd, hh, mm, tzinfo=common.ET)
    wd = [x for x, n in DAYS.items() if n == k.weekday()][0]
    spec = (v.get("final_report") or {}).get(wd, "Fri 16:00")
    day, _, hm = spec.partition(" ")
    back = (k.weekday() - DAYS[day]) % 7 or 7
    d = (k - timedelta(days=back)).date()
    hh, mm = [int(x) for x in hm.split(":")]
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=common.ET)


def report_counts_at(game, cfg):
    """The report counts as in from its time + final_report_grace_min (ESPN needs a little while)."""
    return final_report_time(game, cfg) + timedelta(minutes=cfg["values"].get("final_report_grace_min", 60))


def report_in(game, cfg, inj_state, now):
    t = report_counts_at(game, cfg)
    at = common.parse_iso((inj_state or {}).get("at"))
    return now >= t and at is not None and at >= t


def team_confirmed(ina_state, game, team):
    rec = ((ina_state or {}).get(game["id"]) or {}).get(team) or {}
    return bool(rec.get("confirmed"))


def ever_flagged(inj_state, pid, team=None, name=None):
    """The first designation he carried this week (sticky), or None."""
    fl = (inj_state or {}).get("flagged") or {}
    x = fl.get(pid)
    if x is None and name and team:
        x = fl.get("name:%s:%s" % (team, names.full_key(name)))
    return x.get("status") if x else None


def player_status(inj_state, ina_state, game, team, pid, name=None):
    """-> status string or None (healthy). 'Inactive' once he is on his team's inactive list for this game,
    else ESPN's designation (Questionable, Doubtful, Out, Injured Reserve, ...)."""
    rec = ((ina_state or {}).get(game["id"]) or {}).get(team) or {}
    for x in rec.get("players", []):
        if x.get("pid") == pid or (name and x.get("name") and names.full_key(x["name"]) == names.full_key(name)):
            return "Inactive"
    players = (inj_state or {}).get("players") or {}
    p = players.get(pid)
    if p is None and name:
        p = players.get("name:%s:%s" % (team, names.full_key(name)))
    if not p:
        return None
    if (p.get("fantasy") or "").upper() == "INACTIVE":
        d = common.parse_iso(p.get("date")) if p.get("date") else None
        k = common.parse_iso(game["start"])
        if d is not None and k - timedelta(hours=6) <= d <= k + timedelta(minutes=30):
            return "Inactive"
    st = p.get("status")
    return None if st in (None, "", "Active") else st


def game_confirmed(ina_state, game):
    """Inactives count as in for a game only when BOTH teams posted theirs (Sharp: inactives_rule)."""
    return team_confirmed(ina_state, game, game["away"]) and team_confirmed(ina_state, game, game["home"])


def settled(game, team, cfg, inj_state, ina_state, now):
    """early | injury_report | inactives for one game (team kept for the call shape; the state is per game)."""
    if game_confirmed(ina_state, game):
        return "inactives"
    if report_in(game, cfg, inj_state, now):
        return "injury_report"
    return "early"
