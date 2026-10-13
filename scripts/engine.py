"""engine.py: stats + Hard Rock prices + injury news -> this week's picks, kept honest through the week.

The method is the Sharp standard (config/rules.json "values"; every number is read from there). For one side of
one Hard Rock main line:
    b          break-even of the Hard Rock price
    fair       Hard Rock's own chance with the vig removed (needs both sides; a one-sided price is never a pick)
    model      the DS model's chance for this side (model.prob_over, calibrated) from the player's rows, the
               opponent, and the game's Hard Rock total and spread
    model_adj  model - shrink[market|period|side] (- jump_penalty on an over after a role jump)
    gap        model_adj - fair            (values.gap_uses = "model_adj")
    p          model_weight x model_adj + market_weight x fair                 <- "our chance" on the card
A NEW pick needs, in this order (judge() is the one place this lives):
    market picks=true and side allowed (ops.markets[*].sides), main line (alt_lines_picks false), both sides posted,
    feed checks (hold, staleness), game not started, team stats complete, player status not in status_no_pick,
    missed his team's last game -> waits for inactives (missed_last_waits), DS eligibility flags (model.pick_ok on
    values.elig), wind (weather_*), price inside -200..max_price, model_adj >= min_model, gap <= too_good,
    gap <= news_gap unless the injury report is in, p >= min_p, p - b >= margin for the pick's settledness
    (margin_early / margin_injury / margin_inactives, + board_tighten when the whole board is running cold).
Then caps: 1 per player, max_per_team, max_per_game; ranked by chance, then margin, then settledness.

Settledness per player's team: early -> injury_report -> inactives (only moves forward).
A shown pick is never silently removed: each run it keeps its price (a new one is appended to the Record), is
pulled with a plain reason, or locks at kickoff. slate.json is rebuilt from the Record each run.
"""
import hashlib
import itertools
from datetime import timedelta

import common
import injuries
import model
import names
import odds
import record
import sched

EPS = 1e-9
SETTLED_RANK = {"early": 0, "injury_report": 1, "inactives": 2}
SETTLED_TEXT = {"early": "Early", "injury_report": "Injury report in", "inactives": "Inactives confirmed"}


class ConfigError(Exception):
    pass


def check_config(cfg):
    v = cfg["values"]
    if abs(v["model_weight"] + v["market_weight"] - 1.0) > 1e-9:
        raise ConfigError("model_weight + market_weight must equal 1")
    for k in ("min_p", "min_model", "margin_early", "margin_injury", "margin_inactives", "news_gap", "too_good",
              "drift_pull", "parlay_leg_min_p", "parlay_min_p_2", "parlay_min_p_3", "parlay_edge", "hold_min",
              "hold_max"):
        if not (0 <= v[k] <= 1):
            raise ConfigError("%s must be between 0 and 1" % k)
    if v["price_gate"] < -200 or v["parlay_leg_min_price"] < -200:
        raise ConfigError("no leg can be priced worse than -200 (customer rule)")
    if v["price_gate"] >= 0 or v["max_price"] < 100:
        raise ConfigError("price_gate must be negative and max_price +100 or longer")
    if v["parlay_max_legs"] > 3 or v["parlay_min_legs"] < 2:
        raise ConfigError("parlays are 2-3 legs (customer rule)")


# ---------------------------------------------------------------- market names
def mdef(cfg, mkey):
    return cfg["ops"]["markets"][mkey]


def contract_market(cfg, mkey):
    """ops.markets key ('rec_yds_1h') -> (contract market 'rec_yds', period '1H')."""
    m = mdef(cfg, mkey)
    return m.get("model") or mkey.replace("_1h", ""), m.get("period", "FG")


def market_label(cfg, market, period):
    for k, m in cfg["ops"]["markets"].items():
        if (m.get("model") or k) == market and m.get("period") == period:
            base = m["label"]
            if period == "1H":
                base = base.replace("1H ", "")
                return "%s (1st half)" % (base[0].upper() + base[1:])
            return base
    return "%s %s" % (market, period)


# ---------------------------------------------------------------- the edge rule (pure arithmetic)
def shrink_for(market, period, side, v):
    return v["shrink"].get("%s|%s|%s" % (market, period, "O" if side == "over" else "U"), 0.05)


def edge(model_side, side, market, period, price, other_price, v, fair=None, jump=False):
    b = odds.break_even(price)
    if other_price is not None:
        fair = odds.fair_two_sided(price, other_price)
    adj = model_side - shrink_for(market, period, side, v)
    if side == "over" and jump:
        adj -= v["jump_penalty"]
    if fair is None:
        fair = b
    p = v["model_weight"] * adj + v["market_weight"] * fair
    gap = (adj if v.get("gap_uses", "model_adj") == "model_adj" else model_side) - fair
    return {"b": b, "fair": fair, "model": model_side, "model_adj": adj, "gap": gap, "p": p, "margin": p - b}


def inside_gate(price, v):
    d = odds.decimal(price)
    return odds.decimal(v["price_gate"]) - EPS <= d <= odds.decimal(v["max_price"]) + EPS


