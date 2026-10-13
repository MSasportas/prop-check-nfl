"""odds.py: Hard Rock prices from the-odds-api (americanfootball_nfl, bookmaker hardrockbet_fl).

What lives here
  keys      env ODDS_API_KEYS only (comma separated). Never printed, never written. A key that is used up or
            refused (401 / 429) is skipped and the next one is tried.
  ledger    data/state/credits.json: what each key has left (by POSITION, never the key text) and what we spent.
            The x-requests-remaining header is trusted on every answer (the keys may be shared with other tools).
  plan      allowance = pool credits left / games not started before the keys reset on the 1st. The allowance
            picks a tier (config ops.tiers); the tier decides which pulls run and which markets they ask for.
  pulls     F  full pull of the tier's market set + game lines, once props are posted (from f_hours_before
               kickoff, 8 AM-midnight). A market not posted yet costs nothing and is asked again later.
            R  refresh after the game's final injury report: markets holding a live pick or a near-pick
               (+ main game lines), tiers with r.
            C  check after inactives: same markets, once both teams posted inactives (from c_before_min before
               kickoff) or at c_fallback_min regardless.
            Never a game that has kicked off. Every answer is saved to data/odds/<date>.json (our price history).
  calls     player props: bookmakers=hardrockbet_fl only. Game lines: bookmakers=hardrockbet_fl,draftkings
            (DraftKings is used ONLY for spreads_h1, labelled; it can never be a pick). Cost = unique markets
            returned, so two calls cost the same as one. player_receptions_h1 is never requested (it fails the call).

With PC_ODDS_FIXTURE_DIR set nothing here touches the network (see _fixture_*).
"""
import json
import math
import os
import urllib.parse
from datetime import datetime, timedelta

import common
import names
import teams

API = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
SOURCE = {"ok": 0, "failed": 0}
KEYS_REFUSED_TEXT = "Hard Rock prices are off: the odds keys were refused. New keys are needed."
GAME_KEYS = ("h2h", "spreads", "totals", "spreads_h1", "totals_h1", "team_totals", "team_totals_h1")


# ---------------------------------------------------------------- price arithmetic
def break_even(american):
    """-200 -> 0.6667, +120 -> 0.4545"""
    return (-american) / (-american + 100.0) if american < 0 else 100.0 / (american + 100.0)


def decimal(american):
    """-200 -> 1.5, +120 -> 2.2"""
    return 1.0 + (100.0 / -american if american < 0 else american / 100.0)


def american(dec):
    """Decimal price -> American integer (2.65 -> +165, 1.5 -> -200)."""
    if dec >= 2.0:
        return int(round((dec - 1.0) * 100))
    return int(round(-100.0 / (dec - 1.0)))


def fair_two_sided(price_side, price_other):
    """De-vig: our side's share of the two break-evens."""
    a, b = break_even(price_side), break_even(price_other)
    return a / (a + b)


def fair_one_sided(price, hold):
    """Only one side is posted (anytime TD 'Yes', alternates): assume the book's usual hold on it."""
    return break_even(price) / (1.0 + hold)


def hold(price_a, price_b):
    return break_even(price_a) + break_even(price_b) - 1.0


# ---------------------------------------------------------------- keys and ledger (ported from NHL, proven)
def load_keys():
    raw = os.environ.get("ODDS_API_KEYS", "")
    keys = [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]
    for k in keys:
        common.register_secret(k)
    return keys


def fixture_dir():
    return os.environ.get("PC_ODDS_FIXTURE_DIR")


def ledger_path():
    return common.dpath("state", "credits.json")


class Pool:
    """The key pool and its ledger. Keys are known only by position (n = 1, 2, ...)."""

    def __init__(self, keys, now):
        self.keys, self.now = keys, now
        self.fixture = bool(fixture_dir())
        led = common.read_json(ledger_path(), {}) or {}
        self.spent = led.get("spent", [])
        saved = led.get("keys", [])
        month = now.strftime("%Y-%m")
        if self.fixture:
            start = (common.read_json(os.path.join(fixture_dir(), "pool.json"), {}) or {}).get("keys", [500])
            self.keys = ["fixture-key-%d" % (i + 1) for i in range(len(start))]
        self.state = []
        for i in range(len(self.keys)):
            s = saved[i] if i < len(saved) and led.get("count") == len(self.keys) else {}
            left = s.get("left")
            if s.get("month") != month:
                left = None                                # keys refill on the 1st: forget last month's count
            if self.fixture and left is None:
                left = start[i]
            status = "ok" if left != 0 else "empty"
            if s.get("status") == "invalid" and s.get("month") == month and not self.fixture:
                status = "invalid"
            self.state.append({"n": i + 1, "left": left, "status": status,
                               "checked_at": s.get("checked_at") if left is not None or status == "invalid" else None,
                               "month": month})

    def count(self):
        return len(self.keys)

    def unknown(self):
        return sum(1 for s in self.state if s["left"] is None or s["status"] == "invalid")

    def known(self):
        return any(s["left"] is not None and s["status"] != "invalid" for s in self.state)

    def all_used_up(self):
        return bool(self.state) and all(s["left"] == 0 for s in self.state)

    def all_refused(self):
        return keys_refused(self.state, len(self.state))

    def total_left(self):
        return sum(s["left"] for s in self.state if s["left"] is not None and s["status"] != "invalid")

    def public(self):
        return [{"n": s["n"], "left": s["left"], "status": s["status"]} for s in self.state]

    def spent_on_game(self, gid):
        return sum(e["cost"] for e in self.spent if e.get("game_id") == gid)

    def note_headers(self, i, headers, pull=None, game_id=None, returned=0):
        s = self.state[i]
        try:
            s["left"] = int(float(headers.get("x-requests-remaining")))
        except (TypeError, ValueError):
            if s["left"] is not None and returned:
                s["left"] = max(0, s["left"] - returned)
        try:
            cost = int(float(headers.get("x-requests-last")))
        except (TypeError, ValueError):
            cost = returned
        s["checked_at"] = common.iso(self.now)
        s["status"] = "empty" if s["left"] == 0 else "ok"
        if cost:
            self.spent.append({"at": common.iso(self.now), "date": self.now.date().isoformat(), "pull": pull,
                               "game_id": game_id, "cost": cost, "key": s["n"]})
        return cost

    def save(self):
        keep_from = (self.now - timedelta(days=62)).date().isoformat()
        self.spent = [e for e in self.spent if e["date"] >= keep_from]
        common.write_json(ledger_path(), {"count": len(self.keys), "keys": self.state, "spent": self.spent})

    def candidates(self, need):
        return [i for i, s in enumerate(self.state) if s["status"] == "ok" and (s["left"] is None or s["left"] >= need)]


