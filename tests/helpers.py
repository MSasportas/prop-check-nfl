"""helpers.py: shared setup. A World is a temp folder with real (trimmed) fixtures: the 2026 schedule, the SEA/DEN/
HOU/JAX weekly rosters, ESPN injuries, the real SEA at DEN Hard Rock payload, and the committed nflverse rows.
Every source (ESPN, nflverse, open-meteo, the odds feed) reads files there; nothing touches the network."""
import contextlib
import copy
import io
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import common   # noqa: E402
import run      # noqa: E402

WORLD = os.path.join(HERE, "fixtures", "world")
ENV = ("PC_NOW", "PC_DATA_DIR", "PC_ODDS_FIXTURE_DIR", "PC_FEED_FIXTURE_DIR", "ODDS_API_KEYS", "PC_RAISE",
       "GITHUB_TOKEN", "GH_TOKEN")
SEA_DEN = "2026_06_SEA_DEN"
HOU_JAX = "2026_06_HOU_JAX"
WILSON = "00-0038797"          # Emanuel Wilson, SEA RB
JSN = "00-0038543"             # Jaxon Smith-Njigba, SEA WR (Questionable on the probe)
DARNOLD = "00-0034869"
NIX = "00-0039732"


def cfg():
    return common.load_config()


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def fresh(payload, iso_z):
    """Set every market's last_update (the probe payload is from Oct 13 04:27Z; the feed check wants fresh)."""
    s = re.sub(r'"last_update": "[^"]+"', '"last_update": "%s"' % iso_z, json.dumps(payload))
    return json.loads(s)