def margin_need(settled, v, tighten=0.0):
    return {"early": v["margin_early"], "injury_report": v["margin_injury"],
            "inactives": v["margin_inactives"]}[settled] + tighten


def qualifies(e, price, settled, v, tighten=0.0):
    """Price and chance test for a NEW pick. Returns (True, None) or (False, why)."""
    if not inside_gate(price, v):
        return False, "price outside -200..+%d" % v["max_price"]
    if e["model_adj"] < v["min_model"] - EPS:
        return False, "model chance under the minimum"
    if e["gap"] > v["too_good"] + EPS:
        return False, "too good: assume the book knows"
    if e["gap"] > v["news_gap"] + EPS and settled == "early":
        return False, "big gap waits for the injury report"
    if e["p"] < v["min_p"] - EPS:
        return False, "chance under the minimum"
    if e["margin"] < margin_need(settled, v, tighten) - EPS:
        return False, "margin too small"
    return True, None


def rank_key(p, b, settled):
    return (-round(p, 6), -(round(p - b, 6)), -SETTLED_RANK.get(settled, 0))


# ---------------------------------------------------------------- wording (plain, no dashes)
def fmt_line(line):
    return "%g" % line


def bet_text(side, line, mkey, cfg):
    m = mdef(cfg, mkey)
    market, _ = contract_market(cfg, mkey)
    if market == "anytime_td":
        return "Scores a TD" if side in ("over", "yes") else "No TD"
    return "%s %s %s" % ("Over" if side == "over" else "Under", fmt_line(line), m.get("bet_noun") or m["noun"])


def pct_floor(x):
    import math
    return int(math.floor(x * 100 + 1e-7))


def pct_round(x):
    import math
    return int(math.floor(x * 100 + 0.5))


def pcts(e):
    return pct_floor(e["p"]), pct_round(e["b"])


def reason_text(why_parts, limit=120):
    """'7.4 catches a game his last 5 (3 of 5 over 6.5). DEN allows the 4th fewest catches to WRs.'"""
    parts = [w.strip() for w in (why_parts or []) if w and w.strip()]
    if not parts:
        return None
    cap = lambda s: s[0].upper() + s[1:]
    out = cap(parts[0]) + "."
    if len(parts) > 1 and len(out) + 1 + len(parts[1]) + 1 <= limit:
        out += " " + cap(parts[1]) + "."
    return out.replace("—", ",").replace("–", "-")


def pick_id(slate, game_id, pid, market, period, line, side):
    return "%s:%s:%s:%s:%s:%s:%s" % (slate, game_id, pid, market, period, fmt_line(line), side)


def parlay_id(slate, leg_ids):
    h = hashlib.sha1("|".join(sorted(leg_ids)).encode()).hexdigest()[:10]
    return "%s:P:%s" % (slate, h)