def keys_refused(key_state, key_count):
    ks = list(key_state or [])
    if not ks or (key_count and len(ks) != key_count):
        return False
    return all(k.get("status") == "invalid" or k.get("left") == 0 for k in ks) and \
        any(k.get("status") == "invalid" for k in ks)


# ---------------------------------------------------------------- the client
class Client:
    def __init__(self, pool, cfg):
        self.pool, self.cfg = pool, cfg
        self.errors = []

    def _get(self, path, params, need, pull=None, game_id=None):
        """Try keys in order until one answers. Returns (json or None, cost)."""
        for i in self.pool.candidates(need):
            q = urllib.parse.urlencode(dict(params, apiKey=self.pool.keys[i]))
            r = common.http_get("%s%s?%s" % (API, path, q), timeout=30)
            if r.ok:
                js = r.json()
                if js is None:
                    self.errors.append("odds feed sent an unreadable answer")
                    SOURCE["failed"] += 1
                    return None, 0
                returned = markets_returned(js) if need else 0
                cost = self.pool.note_headers(i, r.headers, pull, game_id, returned)
                SOURCE["ok"] += 1
                return js, cost
            if r.status in (401, 429):
                body = r.text().upper()
                st = self.pool.state[i]
                if "OUT_OF_USAGE" in body or "QUOTA" in body or "USAGE" in body:
                    st["left"], st["status"] = 0, "empty"
                elif r.status == 401:
                    st["status"] = "invalid"
                else:
                    st["status"] = "limited"
                st["checked_at"] = common.iso(self.pool.now)
                self.errors.append("key %d refused (%d), trying the next" % (i + 1, r.status))
                continue
            if r.status in (404, 422):
                if r.status == 422:
                    self.errors.append("odds feed refused the request (422): a market key may be invalid")
                SOURCE["ok"] += 1
                return {}, 0
            self.errors.append("odds feed did not answer (%s)" % (r.error or r.status))
            SOURCE["failed"] += 1
            return None, 0
        self.errors.append("every odds key was refused" if self.pool.all_refused() else "no odds key with credits left")
        return None, 0

    def _fixture_json(self, name):
        try:
            with open(os.path.join(fixture_dir(), name), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _fixture_spend(self, cost, pull, game_id):
        for i in self.pool.candidates(cost):
            st = self.pool.state[i]
            st["left"] -= cost
            st["checked_at"] = common.iso(self.pool.now)
            st["status"] = "empty" if st["left"] == 0 else "ok"
            if cost:
                self.pool.spent.append({"at": common.iso(self.pool.now), "date": self.pool.now.date().isoformat(),
                                        "pull": pull, "game_id": game_id, "cost": cost, "key": st["n"]})
            return True
        return False

    def refresh_keys(self, only_unknown=False):
        """The free events call, once per key, so we know what each key has left. Returns the events (or None)."""
        if self.pool.fixture:
            ev = self._fixture_json("events.json")
            SOURCE["ok" if ev is not None else "failed"] += 1
            if ev is None:
                self.errors.append("odds feed did not answer (fixture has no events.json)")
            return ev
        events = None
        for i in range(self.pool.count()):
            st = self.pool.state[i]
            if only_unknown and st["left"] is not None and st["status"] != "invalid":
                continue
            q = urllib.parse.urlencode({"apiKey": self.pool.keys[i], "dateFormat": "iso"})
            r = common.http_get("%s/events?%s" % (API, q), timeout=30)
            st = self.pool.state[i]
            if r.ok and r.json() is not None:
                self.pool.note_headers(i, r.headers)
                events = events if events is not None else r.json()
                SOURCE["ok"] += 1
            elif r.status in (401, 429):
                body = r.text().upper()
                if "USAGE" in body or "QUOTA" in body:
                    st["left"], st["status"] = 0, "empty"
                else:
                    st["status"] = "invalid" if r.status == 401 else "limited"
                st["checked_at"] = common.iso(self.pool.now)
                self.errors.append("key %d refused (%d)" % (i + 1, r.status))
            else:
                self.errors.append("odds feed did not answer (%s)" % (r.error or r.status))
                SOURCE["failed"] += 1
        return events

    def event_markets(self, event_id, game_id):
        """The market keys Hard Rock lists for one event (costs 1). Returns a sorted list, or None."""
        ops = self.cfg["ops"]
        if self.pool.fixture:
            js = self._fixture_json("markets_%s.json" % event_id) or self._fixture_json("markets_%s.json" % game_id) \
                or self._fixture_json("markets_default.json")
            if js is None:
                self.errors.append("odds feed did not answer (no markets fixture for %s)" % game_id)
                SOURCE["failed"] += 1
                return None
            if not self._fixture_spend(1, "M", game_id):
                return None
        else:
            js, _ = self._get("/events/%s/markets" % event_id, {"bookmakers": ops["book"], "dateFormat": "iso"},
                              1, "M", game_id)
            if not js:
                return None
        for b in js.get("bookmakers", []) or []:
            if b.get("key") == ops["book"]:
                return sorted(m.get("key") for m in b.get("markets", []) if m.get("key"))
        return []

    def event_odds(self, event_id, game, market_keys, books, pull):
        """Prices for one game. Returns (payload or None, cost). None = the feed failed (retry later)."""
        bad = set(self.cfg["ops"].get("never_request", []))
        market_keys = [m for m in market_keys if m not in bad]
        if not market_keys:
            return {"bookmakers": []}, 0
        if self.pool.fixture:
            js = self._fixture_json("odds_%s.json" % game["id"]) or self._fixture_json("odds_%s.json" % event_id)
            if js is None:
                self.errors.append("odds feed did not answer (no fixture for %s at %s)" % (game["away"], game["home"]))
                SOURCE["failed"] += 1
                return None, 0
            js = json.loads(json.dumps(js))
            js["bookmakers"] = [b for b in js.get("bookmakers", []) if b.get("key") in books]
            for b in js["bookmakers"]:                      # the real feed returns only what was asked for
                b["markets"] = [m for m in b.get("markets", []) if m["key"] in market_keys]
            cost = markets_returned(js)
            if not self._fixture_spend(cost, pull, game["id"]):
                self.errors.append("no odds key with credits left")
                return None, 0
            SOURCE["ok"] += 1
            return js, cost
        params = {"bookmakers": ",".join(books), "markets": ",".join(market_keys), "oddsFormat": "american",
                  "dateFormat": "iso"}
        return self._get("/events/%s/odds" % event_id, params, len(market_keys), pull, game["id"])


def markets_returned(js):
    if not isinstance(js, dict):
        return 0
    keys = set()
    for b in js.get("bookmakers", []) or []:
        for m in b.get("markets", []) or []:
            keys.add(m.get("key"))
    return len(keys)


# ---------------------------------------------------------------- market keys
def feed_to_market(cfg):
    """feed key -> (market name in ops.markets e.g. 'rec' or 'rec_yds_1h', alternate bool)."""
    out = {}
    for name, m in cfg["ops"]["markets"].items():
        if m.get("feed_key"):
            out[m["feed_key"]] = (name, False)
        if m.get("alt_key"):
            out[m["alt_key"]] = (name, True)
    return out


def is_player_key(k):
    return k.startswith("player_")


# ---------------------------------------------------------------- the plan (pure functions, easy to test)
# Which pulls each tier runs (Sharp, ops._tiers). M markets check, F full pull, D daily 10 AM re-check of markets
# holding open picks, R refresh after the final injury report, C pre-kickoff check; "c" = C only for games that
# hold an open pick. "below" = under the lite threshold: R only, earliest kickoff first, while credits last.
TIER_PULLS = {"full_plus": "MFDRC", "full": "MFRC", "standard": "MFC", "basic": "MFc", "lite": "MRC", "below": "MR"}


def make_plan(pool_left, games_left, cfg):
    v = cfg["values"]
    share = v.get("pool_share", 1.0)
    allowance = (share * pool_left / float(games_left)) if games_left else float(pool_left or 0)
    name = "below"
    for t, lo in sorted(v["tiers"].items(), key=lambda kv: -kv[1]):
        if allowance >= lo:
            name = t
            break
    pulls = (cfg["ops"].get("tier_pulls") or TIER_PULLS).get(name, "MR")
    return {"allowance": round(allowance, 2), "tier": name, "pulls": pulls, "pool_at_plan": pool_left,
            "games_left": games_left}


def _at_clock(day, hhmm):
    hh, mm = [int(x) for x in hhmm.split(":")]
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=common.ET)


