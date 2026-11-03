"""odds.py: Hard Rock Florida prices AND the other books' prices from the-odds-api (americanfootball_nfl).

What lives here
  keys      env ODDS_API_KEYS (then optional ODDS_API_KEYS_2), comma separated. Never printed, never written. A key that is used up or
            refused (401 / 429) is skipped and the next one is tried.
  ledger    data/state/credits.json: what each key has left (by POSITION, never the key text) and what we spent.
            The x-requests-remaining and x-requests-last headers are trusted on every answer (the keys are shared with Prop
            Check NHL).
  shared    Some keys are also spent by another tool (Prop Check NHL). After every answer the ledger keeps the key's
            remaining (after_us). If a later answer shows less than after_us minus what that call cost, someone else
            spent on it: the key is marked shared for the rest of the month (sticky; cleared when keys refill on the
            1st). Shared keys are tried LAST, and in the allowance their credits count at ops.shared_key_weight.
  plan      allowance = NFL credits (shared keys weighted, capped by ops.monthly_cap) / games not started before the keys reset
            on the 1st. The allowance picks a tier (ops.tiers): full = R + C on every game, tight = R on every game and C only for
            games holding a pick or near-pick, below = R only.
  pulls     R  right after the game's final injury report counts as in (Hard Rock + consensus books, the six picked markets).
            C  pre-kickoff: ops.pulls.c_target_min before kickoff once inactives are confirmed, else c_fallback_min before.
            Never a game that has kicked off. No M / F / D pulls, no game lines, no 1H, no alternate lines.
  calls     ONE call per pull per game: bookmakers = Hard Rock Florida + up to 9 consensus books (the feed bills up to 10 books as ONE
            region), markets = the six picked player markets. Cost = unique markets returned x regions billed, read from
            x-requests-last; a read billed more than that is noted in status.json (odds_notes).
            Every answer is saved to data/odds/<date>.json: "props" (Hard Rock) and "others" {book: {feed key: market}}.

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
MAX_BOOKS_PER_REGION = 10     # the feed bills each group of up to 10 bookmakers as one region


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
    """ODDS_API_KEYS, then ODDS_API_KEYS_2 (an optional second secret; same order as Prop Check NHL). Only the probe
    workflow passes the second one today; with it unset this is exactly the old behaviour."""
    keys = []
    for var in ("ODDS_API_KEYS", "ODDS_API_KEYS_2"):
        raw = os.environ.get(var, "")
        keys += [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]
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
            same = s.get("month") == month                     # shared is sticky for the month only
            self.state.append({"n": i + 1, "left": left, "status": status,
                               "checked_at": s.get("checked_at") if left is not None or status == "invalid" else None,
                               "month": month,
                               "after_us": s.get("after_us") if same else None,
                               "shared": bool(s.get("shared")) if same else False,
                               "shared_at": s.get("shared_at") if same else None,
                               "others_spent": int(s.get("others_spent") or 0) if same else 0})

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

    def weighted_left(self, weight):
        """Credits left for planning: a shared key's credits count at `weight` (another tool spends them too)."""
        return sum(s["left"] * (weight if s.get("shared") else 1.0)
                   for s in self.state if s["left"] is not None and s["status"] != "invalid")

    def shared_count(self):
        return sum(1 for s in self.state if s.get("shared"))

    def public(self):
        return [{"n": s["n"], "left": s["left"], "status": s["status"], "shared": bool(s.get("shared"))}
                for s in self.state]

    def spent_on_game(self, gid):
        return sum(e["cost"] for e in self.spent if e.get("game_id") == gid)

    def note_headers(self, i, headers, pull=None, game_id=None, returned=0):
        s = self.state[i]
        try:
            reported = int(float(headers.get("x-requests-remaining")))
        except (TypeError, ValueError):
            reported = None
        try:
            cost = int(float(headers.get("x-requests-last")))
        except (TypeError, ValueError):
            cost = returned
        if reported is not None:
            s["left"] = reported
            prev = s.get("after_us")
            if prev is not None and reported + cost < prev:     # less than we left it, beyond this call: shared
                s["others_spent"] = int(s.get("others_spent") or 0) + (prev - reported - cost)
                if not s.get("shared"):
                    s["shared"], s["shared_at"] = True, common.iso(self.now)
        elif s["left"] is not None and returned:
            s["left"] = max(0, s["left"] - returned)
        s["after_us"] = s["left"]
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
        """Keys that can pay for `need`: ours alone first, shared keys last (positional order within each)."""
        ok = [i for i, s in enumerate(self.state) if s["status"] == "ok" and (s["left"] is None or s["left"] >= need)]
        return sorted(ok, key=lambda i: (bool(self.state[i].get("shared")), i))


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

    def _get(self, path, params, need, pull=None, game_id=None, books=None):
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
                returned = cost_estimate(js, books) if (need and books) else (markets_returned(js) if need else 0)
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
            st["after_us"] = st["left"]
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

    def event_odds(self, event_id, game, market_keys, books, pull):
        """Prices for one game from `books` (Hard Rock first, see request_books). Returns (payload or None, cost).
        None = the feed failed (retry later)."""
        market_keys = list(market_keys)
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
            cost = cost_estimate(js, books)
            if not self._fixture_spend(cost, pull, game["id"]):
                self.errors.append("no odds key with credits left")
                return None, 0
            SOURCE["ok"] += 1
            return js, cost
        params = {"bookmakers": ",".join(books), "markets": ",".join(market_keys), "oddsFormat": "american",
                  "dateFormat": "iso"}
        js, cost = self._get("/events/%s/odds" % event_id, params, len(market_keys), pull, game["id"],
                             books=books)
        if js:
            n_mk = markets_returned(js)
            if n_mk and cost > n_mk * regions_billed(len(books)):
                self.errors.append("a read billed %d credits for %d market(s): more than one region?" % (cost, n_mk))
        return js, cost