class World:
    def __init__(self, pool=(500, 500, 500, 500), with_rows=True):
        self.saved = {k: os.environ.get(k) for k in ENV}
        self.root = tempfile.mkdtemp(prefix="pcn_test_")
        self.data = os.path.join(self.root, "data")
        self.feed = os.path.join(self.root, "feed")
        self.odds = os.path.join(self.root, "odds")
        os.makedirs(os.path.join(self.data, "state"))
        shutil.copytree(os.path.join(WORLD, "odds"), self.odds)
        os.makedirs(self.feed)
        for f in ("games.csv", "roster_weekly_2026.csv", "espn_injuries.json"):
            shutil.copy(os.path.join(WORLD, f), os.path.join(self.feed, f))
        if with_rows:
            for s in (2024, 2025, 2026):
                shutil.copy(os.path.join(HERE, "fixtures", "rows", "rows%d.json.gz" % s),
                            os.path.join(self.data, "state", "rows%d.json.gz" % s))
        write(os.path.join(self.feed, "head_play_by_play_2026.csv.gz.json"), {"size": 1, "stamp": "seed"})
        write(os.path.join(self.data, "state", "stats_meta.json"),
              {"pbp": {"size": 1, "stamp": "seed"}, "built_for": {"size": 1, "stamp": "seed"},
               "built_at": "2026-10-13T06:00:00-04:00"})
        write(os.path.join(self.odds, "pool.json"), {"keys": list(pool)})
        self.injuries = load(os.path.join(WORLD, "espn_injuries.json"))
        self.sea_den = load(os.path.join(WORLD, "odds", "odds_%s.json" % SEA_DEN))
        self.set_odds(SEA_DEN, self.sea_den, "2026-10-13T14:00:00Z")
        for k in ENV:
            os.environ.pop(k, None)
        os.environ.update(PC_DATA_DIR=self.data, PC_FEED_FIXTURE_DIR=self.feed, PC_ODDS_FIXTURE_DIR=self.odds,
                          PC_RAISE="1")
        self.last = None
        self.output = ""
        self.prev_log = b""

    def close(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.root, ignore_errors=True)

    # ---- driving time
    def at(self, iso):
        """Run once at this Eastern time ('2026-10-13T10:05'). Returns slate.json."""
        os.environ["PC_NOW"] = iso if len(iso) > 16 else iso + ":00-04:00"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.last = run.run()
        self.output += buf.getvalue()
        log = self.log_bytes()
        assert log.startswith(self.prev_log), "the Record was changed in place (not append-only)"
        self.prev_log = log
        return self.slate()

    # ---- the world's sources
    def set_odds(self, gid, payload, last_update_z):
        write(os.path.join(self.odds, "odds_%s.json" % gid), fresh(payload, last_update_z))

    def set_price(self, gid, feed_key, who, side, price, line=None, last_update_z="2026-10-15T20:00:00Z"):
        """Change one outcome's price (and optionally line) in the game's odds fixture."""
        p = os.path.join(self.odds, "odds_%s.json" % gid)
        js = load(p)
        for b in js["bookmakers"]:
            if b["key"] != "hardrockbet_fl":
                continue
            for m in b["markets"]:
                m["last_update"] = last_update_z
                if m["key"] != feed_key:
                    continue
                for o in m["outcomes"]:
                    if o.get("description") == who and o["name"].lower() == side:
                        o["price"] = price
                        if line is not None:
                            o["point"] = line
        write(p, js)

    def drop_market(self, gid, feed_key):
        p = os.path.join(self.odds, "odds_%s.json" % gid)
        js = load(p)
        for b in js["bookmakers"]:
            b["markets"] = [m for m in b["markets"] if m["key"] != feed_key]
        write(p, js)

    def set_injury(self, team_espn_id, name, status, fantasy=None, date="2026-10-14T20:00Z", pos="WR", espn_id=None):
        inj = copy.deepcopy(self.injuries)
        t = next(t for t in inj["injuries"] if t["id"] == str(team_espn_id))
        t["injuries"] = [i for i in t["injuries"] if i["athlete"]["displayName"] != name]
        a = {"displayName": name, "position": {"abbreviation": pos},
             "team": {"id": str(team_espn_id), "abbreviation": ""},
             "links": [{"href": "https://www.espn.com/nfl/player/_/id/%s/x" % (espn_id or "1")}]}
        t["injuries"].append({"status": status, "date": date, "athlete": a,
                              "details": {"fantasyStatus": {"abbreviation": fantasy or status.upper()}}})
        self.injuries = inj
        write(os.path.join(self.feed, "espn_injuries.json"), inj)

    def post_inactives(self, team_espn_id, names_pos, date="2026-10-15T22:45Z"):
        """A team posts its inactives: each (name, pos) becomes Out / INACTIVE dated `date`."""
        for name, pos in names_pos:
            self.set_injury(team_espn_id, name, "Out", "INACTIVE", date, pos)

    def set_scoreboard(self, yyyymmdd, espn_id, state, away=None, home=None, completed=None):
        p = os.path.join(self.feed, "espn_scoreboard_%s.json" % yyyymmdd)
        js = load(p) if os.path.exists(p) else {"events": []}
        js["events"] = [e for e in js["events"] if e["id"] != str(espn_id)]
        comps = []
        for side, sc in (("away", away), ("home", home)):
            comps.append({"homeAway": side, "score": str(sc) if sc is not None else None, "team": {"abbreviation": ""}})
        name = {"pre": "STATUS_SCHEDULED", "in": "STATUS_IN_PROGRESS", "post": "STATUS_FINAL",
                "ppd": "STATUS_POSTPONED"}[state]
        js["events"].append({"id": str(espn_id), "competitions": [{"status": {"type": {
            "name": name, "state": "post" if state in ("post",) else ("pre" if state == "ppd" else state),
            "completed": state == "post" if completed is None else completed}}, "competitors": comps}]})
        write(p, js)

    def set_summary(self, espn_id, doc):
        write(os.path.join(self.feed, "espn_summary_%s.json" % espn_id), doc)

    # ---- reading results
    def path(self, *parts):
        return os.path.join(self.data, *parts)

    def slate(self):
        return common.read_json(self.path("slate.json"))

    def status(self):
        return common.read_json(self.path("status.json"))

    def log_bytes(self):
        p = self.path("record", "events.jsonl")
        if not os.path.exists(p):
            return b""
        with open(p, "rb") as f:
            return f.read()

    def events(self):
        return [json.loads(l) for l in self.log_bytes().decode().splitlines() if l]

    def pick(self, pid, slate=None, market=None):
        """The first pick on this player this week (posting order), optionally in one market."""
        slate = slate or self.slate()
        ps = [p for p in slate["picks"] if p["player_id"] == pid and (market is None or p["market"] == market)]
        ps.sort(key=lambda p: p["first_price_at"])
        return ps[0] if ps else None