def check_slot(now, game, cfg):
    """The latest f_check_times slot at or before now, inside the window that opens f_days_before days before
    kickoff. None before the window opens."""
    v = cfg["values"]
    k = common.parse_iso(game["start"])
    first_day = (k - timedelta(days=v["f_days_before"])).date()
    best = None
    for back in range(0, 8):
        d = (now - timedelta(days=back)).date()
        if d < first_day:
            break
        for t in v["f_check_times"]:
            at = _at_clock(d, t)
            if at <= now and (best is None or at > best):
                best = at
        if best is not None:
            break
    return best


def next_slot(now, game, cfg):
    v = cfg["values"]
    k = common.parse_iso(game["start"])
    first_day = (k - timedelta(days=v["f_days_before"])).date()
    for add in range(0, 9):
        d = (now + timedelta(days=add)).date()
        if d < first_day:
            continue
        for t in sorted(v["f_check_times"]):
            at = _at_clock(d, t)
            if at > now:
                return at
    return None


def n_player(keys):
    return sum(1 for m in keys or () if is_player_key(m))


def props_up(listed, cfg):
    return listed is not None and n_player(listed) >= cfg["values"]["f_min_player_markets"]


def sentinels(games):
    """{ET date: the earliest game that day} (the game whose markets check decides the day)."""
    out = {}
    for g in sorted(games, key=lambda x: (x["start"], x["id"])):
        out.setdefault(common.parse_iso(g["start"]).date().isoformat(), g)
    return out