# ---------------------------------------------------------------- what the engine reads
class Inputs:
    """Everything the engine reads. Built by run.py; tests build it by hand."""

    def __init__(self, cfg, now, slate, season, games, store, behind, view, inj, ina, people, events,
                 cache=None, weather=None, facts=None):
        self.cfg, self.v, self.now, self.slate, self.season = cfg, cfg["values"], now, slate, season
        self.games = {g["id"]: g for g in games}
        self.store, self.behind, self.view = store, behind or {}, view or {}
        self.inj, self.ina = inj or {}, ina or {}
        self.people = people or {}                          # gsis -> dict(first, last, name, team, pos)
        self.events, self.cache = events, cache
        self.weather = (weather or {}).get("games", {}) if isinstance(weather, dict) else {}
        self.facts = facts or {}
        self.tighten = 0.0
        self._c = {}

    def pos(self, pid):
        p = self.people.get(pid) or {}
        pos = p.get("pos") or (self.store.players.get(pid) or [None, None])[1]
        return "RB" if pos in ("FB", "HB") else pos

    def started(self, g):
        return sched.started(g, self.now, self.cache)

    def settled(self, g, team):
        return injuries.settled(g, team, self.cfg, self.inj, self.ina, self.now)

    def status(self, g, team, pid, name=None):
        return injuries.player_status(self.inj, self.ina, g, team, pid, name)

    def context(self, gid, period):
        k = ("ctx", gid, period)
        if k not in self._c:
            g = self.games[gid]
            gc = odds.game_context(self.view.get(gid), g)
            self._c[k] = (gc, self.store.opp_table(g["date"], self.season, period))
        return self._c[k]

    def history(self, pid, team, g):
        k = ("h", pid, team, g["id"])
        if k in self._c:
            return self._c[k]
        rows = self.store.player_rows(pid, before=g["date"])
        with_team = 0
        for r in reversed(rows):
            if r["team"] != team:
                break
            with_team += 1
        tg = [t for t in self.store.team_games.get(team, []) if t["date"] < g["date"] and t["season"] == self.season]
        played = {r["gid"] for r in rows}
        first_with = next((r["date"] for r in rows if r["team"] == team and r["season"] == self.season), None)
        missed = 0
        for t in reversed(tg):
            if t["gid"] in played or first_with is None or t["date"] < first_with:
                break
            missed += 1
        out = {"rows": rows, "with_team": with_team, "missed": missed}
        self._c[k] = out
        return out

    def model(self, pid, team, gid, mkey, line):
        """dict(p_over, flags, why, mu, n, n_season, role_ok, jump, thin, missed, ctx_source) or None."""
        k = ("m", pid, team, gid, mkey, line)
        if k in self._c:
            return self._c[k]
        g = self.games[gid]
        m = mdef(self.cfg, mkey)
        market, period = m.get("model"), m.get("period", "FG")
        pos = self.pos(pid)
        if not market or pos not in ("QB", "RB", "WR", "TE") or market not in model.SPEC:
            self._c[k] = None
            return None
        h = self.history(pid, team, g)
        gc, opp_tab = self.context(gid, period)
        opp = g["away"] if team == g["home"] else g["home"]
        sp = (gc["spread"] or {}).get(team)
        ctx = {"pos": pos, "season": self.season, "date": g["date"], "spread": sp,
               "team_total": model.implied_total(gc["total"], sp) if gc["total"] is not None else None,
               "opp": opp_tab.get(opp) or {}, "opp_name": opp, "games_with_team": h["with_team"],
               "missed_last": h["missed"], "qb_changed": self.team_qb_out(g, team)}
        res = model.prob_over(h["rows"], market, period, line, ctx)
        rs, _ = model._prior_rows(h["rows"], ctx)
        jump = model.role_jump(rs, market, ctx)
        flags = model.eligibility(res["n"], res["role_cv"], ctx, {"elig": self.v["elig"]}, jump)
        out = {"p_over": res["p"], "mu": res["mu"], "n": res["n"], "n_season": res["n_season"], "flags": flags,
               "why": res["why_parts"], "jump": "role_jump" in flags, "thin": res["thin"], "missed": h["missed"],
               "ctx_source": gc["source"]}
        self._c[k] = out
        return out

    def flagged(self, pid, team, name=None):
        return injuries.ever_flagged(self.inj, pid, team, name)

    def last_starter(self, g, team):
        k = ("qb1", g["id"], team)
        if k not in self._c:
            tg = [t for t in self.store.team_games.get(team, []) if t["date"] < g["date"]]
            pid = None
            if tg:
                last = self.store.by_game.get(tg[-1]["gid"], {})
                qbs = sorted((r for r in last.values() if r["team"] == team), key=lambda r: -r["pa"])
                if qbs and qbs[0]["pa"] >= 10:
                    pid = qbs[0]["pid"]
            self._c[k] = pid
        return self._c[k]

    def team_qb_out(self, g, team):
        q = self.v.get("qb_change") or {}
        pid = self.last_starter(g, team)
        return bool(pid) and self.status(g, team, pid) in q.get("statuses", [])

    def qb_block(self, g, team):
        """Sharp qb_change: starter out -> no overs for the team; starter questionable -> no new overs until
        inactives confirm him active."""
        q = self.v.get("qb_change") or {}
        pid = self.last_starter(g, team)
        if not pid:
            return None
        st = self.status(g, team, pid)
        if st in q.get("statuses", []):
            return "starting QB is %s: no overs" % st.lower()
        if q.get("questionable_starter") and (st == "Questionable" or self.flagged(pid, team) == "Questionable") \
                and self.settled(g, team) != "inactives":
            return "starting QB questionable: no overs until inactives are in"
        return None

    def key_out(self, g, team, mkey, pid):
        """Sharp key_out: a teammate with a big share of the targets or carries is out -> legs in that room may not
        carry a big model-vs-book gap (the book has priced the absence; the model has not). Returns his name."""
        ko = self.v.get("key_out") or {}
        rooms = [r for r, ms in (ko.get("rooms") or {}).items() if mkey in ms]
        if not rooms:
            return None
        k = ("ko", g["id"], team)
        if k not in self._c:
            tg = [t for t in self.store.team_games.get(team, []) if t["date"] < g["date"]][-ko.get("lookback_games", 4):]
            tot = {"tg": 0, "ra": 0}
            per = {}
            for t in tg:
                for p2, r in self.store.by_game.get(t["gid"], {}).items():
                    if r["team"] != team:
                        continue
                    tot["tg"] += r["tg"]
                    tot["ra"] += r["ra"]
                    x = per.setdefault(p2, {"tg": 0, "ra": 0})
                    x["tg"] += r["tg"]
                    x["ra"] += r["ra"]
            last = self.store.by_game.get(tg[-1]["gid"], {}) if tg else {}
            out = {}
            for p2, x in per.items():
                if ko.get("only_if_played_last_game") and p2 not in last:
                    continue
                st = self.status(g, team, p2)
                if st not in ko.get("statuses", []):
                    continue
                nm = ((self.people.get(p2) or {}).get("name")) or (self.store.players.get(p2) or [p2])[0]
                if tot["tg"] and x["tg"] / float(tot["tg"]) >= ko.get("target_share", 1):
                    out.setdefault("receiving", []).append((p2, nm))
                if tot["ra"] and x["ra"] / float(tot["ra"]) >= ko.get("carry_share", 1):
                    out.setdefault("rushing", []).append((p2, nm))
            self._c[k] = out
        for r in rooms:
            for p2, nm in self._c[k].get(r, []):
                if p2 != pid:
                    return nm
        return None

    def wind_blocks(self, g, mkey, side):
        wv = self.v.get("weather") or {}
        if not wv or side != wv.get("blocks", "over") or mkey not in wv.get("markets", []):
            return None
        w = self.weather.get(g["id"]) or {}
        if w.get("roof") not in wv.get("roofs", ["outdoors", "open"]) or w.get("wind_mph") is None:
            return None
        k = common.parse_iso(g["start"])
        if k - self.now > timedelta(hours=wv.get("hours_before", 48)):
            return None
        if w["wind_mph"] >= wv["wind_mph"]:
            return "wind %d mph at kickoff" % round(w["wind_mph"])
        return None

    def name_parts(self, pid, feed_name):
        pp = self.people.get(pid) or {}
        full = pp.get("name") or ("%s %s" % (pp.get("first", ""), pp.get("last", ""))).strip() or feed_name
        if pp.get("last"):
            short = names.display(pp.get("football") or pp.get("first"), pp["last"])
        else:
            parts = (feed_name or "").split()
            short = names.display(parts[0], " ".join(parts[1:])) if len(parts) > 1 else feed_name
        return full or feed_name, short