def request_books(cfg, others=True):
    """The bookmakers= list for a price read: Hard Rock Florida first, then ops.consensus_books, never more than
    MAX_BOOKS_PER_REGION in all so the read is billed as one region. The national 'hardrockbet' key is the same operator
    as hardrockbet_fl and is never asked for."""
    ops = cfg["ops"]
    never = set(ops.get("never_book") or ())
    books = [ops["book"]]
    if others:
        for b in ops.get("consensus_books") or []:
            if b not in books and b not in never and len(books) < MAX_BOOKS_PER_REGION:
                books.append(b)
    return books


def regions_billed(n_books):
    """The feed bills each group of up to 10 bookmakers as one region (minimum 1)."""
    return max(1, -(-int(n_books) // MAX_BOOKS_PER_REGION))


def cost_estimate(js, books):
    """What a price read costs when the feed sends no x-requests-last header: markets returned x regions billed."""
    return markets_returned(js) * regions_billed(len(books or []) or 1)


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
# Which pulls each tier runs (ops.tiers). All three are timed off kickoff (2026-11-03, Matt: "a specific time prior to lock"):
# R = baseline, r_before_min (24 h) before kickoff, never before the final injury report counts as in; W = bet read,
# w_target_min (3 h) before kickoff; C = final, c_target_min (70 min) before kickoff once inactives are in. "c" = C only for
# games that hold a pick or a near-pick. "below" = under the tight threshold: R and W only.
TIER_PULLS = {"full": "RWC", "tight": "RWc", "below": "RW"}


def r_due_at(k, rt, pc):
    """When R is due for a game kicking off at k: r_before_min before kickoff, or when the final report counts as in if later."""
    base = k - timedelta(minutes=pc.get("r_before_min", 1440))
    return base if rt is None or rt <= base else rt


def make_plan(pool_left, games_left, cfg):
    t = cfg["ops"]["tiers"]
    allowance = (pool_left / float(games_left)) if games_left else float(pool_left or 0)
    name = "below"
    for tier in ("full", "tight"):
        if allowance >= t[tier]:
            name = tier
            break
    pulls = (t.get("pulls") or {}).get(name) or TIER_PULLS[name]
    return {"allowance": round(allowance, 2), "tier": name, "pulls": pulls, "pool_at_plan": pool_left,
            "games_left": games_left}


def _at_clock(day, hhmm):
    hh, mm = [int(x) for x in hhmm.split(":")]
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=common.ET)


def pick_keys(cfg):
    """The feed keys of the picked markets (ops.gap_guard.markets that are picks=true): what R and C ask for."""
    out = []
    for name in cfg["ops"]["gap_guard"]["markets"]:
        m = cfg["ops"]["markets"].get(name) or {}
        if m.get("picks") and m.get("feed_key"):
            out.append(m["feed_key"])
    return out


def c_target(k, confirmed, pc):
    """When C is due for a game kicking off at k: c_target_min before kickoff if inactives are confirmed (or as soon as
    they are, if later), else c_fallback_min before kickoff."""
    return k - timedelta(minutes=pc["c_target_min"]) if confirmed else k - timedelta(minutes=pc["c_fallback_min"])


def due_pulls(now, games, plan, cfg, done, focus, started, report_times, report_ok, confirmed, watch=None):
    """Which pull each game needs right now. One action per game per run at most.

    done: {gid: [dict(pull, at datetime, asked [keys], returned [keys], hr [Hard Rock keys], ina bool)]} every read so far.
    focus: {gid: set(feed keys)} markets holding an open pick or a near-pick (the tight tier's C).
    report_times: {gid: datetime} when the final injury report counts as in (R is due from then).
    report_ok: set of game ids whose injury read is fresh enough to say the report is in.
    confirmed: set of game ids with both teams' inactives posted.
    watch: unused (kept for callers). W is a standard read now, part of the plan's pulls.
    Returns [dict(pull, game, keys [feed keys], books [bookmakers])] in kickoff order."""
    ops = cfg["ops"]
    pc = ops["pulls"]
    pulls = plan["pulls"]
    keys = pick_keys(cfg)
    books = request_books(cfg)
    out = []
    for g in sorted(games, key=lambda x: (x["start"], x["id"])):
        if started.get(g["id"]):
            continue
        k = common.parse_iso(g["start"])
        if now >= k - timedelta(minutes=pc["c_latest_min"]):
            continue
        reads = done.get(g["id"], [])
        conf = g["id"] in confirmed
        choice = None
        # C: pre-kickoff. A second C is allowed when the first was made before inactives were in and they are in now.
        want_c = "C" in pulls or ("c" in pulls and focus.get(g["id"]))
        if want_c and now >= c_target(k, conf, pc):
            cs = sorted((f for f in reads if f["pull"] == "C"), key=lambda f: f["at"])
            again = bool(cs) and conf and not cs[-1].get("ina") and now - cs[-1]["at"] >= timedelta(minutes=5)
            if not cs or again:
                ks = keys if "C" in pulls else [m for m in keys if m in focus.get(g["id"], ())]
                if ks:
                    choice = ("C", ks)
        # W: the bet read, w_target_min before kickoff (never later than w_latest_min before). Needs the report in.
        rt = report_times.get(g["id"])
        w_from = k - timedelta(minutes=pc.get("w_target_min", 180))
        w_to = k - timedelta(minutes=pc.get("w_latest_min", 100))
        if choice is None and "W" in pulls and rt is not None and rt <= now and g["id"] in report_ok \
                and w_from <= now < w_to:
            ws = sorted((f for f in reads if f["pull"] == "W"), key=lambda f: f["at"])
            # one W; a second only when the first came back without Hard Rock and 20 minutes have passed
            if not ws or (len(ws) == 1 and not ws[0].get("hr") and now - ws[0]["at"] >= timedelta(minutes=20)):
                choice = ("W", keys)
        # R: the baseline, r_before_min before kickoff and after the final injury report counts as in; not once W is due
        if choice is None and "R" in pulls and rt is not None and r_due_at(k, rt, pc) <= now \
                and ("W" not in pulls or now < w_from) and g["id"] in report_ok:
            rs = sorted((f for f in reads if f["pull"] == "R" and f["at"] >= rt), key=lambda f: f["at"])
            hr_up = any(f.get("hr") for f in rs)
            retry = timedelta(minutes=pc["r_retry_min"])
            if not rs or (not hr_up and len(rs) < pc["r_max_reads"] and now - rs[-1]["at"] >= retry):
                choice = ("R", keys)
        if choice:
            out.append({"pull": choice[0], "game": g, "keys": choice[1], "books": books})
    return out


def est_cost(d):
    return len(d["keys"]) * regions_billed(len(d["books"]))


def next_check_at(now, games, plan, cfg, done, report_times, started, confirmed=()):
    """When the next price read is expected (page: "next check"). None when none is coming."""
    if not plan:
        return None
    pc = cfg["ops"]["pulls"]
    pulls = plan["pulls"]
    cands = []
    for g in games:
        if started.get(g["id"]):
            continue
        k = common.parse_iso(g["start"])
        if k - timedelta(minutes=pc["c_latest_min"]) <= now:
            continue
        reads = done.get(g["id"], [])
        rt = report_times.get(g["id"])
        if "R" in pulls and rt is not None and not any(f["pull"] == "R" and f["at"] >= rt for f in reads):
            cands.append(r_due_at(k, rt, pc))
        if "W" in pulls and not any(f["pull"] == "W" for f in reads):
            cands.append(k - timedelta(minutes=pc.get("w_target_min", 180)))
        if "C" in pulls or "c" in pulls:
            cands.append(c_target(k, g["id"] in confirmed, pc))
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


def _rows(m, find, unmatched=None, keep_unmatched=True):
    """One market's feed outcomes -> rows {name, pid, team, side, line, price}. find(name) -> player or None.
    unmatched: list that collects names no roster player matched (None = do not collect)."""
    rows = []
    for o in m.get("outcomes", []) or []:
        who = o.get("description") or ""
        side = (o.get("name") or "").lower()
        side = {"yes": "over", "no": "under"}.get(side, side)
        if side not in ("over", "under") or o.get("price") is None:
            continue
        pp = find(who)
        if pp is None and unmatched is not None and who not in unmatched:
            unmatched.append(who)
        if pp is None and not keep_unmatched:
            continue
        try:
            price = int(round(float(o["price"])))
            line = float(o["point"]) if o.get("point") is not None else 0.5
        except (TypeError, ValueError):
            continue
        rows.append({"name": who, "pid": pp["id"] if pp else None, "team": pp["team"] if pp else None,
                     "side": side, "line": line, "price": price})
    return rows


def normalize(payload, game, matcher, asked, pull, at, cost, cfg):
    """One game's raw answer -> the record kept in data/odds/<date>.json (every price, all markets).
    props[feed key] = {market, alt, last_update, outcomes [{name, pid, team, side, line, price}]}  (Hard Rock Florida only)
    others[book][feed key] = {last_update, outcomes [{pid, side, line, price}]}  every other book that answered, matched
        players only. The national 'hardrockbet' key (same operator as hardrockbet_fl) is never kept as an 'other'.
    lines[feed key] = {book, last_update, outcomes [{side, point, price}]} (game lines, none asked today; old files only)."""
    ops = cfg["ops"]
    f2m = feed_to_market(cfg)
    never = set(ops.get("never_book") or ())
    out = {"game_id": game["id"], "away": game["away"], "home": game["home"], "pull": pull, "at": common.iso(at),
           "cost": cost, "asked": sorted(asked), "book": None, "returned": [], "props": {}, "lines": {},
           "others": {}, "unmatched": []}
    books = {b.get("key"): b for b in (payload or {}).get("bookmakers", []) or []}
    hr = books.get(ops["book"])
    out["book"] = ops["book"] if hr else None
    out["returned"] = sorted({m.get("key") for b in books.values() for m in b.get("markets", []) or [] if m.get("key")})
    seen = {}

    def find(who):
        if who not in seen:
            seen[who] = matcher.find(who) if matcher else None
        return seen[who]

    for m in (hr or {}).get("markets", []) or []:
        key = m.get("key")
        if not key:
            continue
        if not is_player_key(key):
            out["lines"][key] = _line_market(m, "hardrock")
            continue
        name, alt = f2m.get(key, (None, False))
        out["props"][key] = {"market": name, "alt": alt, "last_update": m.get("last_update"),
                             "outcomes": _rows(m, find, out["unmatched"])}
    for bk, b in books.items():
        if bk == ops["book"] or bk in never:
            continue
        mk = {}
        for m in b.get("markets", []) or []:
            key = m.get("key")
            if not key or not is_player_key(key) or f2m.get(key, (None, True))[1]:
                continue                                        # main lines only
            rows = [{"pid": r["pid"], "side": r["side"], "line": r["line"], "price": r["price"]}
                    for r in _rows(m, find, None, keep_unmatched=False)]
            mk[key] = {"last_update": m.get("last_update"), "outcomes": rows}
        if mk:
            out["others"][bk] = mk
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


def is_stale(last_update, read_at, v, key="stale_min"):
    """key 'stale_min' = Hard Rock; 'stale_min_others' = the other books (untuned: the book probe did not store last_update)."""
    lu = common.parse_iso(last_update) if last_update else None
    if lu is None:
        return True
    return (read_at - lu).total_seconds() / 60.0 >= v.get(key, 120)


def pairs_of(outcomes):
    """Outcome rows -> {(pid, line): dict(over, under, name, team)} for matched players."""
    pairs = {}
    for o in outcomes or []:
        if o.get("pid") is None:
            continue
        pr = pairs.setdefault((o["pid"], o["line"]), {"over": None, "under": None, "name": o.get("name"),
                                                      "team": o.get("team")})
        pr[o["side"]] = o["price"]
    return pairs


def checked_pairs(mk, at, v):
    """One Hard Rock market of one read -> pairs with the feed checks applied (why = None when usable)."""
    pairs = pairs_of(mk.get("outcomes"))
    for pr in pairs.values():
        pr["why"] = check_pair(pr["over"], pr["under"], mk.get("last_update"), at, v)
        pr["ok"] = pr["why"] is None
    return pairs


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
            pairs = checked_pairs(mk, at, v)
            failed = sum(0 if pr["ok"] else 1 for pr in pairs.values())
            mv.update(at=at, pull=f["pull"], gone=False, stale=stale, posted=True, pairs=pairs,
                      checked=len(pairs), failed=failed)
            gv["props_at"] = at
    return out


def _be(price):
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    return 100.0 / (price + 100.0) if price > 0 else -price / (-price + 100.0)


def main_total(outcomes, over="over", under="under"):
    """The main over/under pair: same number on both sides, priced closest to even. -> (over, under) or None.
    A feed can carry alternate numbers under the same key (Hard Rock 1H total 30.5 at +425/-800)."""
    best = None
    for o in outcomes or []:
        if o.get("side") != over or o.get("point") is None:
            continue
        for u in outcomes:
            if u.get("side") == under and u.get("point") == o["point"]:
                bo, bu = _be(o.get("price")), _be(u.get("price"))
                if bo is None or bu is None or not (0.41 <= bo <= 0.62 and 0.41 <= bu <= 0.62):
                    continue                                   # not a main line (e.g. +425 / -800)
                d = abs(bo - bu)
                if best is None or d < best[0]:
                    best = (d, o, u)
    return (best[1], best[2]) if best else None


def main_spread(outcomes, away, home):
    """The main spread pair: away and home on mirrored numbers, priced closest to even. -> (away, home) or None."""
    best = None
    for a in outcomes or []:
        if a.get("side") != away or a.get("point") is None:
            continue
        for h in outcomes:
            if h.get("side") == home and h.get("point") is not None and abs(h["point"] + a["point"]) < 1e-9:
                ba, bh = _be(a.get("price")), _be(h.get("price"))
                if ba is None or bh is None or not (0.41 <= ba <= 0.62 and 0.41 <= bh <= 0.62):
                    continue                                   # not a main line (e.g. +425 / -800)
                d = abs(ba - bh)
                if best is None or d < best[0]:
                    best = (d, a, h)
    return (best[1], best[2]) if best else None


def game_context(gv, game):
    """Hard Rock main total and each team's spread (+ = favored) from the newest game lines; falls back to the
    schedule's consensus line when Hard Rock has none. -> dict(total, spread {team: float}, source)."""
    lines = (gv or {}).get("lines") or {}
    tot = None
    mt = main_total((lines.get("totals") or {}).get("outcomes"))
    if mt:
        tot = float(mt[0]["point"])
    sp = {}
    ms = main_spread((lines.get("spreads") or {}).get("outcomes"), game["away"], game["home"])
    if ms:
        for o in ms:
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
             "skipped": {}, "last_fail_at": None, "fail_note": None, "key_count": None, "plan": None, "failed": {}}
    for k, dv in (("events", {}), ("done", {}), ("tried", {}), ("skipped", {}), ("unknown_teams", []), ("failed", {})):
        d.setdefault(k, dv)
    return d