def due_pulls(now, games, plan, cfg, done, focus, open_games, report_times, started, listed, m_done):
    """Which pull each game needs right now. One action per game per run at most.

    done: {gid: [dict(pull, at datetime, asked [keys], returned [keys])]} every read already made.
    focus: {gid: set(feed keys)} markets holding an open pick or a near-pick (C).
    open_games: {gid: set(feed keys)} markets holding an OPEN pick (D, and "c" in the basic tier).
    report_times: {gid: datetime} when the final injury report counts as in (R starts r_after_report_min later).
    listed: {gid: [keys]} Hard Rock's market list from M. m_done: {gid: datetime} last M per game.
    Returns [dict(pull, game, keys [feed keys, player and game], books [bookmakers])] in kickoff order.
    Every key is intersected with Hard Rock's list for the event (an unlisted key fails the whole call)."""
    v, ops = cfg["values"], cfg["ops"]
    pulls = plan["pulls"]
    retry = timedelta(minutes=ops.get("empty_retry_min", 180))
    never = set(ops.get("never_request", []))
    sent = sentinels(games)
    out = []
    for g in sorted(games, key=lambda x: (x["start"], x["id"])):
        if started.get(g["id"]):
            continue
        k = common.parse_iso(g["start"])
        if now >= k - timedelta(minutes=v["c_latest_min"]):
            continue
        reads = done.get(g["id"], [])
        lst = listed.get(g["id"])
        hr = set(lst or [])
        mt = m_done.get(g["id"])

        def allowed(keys):
            return [m for m in keys if m in hr and m not in never]

        def got_since(since):
            got = set()
            for f in reads:
                if f["at"] >= since:
                    got |= set(f["returned"])
            return got

        def read_since(kind, since):
            return any(f["pull"] == kind and f["at"] >= since for f in reads)

        f_ok = any(f["pull"] == "F" and n_player(f["returned"]) >= v["f_min_player_markets"] for f in reads)
        forced = now >= k - timedelta(hours=v["f_force_hours"])
        slot = check_slot(now, g, cfg)
        c_at = k - timedelta(minutes=v["c_min"])
        rt = report_times.get(g["id"])
        r_at = rt + timedelta(minutes=v["r_after_report_min"]) if rt else None
        choice = None

        def need_m(fresh_after):
            """M is due when we have no list, or the list shows no props and is older than fresh_after."""
            if mt is None:
                return True
            return not props_up(lst, cfg) and fresh_after is not None and mt < fresh_after

        # C: pre-kickoff check (markets holding open picks or near-picks + spreads/totals)
        want_c = "C" in pulls or ("c" in pulls and open_games.get(g["id"]))
        if want_c and now >= c_at:
            fc = sorted(focus.get(g["id"], ()) if "C" in pulls else open_games.get(g["id"], ()))
            pl = [m for m in allowed(fc) if m not in got_since(c_at)]
            if pl and not read_since("C", c_at):
                choice = ("C", pl + allowed(ops["pull_check_lines_keys"]), [ops["book"]])
        # R: after the final injury report
        if choice is None and "R" in pulls and r_at is not None and r_at <= now < c_at and not read_since("R", r_at):
            if lst is None:
                choice = ("M", [], [])
            else:
                pl = allowed(ops["pull_lite_keys"])
                if pl:
                    choice = ("R", pl, ops.get("pull_lite_bookmakers") or [ops["book"]])
        # D: 10 AM daily re-check of markets holding open picks (not on the R / C path)
        if choice is None and "D" in pulls and open_games.get(g["id"]) and now < c_at:
            d_at = _at_clock(now.date(), v.get("d_time", "10:00"))
            if d_at <= now < d_at + timedelta(hours=2) and not any(f["at"] >= d_at for f in reads):
                pl = allowed(sorted(open_games[g["id"]]))
                if pl:
                    choice = ("D", pl, [ops["book"]])
        # F: the full pull, once Hard Rock lists enough player markets (checked at f_check_times, sentinel first)
        if choice is None and "F" in pulls and not f_ok and (slot is not None or forced):
            s = sent.get(k.date().isoformat())
            day_up = s is None or s["id"] == g["id"] or started.get(s["id"]) or props_up(listed.get(s["id"]), cfg)
            if day_up or forced:
                fresh = slot if not forced else max(slot or now - retry, now - retry)
                if need_m(fresh):
                    choice = ("M", [], [])
                elif props_up(lst, cfg) or (forced and n_player(lst)):
                    last_f = max([f["at"] for f in reads if f["pull"] == "F"] or [None], key=lambda x: x or k - timedelta(days=99))
                    if last_f is None or (slot is not None and last_f < slot) or (forced and now - last_f >= retry):
                        pl = allowed(ops["pull_full_keys"])
                        if pl:
                            choice = ("F", pl, ops.get("pull_full_bookmakers") or [ops["book"]])
        # lite tier: no F; the markets list is still needed before R / C can ask anything
        if choice is None and "F" not in pulls and lst is None and (slot is not None or forced) and \
                (mt is None or now - mt >= retry):
            s = sent.get(k.date().isoformat())
            if s is None or s["id"] == g["id"] or forced:
                choice = ("M", [], [])
        if choice:
            books = list(choice[2])
            if not any(x in choice[1] for x in ops.get("display_only", {})):
                books = [ops["book"]]                       # other books are only ever read for display_only keys
            out.append({"pull": choice[0], "game": g, "keys": choice[1], "books": books or [ops["book"]]})
    return out