# ---------------------------------------------------------------- one side, every rule, in order
def judge(inp, g, mkey, pid, line, pair, side, mv_at):
    """-> (candidate or None, verdict text, numbers or None). The single place the entry rules live."""
    v, cfg = inp.v, inp.cfg
    m = mdef(cfg, mkey)
    team = pair.get("team")
    if team not in (g["away"], g["home"]):
        return None, "player not on either team", None
    if not m.get("picks"):
        return None, "market not picked (%s)" % m.get("why", "display only"), None
    if m.get("sides", "both") not in ("both", side):
        return None, "this side is never picked", None
    price = pair.get(side)
    if price is None:
        return None, "Hard Rock has no %s price" % side, None
    if pair.get(("under" if side == "over" else "over")) is None:
        return None, "one-sided price: no fair", None
    if not pair.get("ok"):
        return None, "feed check: %s" % pair.get("why"), None
    if inp.started(g):
        return None, "game started", None
    if team in inp.behind:
        return None, "team excluded: %s" % inp.behind[team], None
    mm = inp.model(pid, team, g["id"], mkey, line)
    if mm is None:
        return None, "no model for this player or market", None
    status = inp.status(g, team, pid, pair.get("name"))
    settled = inp.settled(g, team)
    if status in v["status_no_pick"]:
        return None, "listed %s" % status.lower(), None
    flagged = inp.flagged(pid, team, pair.get("name"))
    if flagged in v["status_no_pick"]:
        return None, "listed %s this week" % flagged.lower(), None
    if mm["missed"] >= 1 and (settled == "early" or status is not None):
        return None, "missed his last game: waits for the injury report", None
    if not model.pick_ok(mm["flags"], side):
        return None, "eligibility: %s" % ", ".join(f for f in mm["flags"] if f != "role_jump"), None
    if side == "over":
        qb = inp.qb_block(g, team)
        if qb:
            return None, qb, None
    wind = inp.wind_blocks(g, mkey, side)
    if wind:
        return None, wind, None
    other = pair.get("under" if side == "over" else "over")
    ms = mm["p_over"] if side == "over" else 1.0 - mm["p_over"]
    market, period = contract_market(cfg, mkey)
    e = edge(ms, side, market, period, price, other, v, jump=mm["jump"])
    ok, why = qualifies(e, price, settled, v, inp.tighten)
    if ok and e["gap"] > v["news_gap"] + EPS:
        ko = inp.key_out(g, team, mkey, pid)
        if ko:
            ok, why = False, "big gap with %s out: the book has priced it" % ko
    nums = {"e": e, "m": mm, "settled": settled, "status": status}
    if not ok:
        return None, why, nums
    c = {"id": pick_id(inp.slate, g["id"], pid, market, period, line, side), "game_id": g["id"], "pid": pid,
         "team": team, "opp": g["away"] if team == g["home"] else g["home"], "home": team == g["home"],
         "mkey": mkey, "market": market, "period": period, "line": line, "side": side, "price": price,
         "price_at": mv_at, "e": e, "m": mm, "settled": settled, "name": pair["name"]}
    return c, "QUALIFIES", nums


def _pairs(inp):
    for gid, g in inp.games.items():
        props = (inp.view.get(gid) or {}).get("props") or {}
        for mkey in inp.cfg["ops"]["markets"]:
            mv = props.get(mkey)
            if not mv or mv.get("gone") or not mv.get("posted"):
                continue
            for (pid, line), pair in mv["pairs"].items():
                yield gid, g, mkey, mv, pid, line, pair