def done_map(st):
    """Every price read this slate, plus the newest unpaid empty ask per game (for the retry clock)."""
    out = {}
    for gid, lst in (st.get("done") or {}).items():
        for f in lst:
            out.setdefault(gid, []).append({"pull": f["pull"], "at": common.parse_iso(f["at"]), "asked": f["asked"],
                                            "returned": f["returned"], "hr": f.get("hr") or [], "ina": bool(f.get("ina"))})
    for gid, f in (st.get("tried") or {}).items():
        out.setdefault(gid, []).append({"pull": f["pull"], "at": common.parse_iso(f["at"]), "asked": f["asked"],
                                        "returned": [], "hr": [], "ina": False})
    return out


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


def run_pulls(now, slate, games, games_left, cfg, matcher_for, focus, report_times, report_ok, confirmed, started,
              dry_run=False, reserve_games=None):
    """The whole odds step. Returns a result dict for status.json and the engine.
    focus {gid: set(feed keys)}: markets holding an open pick or near-pick (tight tier's C). report_times {gid: when the final
    injury report counts as in}. report_ok: game ids whose injury read is fresh after that time. confirmed: game ids with
    both teams' inactives posted. reserve_games: every game that has not started and kicks off in the plan window or before the
    keys reset (dicts with id, start). Unused since W became a standard read (2026-11-03)."""
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
    last_ev = common.parse_iso(st.get("events_at"))
    if last_ev is not None and (now < last_ev or now - last_ev >= timedelta(hours=ops.get("keys_refresh_hours", 6))):
        need = True                                          # free: re-read every key (who else spends them)
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
    plan = _set_plan(st, pool, games_left, cfg, now)
    res["plan"] = plan

    # 3. what is due (a game whose read just failed rests fail_retry_min before it is asked again)
    rest = timedelta(minutes=ops.get("fail_retry_min", 15))

    def resting(gid):
        t = common.parse_iso((st["failed"].get(gid) or {}).get("at"))
        return t is not None and t <= now < t + rest

    due = [d for d in due_pulls(now, games, plan, cfg, done_map(st), focus, started, report_times, report_ok, confirmed)
           if not resting(d["game"]["id"])]
    res["due"] = [{"pull": d["pull"], "game_id": d["game"]["id"], "cost_max": est_cost(d)} for d in due]
    failed, no_budget = [], []
    day = now.date().isoformat()
    doc = None
    cap = plan["allowance"] * ops.get("game_cap_factor", 1.6)
    for d in due:
        g = d["game"]
        if dry_run:
            continue
        cost_max = est_cost(d)
        is_w = d["pull"] == "W"
        over_cap = d["pull"] == "R" and pool.spent_on_game(g["id"]) > 0 and \
            pool.spent_on_game(g["id"]) + cost_max > cap
        if pool.total_left() < cost_max or over_cap:
            if not is_w:                                    # a watch read that cannot be paid is simply not made
                no_budget.append(g["id"])
                st["skipped"][g["id"]] = {"pull": d["pull"], "why": "no budget", "at": common.iso(now)}
            continue
        ev = st["events"].get(g["id"])
        if not ev:
            if is_w:
                continue
            failed.append(g["id"])
            st["failed"][g["id"]] = {"pull": d["pull"], "at": common.iso(now)}
            client.errors.append("no odds event yet for %s at %s" % (g["away"], g["home"]))
            continue
        payload, cost = client.event_odds(ev, g, d["keys"], d["books"], d["pull"])
        if payload is None:
            if not is_w:                                    # a failed watch read never marks the odds step or the page
                failed.append(g["id"])
            st["failed"][g["id"]] = {"pull": d["pull"], "at": common.iso(now)}
            continue
        st["failed"].pop(g["id"], None)
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
        hr_keys = sorted(k for k, mk in rec["props"].items() if not mk.get("alt") and mk.get("outcomes"))
        st["done"].setdefault(g["id"], []).append({"pull": d["pull"], "at": rec["at"], "asked": rec["asked"],
                                                   "returned": rec["returned"], "cost": cost, "hr": hr_keys,
                                                   "ina": g["id"] in confirmed, "n_others": len(rec["others"])})
        st["skipped"].pop(g["id"], None)
        res["fetched_now"] += 1
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
    if res["fetched_now"]:
        plan = _set_plan(st, pool, games_left, cfg, now)    # the plan the next run will find: no churn from it
        res["plan"] = plan
    res["next_at"] = next_check_at(now, games, plan, cfg, done_map(st), report_times, started, confirmed)
    return _finish(st, pool, res, write=not dry_run)