def est_cost(d):
    return 1 if d["pull"] == "M" else len(d["keys"])


def next_check_at(now, games, plan, cfg, done, report_times, started, listed, m_done, open_games=None):
    """When the next price read is expected (page: "next check"). None when none is coming."""
    if not plan:
        return None
    v = cfg["values"]
    pulls = plan["pulls"]
    cands = []
    for g in games:
        if started.get(g["id"]):
            continue
        k = common.parse_iso(g["start"])
        if k - timedelta(minutes=v["c_latest_min"]) <= now:
            continue
        reads = done.get(g["id"], [])
        f_ok = any(f["pull"] == "F" and n_player(f["returned"]) >= v["f_min_player_markets"] for f in reads)
        if "F" in pulls and not f_ok:
            ns = next_slot(now, g, cfg)
            if ns:
                cands.append(ns)
            cands.append(k - timedelta(hours=v["f_force_hours"]))
        rt = report_times.get(g["id"])
        if "R" in pulls and rt:
            r_at = rt + timedelta(minutes=v["r_after_report_min"])
            if not any(f["pull"] == "R" and f["at"] >= r_at for f in reads):
                cands.append(r_at)
        if "C" in pulls or ("c" in pulls and (open_games or {}).get(g["id"])):
            cands.append(k - timedelta(minutes=v["c_min"]))
    cands = [c for c in cands if c > now]
    return min(cands) if cands else None


# ---------------------------------------------------------------- matching events and names
def match_events(events, games, hours):
    """Feed events -> game ids by both team names and a start time within `hours`.
    Returns ({game_id: event_id}, [unknown team names])."""
    out, unknown = {}, []
    for e in events or []:
        a, h = teams.code_for_feed(e.get("away_team")), teams.code_for_feed(e.get("home_team"))
        for nm, ab in ((e.get("away_team"), a), (e.get("home_team"), h)):
            if ab is None and nm and nm not in unknown:
                unknown.append(nm)
        if not a or not h:
            continue
        try:
            when = common.parse_iso(e["commence_time"])
        except (KeyError, ValueError, TypeError):
            continue
        for g in games:
            if g["away"] == a and g["home"] == h and \
                    abs((common.parse_iso(g["start"]) - when).total_seconds()) <= hours * 3600:
                out[g["id"]] = e["id"]
    return out, unknown


def normalize(payload, game, matcher, asked, pull, at, cost, cfg):
    """One game's raw answer(s) -> the record kept in data/odds/<date>.json (every price, all markets).
    props[feed key] = {market, alt, last_update, outcomes [{name, pid, team, side, line, price}]}
    lines[feed key] = {book, last_update, outcomes [{side, point, price}]}; DraftKings rows land under
    "<key>@draftkings" and are display only."""
    ops = cfg["ops"]
    f2m = feed_to_market(cfg)
    out = {"game_id": game["id"], "away": game["away"], "home": game["home"], "pull": pull, "at": common.iso(at),
           "cost": cost, "asked": sorted(asked), "book": None, "returned": [], "props": {}, "lines": {},
           "unmatched": []}
    books = {b.get("key"): b for b in (payload or {}).get("bookmakers", []) or []}
    hr = books.get(ops["book"])
    out["book"] = ops["book"] if hr else None
    out["returned"] = sorted({m.get("key") for b in books.values() for m in b.get("markets", []) or [] if m.get("key")})
    for m in (hr or {}).get("markets", []) or []:
        key = m.get("key")
        if not key:
            continue
        if not is_player_key(key):
            out["lines"][key] = _line_market(m, "hardrock")
            continue
        name, alt = f2m.get(key, (None, False))
        rows = []
        for o in m.get("outcomes", []) or []:
            who = o.get("description") or ""
            side = (o.get("name") or "").lower()
            side = {"yes": "over", "no": "under"}.get(side, side)
            if side not in ("over", "under") or o.get("price") is None:
                continue
            pp = matcher.find(who) if matcher else None
            if pp is None and who not in out["unmatched"]:
                out["unmatched"].append(who)
            try:
                price = int(round(float(o["price"])))
                line = float(o["point"]) if o.get("point") is not None else 0.5
            except (TypeError, ValueError):
                continue
            rows.append({"name": who, "pid": pp["id"] if pp else None, "team": pp["team"] if pp else None,
                         "side": side, "line": line, "price": price})
        out["props"][key] = {"market": name, "alt": alt, "last_update": m.get("last_update"), "outcomes": rows}
    for bk, b in books.items():
        if bk == ops["book"]:
            continue
        for m in b.get("markets", []) or []:
            if ops.get("display_only", {}).get(m.get("key")) == bk:
                out["lines"]["%s@%s" % (m["key"], bk)] = _line_market(m, bk)
    return out