def candidates(inp, paused=()):
    out, counts = [], {"checked": 0, "failed": 0}
    for gid in inp.games:
        for mkey, mv in ((inp.view.get(gid) or {}).get("props") or {}).items():
            if mv.get("posted") and not mv.get("gone"):
                counts["checked"] += mv["checked"]
                counts["failed"] += mv["failed"]
    for gid, g, mkey, mv, pid, line, pair in _pairs(inp):
        if mkey in paused or not mdef(inp.cfg, mkey).get("picks"):
            continue
        for side in ("over", "under"):
            c, _, _ = judge(inp, g, mkey, pid, line, pair, side, mv["at"])
            if c:
                out.append(c)
    return out, counts


def explain(inp):
    rows = []
    for gid, g, mkey, mv, pid, line, pair in _pairs(inp):
        for side in ("over", "under"):
            if pair.get(side) is None:
                continue
            c, verdict, nums = judge(inp, g, mkey, pid, line, pair, side, mv["at"])
            row = {"player": pair["name"], "team": pair.get("team"), "market": mkey, "line": line, "side": side,
                   "price": pair[side], "verdict": verdict}
            if nums:
                row.update({k: round(nums["e"][k], 4) for k in nums["e"]})
                row.update(settled=nums["settled"], n=nums["m"]["n"], mu=round(nums["m"]["mu"], 2),
                           flags=",".join(nums["m"]["flags"]))
            rows.append(row)
    return rows


def focus_markets(inp, open_ids=(), near=True):
    """{game_id: set(feed keys)} for pull C: markets holding an open pick, and markets with a side that cleared
    every rule except the price ones and came within near_pick_margin of its margin."""
    v, out = inp.v, {}
    items = record.fold(inp.events)
    feed = {k: m.get("feed_key") for k, m in inp.cfg["ops"]["markets"].items()}
    for i in open_ids:
        it = items.get(i)
        if it and it["kind"] == "single" and feed.get(it["shown"].get("mkey")):
            out.setdefault(it["shown"]["game_id"], set()).add(feed[it["shown"]["mkey"]])
    if not near:
        return out
    for gid, g, mkey, mv, pid, line, pair in _pairs(inp):
        if not mdef(inp.cfg, mkey).get("picks"):
            continue
        for side in ("over", "under"):
            if pair.get(side) is None:
                continue
            c, verdict, nums = judge(inp, g, mkey, pid, line, pair, side, mv["at"])
            near = nums is not None and verdict in ("margin too small", "chance under the minimum") and \
                nums["e"]["margin"] - margin_need(nums["settled"], v, inp.tighten) >= v["near_pick_margin"] - EPS
            if c or near:
                out.setdefault(gid, set()).add(feed[mkey])
    return out


def apply_caps(cands, v, live, pulled_players=()):
    """Best line per player and market, then 1 per player, max_per_team, max_per_game (counting open picks).
    pulled_players: players whose pick was pulled this week (not for a line move): they count toward the 1 per
    player cap, so a pulled player does not come back in another market the same week."""
    by = {}
    for c in cands:
        k = (c["pid"], c["mkey"])
        if k not in by or rank_key(c["e"]["p"], c["e"]["b"], c["settled"]) < rank_key(by[k]["e"]["p"], by[k]["e"]["b"], by[k]["settled"]):
            by[k] = c
    ranked = sorted(by.values(), key=lambda c: rank_key(c["e"]["p"], c["e"]["b"], c["settled"]) + (c["id"],))
    per_p, per_t, per_g = {}, {}, {}
    for pid in pulled_players:
        per_p[pid] = per_p.get(pid, 0) + 1
    for x in live.values():
        per_p[x["pid"]] = per_p.get(x["pid"], 0) + 1
        per_t[(x["game_id"], x["team"])] = per_t.get((x["game_id"], x["team"]), 0) + 1
        per_g[x["game_id"]] = per_g.get(x["game_id"], 0) + 1
    out = []
    for c in ranked:
        tk = (c["game_id"], c["team"])
        if per_p.get(c["pid"], 0) >= v["max_per_player"] or per_t.get(tk, 0) >= v["max_per_team"] or \
                per_g.get(c["game_id"], 0) >= v["max_per_game"]:
            continue
        per_p[c["pid"]] = per_p.get(c["pid"], 0) + 1
        per_t[tk] = per_t.get(tk, 0) + 1
        per_g[c["game_id"]] = per_g.get(c["game_id"], 0) + 1
        out.append(c)
    return out


# ---------------------------------------------------------------- pull reasons (sentences shown on the page)
def pull_reason(code, **kw):
    if code == "inactive":
        return "Inactive today."
    if code == "status":
        return "Listed %s." % kw["status"].lower()
    if code == "gate":
        return "Hard Rock moved it to %s, past our %s limit." % (common.fmt_price(kw["price"]),
                                                                 common.fmt_price(kw["gate"]))
    if code == "max_price":
        return "Hard Rock moved it to %s, longer than we take." % common.fmt_price(kw["price"])
    if code == "line_moved":
        return "Hard Rock moved the line to %s." % fmt_line(kw["line"])
    if code == "line_gone":
        return "Hard Rock took this line down."
    if code == "market_gone":
        return "Hard Rock took this market down."
    if code == "margin":
        return "The price at %s no longer clears the bar." % common.fmt_price(kw["price"])
    if code == "drift":
        return "The price moved against this side before inactives were in."
    if code == "wind":
        return "Strong %s." % kw["wind"]
    if code == "leg":
        return "%s leg pulled." % kw["who"]
    return "Pulled."