def month_spent(pool, cfg, now):
    """Credits the NFL plan has spent this calendar month, counted from ops.monthly_cap_from when that is later."""
    first = now.date().replace(day=1).isoformat()
    since = max(first, cfg["ops"].get("monthly_cap_from") or first)
    return sum(e["cost"] for e in pool.spent if e["date"] >= since and e["date"][:7] == first[:7])


def _set_plan(st, pool, games_left, cfg, now):
    """The plan from the pool as it stands (shared keys weighted, and no more than ops.monthly_cap minus this month's
    spend: the keys are shared with Prop Check NHL). made_at moves only when the plan changed."""
    ops = cfg["ops"]
    w = ops.get("shared_key_weight", 0.5)
    avail = pool.weighted_left(w)
    cap = ops.get("monthly_cap")
    if cap is not None:
        avail = min(avail, max(0.0, cap - month_spent(pool, cfg, now)))
    plan = make_plan(round(avail, 2), games_left, cfg)
    plan["pool_raw"] = pool.total_left()
    plan["keys_shared"] = pool.shared_count()
    old = st.get("plan") or {}
    same = {k: v for k, v in old.items() if k != "made_at"} == plan
    plan["made_at"] = old.get("made_at") if same and old.get("made_at") else common.iso(now)
    st["plan"] = plan
    return plan


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
    shared = sum(1 for k in key_state if k.get("shared"))
    empty = bool(key_state) and all(k.get("left") == 0 for k in key_state)
    days_left = None
    if left is not None and now is not None:
        by_day = {}
        for e in spent or ():
            if e["date"] < now.date().isoformat():
                by_day[e["date"]] = by_day.get(e["date"], 0) + e["cost"]
        recent = [c for d, c in sorted(by_day.items())[-7:] if c > 0]
        window = cfg["ops"].get("plan_window_days", 7)       # the plan looks one week ahead, so "days left" stops there
        if recent:
            pace = sum(recent) / float(len(recent))
        else:                                                # no history yet: the R-only cost of the week's games, per day
            pace = games_left * len(pick_keys(cfg)) * regions_billed(len(request_books(cfg))) / float(window)
        days_left = window if pace <= 0 else min(window, int(math.floor(left / pace + 1e-9)))
    allowance = plan["allowance"] if plan else None
    low = (allowance is not None and allowance < cfg["ops"]["tiers"]["pool_low_allowance"]) and not empty
    return {"credits_left": left, "keys": len(key_state), "keys_live": live, "keys_shared": shared,
            "days_left": days_left, "low": low, "empty": empty, "allowance": allowance, "tier": plan["tier"] if plan else None, "games_left": games_left}