def _line_market(m, book):
    rows = []
    for o in m.get("outcomes", []) or []:
        nm = o.get("name") or ""
        code = teams.code_for_feed(nm)
        if code:
            side = code
        elif o.get("description"):
            side = "%s %s" % (teams.code_for_feed(o["description"]) or o["description"], nm.lower())
        else:
            side = nm.lower()
        try:
            price = int(round(float(o["price"])))
        except (TypeError, ValueError, KeyError):
            continue
        rows.append({"side": side, "point": o.get("point"), "price": price})
    return {"book": book, "last_update": m.get("last_update"), "outcomes": rows}


# ---------------------------------------------------------------- feed checks and the view
def check_pair(over, under, last_update, read_at, v):
    if over is None or under is None:
        return "one side missing"
    h = hold(over, under)
    if h < v["hold_min"] or h > v["hold_max"]:
        return "hold out of range"
    if is_stale(last_update, read_at, v):
        return "price is stale"
    return None


def is_stale(last_update, read_at, v):
    lu = common.parse_iso(last_update) if last_update else None
    if lu is None:
        return True
    return (read_at - lu).total_seconds() / 60.0 >= v.get("stale_min", 120)


def build_view(fetches, cfg):
    """Latest known prices per game. Returns {game_id: dict} with
      props   {market name: view}: at, pull, gone (the newest read that ASKED for it came back without it),
              posted, stale, pairs {(pid, line): dict(over, under, ok, why, name, team)}, checked, failed,
              alt {(pid, line): dict(over, under, name, team)} (alternates: Research only, never picks)
      lines   {feed key: dict(book, outcomes, at)} newest of each
      lines_at, props_at (newest read with player props), asked_props (ever asked), unmatched
    """
    v = cfg["values"]
    f2m = feed_to_market(cfg)
    out = {}
    for f in sorted(fetches or [], key=lambda x: x["at"]):
        at = common.parse_iso(f["at"])
        gv = out.setdefault(f["game_id"], {"props": {}, "lines": {}, "lines_at": None, "props_at": None,
                                           "asked_props": False, "unmatched": set()})
        gv["unmatched"] |= set(f.get("unmatched", []))
        for key, lm in (f.get("lines") or {}).items():
            gv["lines"][key] = dict(lm, at=f["at"])
            gv["lines_at"] = at
        asked = [k for k in f.get("asked", []) if is_player_key(k)]
        if asked:
            gv["asked_props"] = True
        if f.get("book") is None and asked:
            continue                                        # the book was absent: a feed problem, not news
        for key in asked:
            name, alt = f2m.get(key, (None, False))
            if name is None:
                continue
            mv = gv["props"].setdefault(name, {"at": None, "pull": None, "gone": False, "stale": False,
                                                "posted": False, "pairs": {}, "checked": 0, "failed": 0, "alt": {}})
            if key not in (f.get("props") or {}):
                if not alt and mv["posted"]:
                    mv.update(at=at, pull=f["pull"], gone=True, pairs={}, checked=0, failed=0)
                continue
            mk = f["props"][key]
            if alt:
                alts = {}
                for o in mk["outcomes"]:
                    if o["pid"] is None:
                        continue
                    x = alts.setdefault((o["pid"], o["line"]), {"over": None, "under": None, "name": o["name"],
                                                                "team": o["team"]})
                    x[o["side"]] = o["price"]
                mv["alt"], mv["alt_at"] = alts, at
                continue
            stale = is_stale(mk.get("last_update"), at, v)
            if stale and mv["posted"] and not mv["gone"]:
                mv["stale_read_at"] = at                    # keep the last good read
                continue
            pairs = {}
            for o in mk["outcomes"]:
                if o["pid"] is None:
                    continue
                pr = pairs.setdefault((o["pid"], o["line"]), {"over": None, "under": None, "name": o["name"],
                                                              "team": o["team"]})
                pr[o["side"]] = o["price"]
            failed = 0
            for pr in pairs.values():
                pr["why"] = check_pair(pr["over"], pr["under"], mk.get("last_update"), at, v)
                pr["ok"] = pr["why"] is None
                failed += 0 if pr["ok"] else 1
            mv.update(at=at, pull=f["pull"], gone=False, stale=stale, posted=True, pairs=pairs,
                      checked=len(pairs), failed=failed)
            gv["props_at"] = at
    return out


def game_context(gv, game):
    """Hard Rock main total and each team's spread (+ = favored) from the newest game lines; falls back to the
    schedule's consensus line when Hard Rock has none. -> dict(total, spread {team: float}, source)."""
    lines = (gv or {}).get("lines") or {}
    tot = None
    for o in (lines.get("totals") or {}).get("outcomes", []):
        if o["side"] == "over" and o.get("point") is not None:
            tot = float(o["point"])
    sp = {}
    for o in (lines.get("spreads") or {}).get("outcomes", []):
        if o["side"] in (game["away"], game["home"]) and o.get("point") is not None:
            sp[o["side"]] = -float(o["point"])
    src = "hardrock"
    if tot is None or len(sp) < 2:
        src = "schedule"
        if tot is None:
            tot = game.get("total_line")
        if len(sp) < 2 and game.get("spread_line") is not None:
            x = float(game["spread_line"])                  # nfldata: + = home favored
            sp = {game["home"]: x, game["away"]: -x}
    if tot is None and not sp:
        src = None
    return {"total": tot, "spread": sp, "source": src}


# ---------------------------------------------------------------- state and the pull itself
def state_path():
    return common.dpath("state", "odds_state.json")