# ---------------------------------------------------------------- one run of the engine
def evaluate(inp, paused=(), block_new=False):
    """Returns (new_events, state). Writes nothing."""
    v, now = inp.v, inp.now
    at = common.iso(now)
    items = record.fold(inp.events)
    week = {i: it for i, it in items.items() if it["slate"] == inp.slate}
    singles = {i: it for i, it in week.items() if it["kind"] == "single"}
    new, live = [], {}

    # ---- 1. picks already shown: lock, pull, or carry on with the newest price
    for i, it in sorted(singles.items()):
        if not record.is_open(it):
            continue
        sh = it["shown"]
        g = inp.games.get(sh["game_id"])
        if g is None or inp.started(g):
            new.append({"t": "locked", "id": i, "at": at, "state": it["state"]})
            continue
        status = inp.status(g, sh["team"], sh["player_id"], sh.get("feed_name"))
        if status == "Inactive":
            new.append({"t": "pulled", "id": i, "at": at, "code": "inactive", "reason": pull_reason("inactive")})
            continue
        if status in v["status_no_pick"]:
            new.append({"t": "pulled", "id": i, "at": at, "code": "injury", "reason": pull_reason("status", status=status)})
            continue
        settled = inp.settled(g, sh["team"])
        if SETTLED_RANK.get(settled, 0) < SETTLED_RANK.get(it["state"], 0):
            settled = it["state"]                           # only moves forward
        wind = inp.wind_blocks(g, sh["mkey"], sh["side"]) if (v.get("weather") or {}).get("pulls_shown") else None
        if wind:
            new.append({"t": "pulled", "id": i, "at": at, "code": "wind", "reason": pull_reason("wind", wind=wind)})
            continue
        mv = ((inp.view.get(sh["game_id"]) or {}).get("props") or {}).get(sh["mkey"])
        last_read = common.parse_iso(it["prices"][-1]["at"])
        price, price_at, other = record.last_price(it), last_read, None
        pair = None if mv is None or mv.get("gone") else mv["pairs"].get((sh["player_id"], sh["line"]))
        bad_pair = pair is not None and pair.get(sh["side"]) is not None and not pair.get("ok")
        newer = mv is not None and mv.get("at") is not None and mv["at"] > last_read
        if newer and bad_pair:
            pass                                            # newest read failed the feed checks: keep the last good one
        elif newer:
            if mv.get("gone"):
                new.append({"t": "pulled", "id": i, "at": at, "code": "market_gone", "reason": pull_reason("market_gone")})
                continue
            if pair is None or pair.get(sh["side"]) is None:
                moved = sorted(l for (pid, l), p in mv["pairs"].items()
                               if pid == sh["player_id"] and l != sh["line"] and p.get(sh["side"]) is not None)
                if moved:
                    new.append({"t": "pulled", "id": i, "at": at, "code": "line_moved",
                                "reason": pull_reason("line_moved", line=moved[0])})
                else:
                    new.append({"t": "pulled", "id": i, "at": at, "code": "line_gone", "reason": pull_reason("line_gone")})
                continue
            price, price_at = pair[sh["side"]], mv["at"]
            other = pair.get("under" if sh["side"] == "over" else "over")
        elif pair is not None and pair.get("ok"):
            other = pair.get("under" if sh["side"] == "over" else "over")
        mm = inp.model(sh["player_id"], sh["team"], sh["game_id"], sh["mkey"], sh["line"])
        if mm is None:
            ms, jump, why = sh.get("model"), False, None
        else:
            ms = mm["p_over"] if sh["side"] == "over" else 1.0 - mm["p_over"]
            jump, why = mm["jump"], mm["why"]
        e = edge(ms, sh["side"], sh["market"], sh["period"], price, other, v, fair=it.get("fair") or sh.get("fair"),
                 jump=jump)
        code = None
        if odds.decimal(price) < odds.decimal(v["price_gate"]) - EPS:
            code, text = "gate", pull_reason("gate", price=price, gate=v["price_gate"])
        elif odds.decimal(price) > odds.decimal(v["max_price"]) + EPS:
            code, text = "max_price", pull_reason("max_price", price=price)
        elif e["margin"] < v["pull_margin"] - EPS:
            code, text = "margin", pull_reason("margin", price=price)
        elif settled != "inactives" and odds.break_even(record.first_price(it)) - e["b"] >= v["drift_pull"] - EPS:
            code, text = "drift", pull_reason("drift")
        if code:
            new.append({"t": "pulled", "id": i, "at": at, "code": code, "reason": text, "seen_price": price})
            continue
        reason = (reason_text(why, v.get("reason_max_chars", 120)) if why else None) or it["reason"]
        p_pct, b_pct = pcts(e)
        if price != record.last_price(it):
            new.append({"t": "price", "id": i, "at": at, "price": price, "price_at": common.iso(price_at),
                        "p": round(e["p"], 4), "breakeven": round(e["b"], 4), "fair": round(e["fair"], 4),
                        "p_pct": p_pct, "breakeven_pct": b_pct, "reason": reason})
        elif round(e["p"], 4) != it["p"] or reason != it["reason"] or p_pct != it.get("p_pct"):
            new.append({"t": "update", "id": i, "at": at, "p": round(e["p"], 4), "breakeven": round(e["b"], 4),
                        "fair": round(e["fair"], 4), "p_pct": p_pct, "breakeven_pct": b_pct, "reason": reason})
        if settled != it["state"]:
            new.append({"t": "state", "id": i, "at": at, "state": settled})
        live[i] = {"p": e["p"], "b": e["b"], "settled": settled, "price": price, "price_at": price_at,
                   "pid": sh["player_id"], "team": sh["team"], "game_id": sh["game_id"], "start": sh["start"]}

    # ---- 2. new picks, inside the caps
    cands, counts = candidates(inp, paused)
    if block_new:
        cands = []
    items_now = record.fold(inp.events + new)
    pulled_players = {it["shown"]["player_id"] for it in items_now.values()
                      if it["slate"] == inp.slate and it["kind"] == "single" and it["pulled"]
                      and it["pulled"].get("code") != "line_moved"}
    fresh = apply_caps([c for c in cands if c["id"] not in singles], v, live, pulled_players)
    for c in fresh:
        live[c["id"]] = {"p": c["e"]["p"], "b": c["e"]["b"], "settled": c["settled"], "price": c["price"],
                         "price_at": c["price_at"], "pid": c["pid"], "team": c["team"], "game_id": c["game_id"],
                         "start": inp.games[c["game_id"]]["start"]}
    order = sorted(live, key=lambda i: rank_key(live[i]["p"], live[i]["b"], live[i]["settled"]) + (i,))
    ranks = {i: n + 1 for n, i in enumerate(order)}
    for c in fresh:
        g, e, mm = inp.games[c["game_id"]], c["e"], c["m"]
        full, short = inp.name_parts(c["pid"], c["name"])
        body = {
            "id": c["id"], "rank": ranks[c["id"]], "game_id": c["game_id"], "player": full, "player_short": short,
            "feed_name": c["name"], "player_id": c["pid"], "pos": inp.pos(c["pid"]), "team": c["team"],
            "opp": c["opp"], "home": c["home"], "start": g["start"], "mkey": c["mkey"], "market": c["market"],
            "period": c["period"], "line": c["line"], "side": c["side"],
            "bet": bet_text(c["side"], c["line"], c["mkey"], inp.cfg), "book": "Hard Rock", "price": c["price"],
            "price_at": common.iso(c["price_at"]), "p": round(e["p"], 4), "breakeven": round(e["b"], 4),
            "fair": round(e["fair"], 4), "p_pct": pcts(e)[0], "breakeven_pct": pcts(e)[1],
            "model": round(e["model"], 4), "model_adj": round(e["model_adj"], 4), "mu": round(mm["mu"], 2),
            "reason": reason_text(mm["why"], v.get("reason_max_chars", 120)), "state": c["settled"], "thin": bool(mm["thin"]),
            "games_used": mm["n"], "games_this_season": mm["n_season"], "flags": mm["flags"],
            "lines_from": mm["ctx_source"],
        }
        new.append({"t": "shown", "kind": "single", "id": c["id"], "at": at, "slate": inp.slate, "pick": body})

    # ---- 3. parlays
    new += _parlays(inp, record.fold(inp.events + new), live, at)
    return new, {"counts": counts, "ranks": ranks, "live": live}