def odds_path(day):
    return common.dpath("odds", "%s.json" % day)


def load_state(slate):
    d = common.read_json(state_path(), {}) or {}
    if d.get("slate") != slate:
        d = {"slate": slate, "events": {}, "events_at": None, "unknown_teams": [], "done": {}, "tried": {},
             "skipped": {}, "markets": {}, "markets_at": {}, "last_fail_at": None, "fail_note": None,
             "key_count": None, "plan": None}
    for k, dv in (("events", {}), ("done", {}), ("tried", {}), ("skipped", {}), ("markets", {}),
                  ("markets_at", {}), ("unknown_teams", [])):
        d.setdefault(k, dv)
    return d


def done_map(st):
    """Every price read this slate, plus the newest unpaid empty ask per game (for the retry clock)."""
    out = {}
    for gid, lst in (st.get("done") or {}).items():
        for f in lst:
            out.setdefault(gid, []).append({"pull": f["pull"], "at": common.parse_iso(f["at"]), "asked": f["asked"],
                                            "returned": f["returned"]})
    for gid, f in (st.get("tried") or {}).items():
        out.setdefault(gid, []).append({"pull": f["pull"], "at": common.parse_iso(f["at"]), "asked": f["asked"],
                                        "returned": []})
    return out


def m_map(st):
    return {gid: common.parse_iso(t) for gid, t in (st.get("markets_at") or {}).items()}


def load_fetches(games, now, days=9):
    """Every saved read for these games from the last `days` daily files."""
    ids = {g["id"] for g in games}
    out = []
    for i in range(days):
        d = (now - timedelta(days=i)).date().isoformat()
        doc = common.read_json(odds_path(d))
        for f in (doc or {}).get("fetches", []):
            if f["game_id"] in ids:
                out.append(f)
    return out


def run_pulls(now, slate, games, games_left, cfg, matcher_for, focus, held, report_times, started, dry_run=False):
    """The whole odds step. Returns a result dict for status.json and the engine."""
    ops = cfg["ops"]
    keys = load_keys()
    pool = Pool(keys, now)
    client = Client(pool, cfg)
    st = load_state(slate)
    res = {"status": "ok", "note": "", "keys": pool.public(), "pool_left": None, "plan": st.get("plan"),
           "fetched_now": 0, "errors": client.errors, "no_keys": pool.count() == 0, "skipped_games": []}
    open_games = [g for g in games if not started.get(g["id"])]
    if pool.count() == 0:
        res.update(status="skipped", note="no odds keys set")
        return _finish(st, pool, res, write=not dry_run)
    if not open_games:
        res.update(status="skipped", note="no games left to price this week")
        return _finish(st, pool, res, write=not dry_run)

    # 1. pool and events (free): first run of a slate, a key added, a key not reported, a game with no event
    need = st.get("events_at") is None or st.get("key_count") != pool.count()
    only_unknown = False
    if not need and pool.unknown():
        need, only_unknown = True, True
    missing_ev = any(g["id"] not in st["events"] for g in open_games)
    last_ev = common.parse_iso(st.get("events_at"))
    if missing_ev and (last_ev is None or now - last_ev >= timedelta(minutes=30) or now < last_ev):
        need = True
    if need and not dry_run:
        events = client.refresh_keys(only_unknown=only_unknown and not missing_ev)
        if events is not None:
            matched, unknown = match_events(events, games, ops["event_match_hours"])
            st["events"].update(matched)
            st["unknown_teams"] = unknown
            st["events_at"] = common.iso(now)
            st["key_count"] = pool.count()
        else:
            st["last_fail_at"], st["fail_note"] = common.iso(now), "The odds feed did not answer."

    # 2. the plan, from what the pool has now
    if not pool.known():
        refused = pool.all_refused()
        res.update(status="skipped" if refused or not client.errors else "failed",
                   note="every odds key was refused" if refused else "could not read the odds keys yet")
        return _finish(st, pool, res, write=not dry_run)
    plan = make_plan(pool.total_left(), games_left, cfg)
    plan["made_at"] = common.iso(now)
    st["plan"] = plan
    res["plan"] = plan

    # 3. what is due
    due = due_pulls(now, games, plan, cfg, done_map(st), focus, held, report_times, started, st["markets"],
                    m_map(st))
    res["due"] = [{"pull": d["pull"], "game_id": d["game"]["id"], "cost_max": est_cost(d)} for d in due]
    failed, no_budget = [], []
    day = now.date().isoformat()
    doc = None
    cap = plan["allowance"] * ops.get("game_cap_factor", 1.6)
    queue = list(due)
    acted = {}                                               # game id -> pulls made this run
    passes = 0
    while queue or (passes < 2 and any("M" in a for a in acted.values()) and not dry_run):
        if not queue:
            # markets lists came in: what they unlock (F or R for these games, M for the rest of a sentinel's day)
            # runs in this same cycle. A game that already made a price read this run is left alone.
            passes += 1
            again = due_pulls(now, games, plan, cfg, done_map(st), focus, held, report_times, started,
                              st["markets"], m_map(st))
            queue = [x for x in again if not (set(acted.get(x["game"]["id"], [])) - {"M"})
                     and not (x["pull"] == "M" and "M" in acted.get(x["game"]["id"], []))
                     and x["game"]["id"] not in failed and x["game"]["id"] not in no_budget]
            res["due"] += [{"pull": x["pull"], "game_id": x["game"]["id"], "cost_max": est_cost(x)} for x in queue]
            if not queue:
                break
            continue
        d = queue.pop(0)
        g = d["game"]
        if dry_run:
            continue
        cost_max = est_cost(d)
        over_cap = d["pull"] in ("F", "R", "D") and pool.spent_on_game(g["id"]) > 0 and \
            pool.spent_on_game(g["id"]) + cost_max > cap
        if pool.total_left() < cost_max or over_cap:
            no_budget.append(g["id"])
            st["skipped"][g["id"]] = {"pull": d["pull"], "why": "no budget", "at": common.iso(now)}
            continue
        ev = st["events"].get(g["id"])
        if not ev:
            failed.append(g["id"])
            client.errors.append("no odds event yet for %s at %s" % (g["away"], g["home"]))
            continue
        if d["pull"] == "M":
            mk = client.event_markets(ev, g["id"])
            if mk is None:
                failed.append(g["id"])
                continue
            st["markets"][g["id"]] = mk
            st["markets_at"][g["id"]] = common.iso(now)
            pool.save()
            common.write_json(state_path(), st)
            res["fetched_now"] += 1
            acted.setdefault(g["id"], []).append("M")
            continue
        payload, cost = client.event_odds(ev, g, d["keys"], d["books"], d["pull"])
        if payload is None:
            failed.append(g["id"])
            continue
        rec = normalize(payload, g, matcher_for(g), d["keys"], d["pull"], now, cost, cfg)
        if not rec["returned"] and not cost:
            st["tried"][g["id"]] = {"pull": d["pull"], "at": rec["at"], "asked": rec["asked"]}
            res.setdefault("not_posted", []).append(g["id"])
            common.write_json(state_path(), st)
            continue
        if doc is None:
            doc = common.read_json(odds_path(day)) or {"date": day, "fetches": []}
        doc["fetches"].append(rec)
        common.write_json(odds_path(day), doc, pretty=False)       # credits were spent: keep the answer right away
        st["done"].setdefault(g["id"], []).append({"pull": d["pull"], "at": rec["at"], "asked": rec["asked"],
                                                   "returned": rec["returned"], "cost": cost})
        st["skipped"].pop(g["id"], None)
        res["fetched_now"] += 1
        acted.setdefault(g["id"], []).append(d["pull"])
        pool.save()
        common.write_json(state_path(), st)
    if failed:
        st["last_fail_at"], st["fail_note"] = common.iso(now), "The odds feed did not answer."
        res["status"] = "failed" if not res["fetched_now"] else "ok"
        if pool.all_refused():
            res.update(status="skipped", note="every odds key was refused")
    elif res["fetched_now"]:
        st["last_fail_at"], st["fail_note"] = None, None
    if not res["note"]:
        bits = []
        kinds = sorted({d["pull"] for d in res["due"]})
        if res["fetched_now"]:
            bits.append("%d read(s) (%s)" % (res["fetched_now"], ", ".join(kinds)))
        if res.get("not_posted"):
            bits.append("%d game(s) not posted yet" % len(res["not_posted"]))
        if failed:
            bits.append("%d game(s) failed" % len(failed))
        if no_budget:
            bits.append("%d game(s) skipped, budget" % len(no_budget))
        res["note"] = ", ".join(bits) or "no pull due"
    res["failed_games"], res["no_budget"] = failed, no_budget
    res["next_at"] = next_check_at(now, games, plan, cfg, done_map(st), report_times, started, st["markets"], m_map(st),
                                   held)
    return _finish(st, pool, res, write=not dry_run)