def _parlay_numbers(leg_ids, live):
    dec = p = b = 1.0
    for l in leg_ids:
        dec *= odds.decimal(live[l]["price"])
        p *= live[l]["p"]
        b *= live[l]["b"]
    least = min((live[l]["settled"] for l in leg_ids), key=lambda s: SETTLED_RANK.get(s, 0))
    return {"price": odds.american(dec), "p": p, "b": b, "legs": {l: live[l]["price"] for l in leg_ids},
            "price_at": max(live[l]["price_at"] for l in leg_ids), "state": least}


def _parlays(inp, items, live, at):
    v, new = inp.v, []
    week = {i: it for i, it in items.items() if it["slate"] == inp.slate}
    open_parlays, use = [], {}
    for i, it in sorted(week.items()):
        if it["kind"] != "parlay" or not record.is_open(it):
            continue
        legs = it["shown"]["legs"]
        pulled = [l for l in legs if l in week and week[l]["pulled"]]
        locked = [l for l in legs if l in week and week[l]["locked_at"]]
        if pulled:
            who = week[pulled[0]]["shown"].get("player_short") or week[pulled[0]]["shown"]["player"]
            new.append({"t": "pulled", "id": i, "at": at, "code": "leg", "reason": pull_reason("leg", who=who),
                        "legs": pulled})
            continue
        if locked or any(l not in live for l in legs):
            new.append({"t": "locked", "id": i, "at": at, "state": it["state"]})
            continue
        n = _parlay_numbers(legs, live)
        if n["legs"] != it["prices"][-1].get("legs") or n["price"] != record.last_price(it):
            new.append({"t": "price", "id": i, "at": at, "price": n["price"], "price_at": common.iso(n["price_at"]),
                        "leg_prices": n["legs"], "p": round(n["p"], 4), "breakeven": round(n["b"], 4),
                        "p_pct": pct_floor(n["p"]), "breakeven_pct": pct_round(n["b"])})
        elif round(n["p"], 4) != it["p"]:
            new.append({"t": "update", "id": i, "at": at, "p": round(n["p"], 4), "breakeven": round(n["b"], 4),
                        "p_pct": pct_floor(n["p"]), "breakeven_pct": pct_round(n["b"])})
        if n["state"] != it["state"]:
            new.append({"t": "state", "id": i, "at": at, "state": n["state"]})
        open_parlays.append(legs)
        for l in legs:
            use[l] = use.get(l, 0) + 1

    lo, hi = odds.decimal(v["parlay_leg_min_price"]), odds.decimal(v["parlay_leg_max_price"])
    legs_ok = [i for i, x in live.items() if x["p"] >= v["parlay_leg_min_p"] - EPS
               and lo - EPS <= odds.decimal(x["price"]) <= hi + EPS]
    legs_ok.sort(key=lambda i: rank_key(live[i]["p"], live[i]["b"], live[i]["settled"]) + (i,))
    legs_ok = legs_ok[:12]

    def best(size, forbid_shared_with=()):
        top = None
        for combo in itertools.combinations(legs_ok, size):
            if v["parlay_one_per_game"] and len({live[l]["game_id"] for l in combo}) < size:
                continue
            if len({live[l]["pid"] for l in combo}) < size:
                continue
            if any(use.get(l, 0) >= v["parlay_leg_max_use"] for l in combo):
                continue
            if any(set(combo) & set(o) for o in forbid_shared_with):
                continue
            pid_ = parlay_id(inp.slate, combo)
            if pid_ in week:
                continue
            n = _parlay_numbers(combo, live)
            floor = v["parlay_min_p_2"] if size == 2 else v["parlay_min_p_3"]
            if n["p"] < floor - EPS or n["p"] < n["b"] + v["parlay_edge"] - EPS:
                continue
            key = (-(round(n["p"] - n["b"], 6)), -round(n["p"], 6), pid_)
            if top is None or key < top[0]:
                top = (key, combo, n, pid_)
        return top

    def add(found):
        _, combo, n, pid_ = found
        legs = sorted(combo, key=lambda l: rank_key(live[l]["p"], live[l]["b"], live[l]["settled"]) + (l,))
        body = {"id": pid_, "legs": legs, "n": len(legs), "price": n["price"], "price_at": common.iso(n["price_at"]),
                "leg_prices": n["legs"], "p": round(n["p"], 4), "breakeven": round(n["b"], 4),
                "p_pct": pct_floor(n["p"]), "breakeven_pct": pct_round(n["b"]), "state": n["state"],
                "start": min(live[l]["start"] for l in legs)}
        new.append({"t": "shown", "kind": "parlay", "id": pid_, "at": at, "slate": inp.slate, "parlay": body})
        open_parlays.append(legs)
        for l in legs:
            use[l] = use.get(l, 0) + 1

    if v["parlay_min_legs"] <= 2:
        while sum(1 for o in open_parlays if len(o) == 2) < 2 and len(open_parlays) < v["parlay_cap"]:
            f = best(2, [o for o in open_parlays if len(o) == 2])
            if not f:
                break
            add(f)
    if v["parlay_max_legs"] >= 3:
        if not any(len(o) == 3 for o in open_parlays) and len(open_parlays) < v["parlay_cap"]:
            f = best(3)
            if f:
                add(f)
    return new


# ---------------------------------------------------------------- the safety switches
def paused_markets(events, cfg):
    """Per market: pause after pause_legs graded if hit rate is pause_gap under needed or units <= pause_units.
    Whole board: after board_legs graded, if board_gap under needed, margins go up by board_tighten.
    Returns ({ops market key: sentence}, tighten)."""
    v = cfg["values"]
    rec, _ = record.build_views(events, cfg)
    out = {}
    for r in rec["summary"]["rows"]:
        if r["key"] in ("parlays", "pulled") or r["n"] < v["pause_legs"]:
            continue
        if (r["needed"] is not None and r["hit_rate"] <= r["needed"] - v["pause_gap"] + EPS) or r["units"] <= v["pause_units"]:
            market, _, period = r["key"].partition("|")
            for k, m in cfg["ops"]["markets"].items():
                if (m.get("model") or k) == market and m.get("period") == period:
                    out[k] = "%s picks are paused: results are running too far under the price." % r["label"]
    h = rec["summary"]["headline"]
    tighten = 0.0
    if h["n"] >= v.get("board_legs", 10 ** 9) and h["needed"] is not None and h["hit_rate"] is not None and \
            h["hit_rate"] <= h["needed"] - v["board_gap"] + EPS:
        tighten = v["board_tighten"]
    return out, tighten