def _finish(st, pool, res, write=True):
    res["keys"] = pool.public()
    res["pool_left"] = pool.total_left() if pool.known() else None
    res["all_used_up"] = pool.all_used_up()
    res["all_refused"] = pool.all_refused()
    res["state"] = st
    if write:
        pool.save()
        common.write_json(state_path(), st)
    return res


def pool_summary(key_state, plan, games_left, cfg, spent=(), now=None):
    """slate.status.pool: credits_left, keys, keys_live, days_left (at the recent pace), low, empty.
    Never a key value; keys are counted, not named."""
    reported = [k for k in key_state if k.get("left") is not None and k.get("status") != "invalid"]
    left = sum(k["left"] for k in reported) if reported else None
    live = sum(1 for k in reported if k["left"] > 0)
    empty = bool(key_state) and all(k.get("left") == 0 for k in key_state)
    days_left = None
    if left is not None and now is not None:
        by_day = {}
        for e in spent or ():
            if e["date"] < now.date().isoformat():
                by_day[e["date"]] = by_day.get(e["date"], 0) + e["cost"]
        recent = [c for d, c in sorted(by_day.items())[-7:] if c > 0]
        if recent:
            days_left = int(math.floor(left / (sum(recent) / float(len(recent))) + 1e-9))
        first = (now.date().replace(day=1) + timedelta(days=32)).replace(day=1)
        cap = (first - now.date()).days
        days_left = cap if days_left is None and left > 0 else (min(days_left, cap) if days_left is not None else 0)
    allowance = plan["allowance"] if plan else None
    low = (allowance is not None and allowance < cfg["values"]["pool_low_allowance"]) and not empty
    return {"credits_left": left, "keys": len(key_state), "keys_live": live, "days_left": days_left, "low": low,
            "empty": empty, "allowance": allowance, "tier": plan["tier"] if plan else None, "games_left": games_left}
