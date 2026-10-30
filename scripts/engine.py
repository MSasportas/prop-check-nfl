"""engine.py: Gap + Guard picks kept honest through the week, written to the Record.

gg.py decides what is a NEW pick (Hard Rock's price vs the other books' no-vig median, then the model's veto). This file owns
everything around it:
  Inputs        stats + prices + injury news, as the engine reads them (model, status, settledness, key-out, room change, wind)
  pre_check     the NFL vetoes that come BEFORE any price talk (status, missed last game, QB, eligibility, room change, wind,
                key teammate out until inactives are in)
  evaluate      lock / pull / keep the picks already shown, add the new ones gg.py found, sweep old open items
  safety_pause  new-era singles down 10 units or more: no new picks until a person reviews

A pick is never silently removed: each run it is kept (frozen at the price of the first passing read), pulled with a plain
reason and an "if you already bet it" sentence, or locked at kickoff. slate.json is rebuilt from the Record each run.
Settledness (per game): injury_report -> inactives (only moves forward). There is no "early" pick: nothing is made before
the final injury report counts as in.
"""
import re
import math
import sys
from datetime import timedelta

import common
import gg
import injuries
import model
import names
import odds
import record
import sched

EPS = 1e-9
SELF = sys.modules[__name__]                       # gg.py calls back into pre_check / model_side through this
SETTLED_RANK = {"early": 0, "injury_report": 1, "inactives": 2}
SETTLED_TEXT = {"early": "Early", "injury_report": "Injury report in", "inactives": "Inactives confirmed"}


class ConfigError(Exception):
    pass


def check_config(cfg):
    v, t = cfg["values"], gg.T(cfg)
    if abs(t["model_weight"] + t["market_weight"] - 1.0) > 1e-9:
        raise ConfigError("model_weight + market_weight must equal 1")
    for k in ("gap_ev", "pin_ev_min", "pull_ev", "final_ev"):
        if not (0 <= t[k] <= 1):
            raise ConfigError("%s must be between 0 and 1" % k)
    for k in ("hold_min", "hold_max"):
        if not (0 <= v[k] <= 1):
            raise ConfigError("%s must be between 0 and 1" % k)
    if t["price_gate"] < -150:
        raise ConfigError("no leg can be priced worse than -150 (customer rule)")
    if t["price_gate"] >= 0 or t["max_price"] < 100:
        raise ConfigError("price_gate must be negative and max_price +100 or longer")
    if t["books_min"] < 4:
        raise ConfigError("books_min must be 4 or more")
    if set(t["flag_pulls"]) - {"R", "C"}:
        raise ConfigError("only R and C reads can flag")
    for m in gg.market_names(cfg):
        md = cfg["ops"]["markets"][m]
        if md.get("period", "FG") != "FG" or md.get("alt_key"):
            raise ConfigError("market %s: picks are full game, main line only" % m)


# ---------------------------------------------------------------- market names
def mdef(cfg, mkey):
    return cfg["ops"]["markets"][mkey]


def contract_market(cfg, mkey):
    """ops.markets key ('rec_yds') -> (model market 'rec_yds', period 'FG')."""
    m = mdef(cfg, mkey)
    return m.get("model") or mkey, m.get("period", "FG")


def market_label(cfg, market, period):
    for k, m in cfg["ops"]["markets"].items():
        if (m.get("model") or k) == market and m.get("period", "FG") == period:
            return m["label"]
    return "%s %s" % (market, period)


# ---------------------------------------------------------------- the model's side chance (the guard's input)
def shrink_for(market, period, side, v):
    return v["shrink"].get("%s|%s|%s" % (market, period, "O" if side == "over" else "U"), 0.05)


def model_side(inp, mm, side, mname):
    """The model's chance for the side, minus the DS shrink for market/side, minus jump_penalty on an over after a role jump.
    This is the number the guard uses (20%); it can only veto."""
    v = inp.v
    market, period = contract_market(inp.cfg, mname)
    ms = mm["p_over"] if side == "over" else 1.0 - mm["p_over"]
    adj = ms - shrink_for(market, period, side, v)
    if side == "over" and mm["jump"]:
        adj -= v["jump_penalty"]
    return min(0.999, max(0.001, adj))


# ---------------------------------------------------------------- wording (plain, no dashes)
def fmt_line(line):
    return "%g" % line


def bet_text(side, line, mkey, cfg):
    m = mdef(cfg, mkey)
    return "%s %s %s" % ("Over" if side == "over" else "Under", fmt_line(line), m.get("bet_noun") or m["noun"])


def pct_floor(x):
    return int(math.floor(x * 100 + 1e-7))


def pct_round(x):
    return int(math.floor(x * 100 + 0.5))


def _supports(part, side):
    """False when a context clause argues against the side (e.g. 'allows the most' on an under)."""
    s = part.lower()
    if " allows the " in s:
        return ("most" in s) == (side == "over")
    m = re.search(r"projected for ([\d.]+) points", s)
    if m and side in ("over", "under"):
        return (float(m.group(1)) > model.PARAMS["lg_team_total"]) == (side == "over")
    return True


def reason_text(why_parts, limit=120, side=None):
    """'7.4 catches a game his last 5 (3 of 5 over 6.5). DEN allows the 4th fewest catches to WRs.'
    Context clauses that argue against the side are left out."""
    parts = [w.strip() for w in (why_parts or []) if w and w.strip()]
    if not parts:
        return None
    if side:
        parts = parts[:1] + [w for w in parts[1:] if _supports(w, side)]
    cap = lambda s: s[0].upper() + s[1:]
    out = cap(parts[0]) + "."
    if len(parts) > 1 and len(out) + 1 + len(parts[1]) + 1 <= limit:
        out += " " + cap(parts[1]) + "."
    return out.replace("\u2014", ",").replace("\u2013", "-")


def rank_key(final_ev, settled, i=""):
    return (-round(final_ev, 6), -SETTLED_RANK.get(settled, 0), i)


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
               "opp_raw": self.store.raw_ranks(g["date"], self.season, period).get(opp) or {},
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

    def _share(self, rows, team):
        """(target share, carry share) of `team` over these rows' games."""
        tg = sum(r["tg"] for r in rows)
        ra = sum(r["ra"] for r in rows)
        ttg = tra = 0
        for r in rows:
            for x in self.store.by_game.get(r["gid"], {}).values():
                if x["team"] == team:
                    ttg += x["tg"]
                    tra += x["ra"]
        return (tg / float(ttg) if ttg else 0.0), (ra / float(tra) if tra else 0.0)

    def room_changes(self, g, team):
        """Sharp room_change: {room: [(pid, sentence)]} for `team` before game g. See config values.room_change."""
        rc = self.v.get("room_change") or {}
        k = ("rc", g["id"], team)
        if k in self._c:
            return self._c[k]
        out = {}
        self._c[k] = out
        if not rc:
            return out
        d = g["date"]
        n_look, after = rc.get("lookback_games", 4), rc.get("team_games_after", 2)
        poss, leave_st = set(rc.get("positions", ["WR", "TE", "RB"])), set(rc.get("leave_statuses", []))
        tg = [t["date"] for t in self.store.team_games.get(team, []) if t["date"] < d]
        roster_on = any("status" in p for p in self.people.values())   # weekly roster loaded

        def games_since(dt):
            return sum(1 for x in tg if x > dt)

        def rooms_for(rows, t):
            s_tg, s_ra = self._share(rows[-n_look:], t)
            r = []
            if s_tg >= rc.get("target_share", 1) - EPS:
                r.append("receiving")
            if s_ra >= rc.get("carry_share", 1) - EPS:
                r.append("rushing")
            return r

        def name(pid):
            return ((self.people.get(pid) or {}).get("name")) or (self.store.players.get(pid) or [pid])[0]

        recent = set()
        for t in self.store.team_games.get(team, []):
            if t["date"] < d and games_since(t["date"]) < max(after, rc.get("max_wait_team_games", 6)):
                recent |= {p2 for p2, r in self.store.by_game.get(t["gid"], {}).items() if r["team"] == team}
        cands = recent | {p2 for p2, p in self.people.items() if p.get("team") == team}
        for p2 in sorted(cands):
            if self.pos(p2) not in poss:
                continue
            rows = self.store.player_rows(p2, before=d)
            if not rows:
                continue
            person = self.people.get(p2)
            # ---- joined this team: his rows before his current stint are for another team
            if (person or {}).get("team") == team or rows[-1]["team"] == team:
                n_with = 0
                for r in reversed(rows):
                    if r["team"] != team:
                        break
                    n_with += 1
                old = rows[:len(rows) - n_with]
                if old and n_with < after and (person or {}).get("status") not in leave_st:
                    u = old[-1]["team"]
                    block = []
                    for r in reversed(old):
                        if r["team"] != u:
                            break
                        block.insert(0, r)
                    if games_since(block[-1]["date"]) < rc.get("max_wait_team_games", 6):
                        for room in rooms_for(block, u):
                            out.setdefault(room, []).append(
                                (p2, "%s joined %s (%d game%s with them)" % (name(p2), team, n_with,
                                                                             "" if n_with == 1 else "s")))
            # ---- left this team
            mine = [r for r in rows if r["team"] == team]
            if not mine or games_since(mine[-1]["date"]) >= after:
                continue
            last = mine[-1]
            later = [r for r in rows if r["date"] > last["date"] and r["team"] != team]
            why = None
            if later:
                why = "now with %s" % later[-1]["team"]
            elif roster_on and (person is None or person.get("from_rows")):
                why = "off the roster"
            elif person is not None and person.get("team") not in (None, team):
                why = "now with %s" % person["team"]
            elif person is not None and person.get("status") in leave_st:
                why = "on reserve (IR/PUP/NFI)"
            if why:
                block = []
                for r in reversed(rows[:rows.index(last) + 1]):
                    if r["team"] != team:
                        break
                    block.insert(0, r)
                for room in rooms_for(block, team):
                    out.setdefault(room, []).append((p2, "%s left %s (%s)" % (name(p2), team, why)))
        return out

    def room_change(self, g, team, mkey):
        """Sentence when a room change blocks this market for this team, else None."""
        rc = self.v.get("room_change")
        if not rc:
            return None
        rooms = [r for r, ms in ((self.v.get("key_out") or {}).get("rooms") or {}).items() if mkey in ms]
        if not rooms:
            return None
        ch = self.room_changes(g, team)
        for r in rooms:
            for _, s in ch.get(r, []):
                return "%s: %s picks wait %d games" % (s, r, rc.get("team_games_after", 2))
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



# ---------------------------------------------------------------- the NFL vetoes that come before any price talk
def pre_check(inp, g, mkey, pid, line, pair, side, settled):
    """-> dict(reason, mm, status). reason is None when nothing blocks this side; mm is the model dict (None = no model)."""
    v, cfg = inp.v, inp.cfg
    m = mdef(cfg, mkey)
    team = pair.get("team")
    out = {"reason": None, "mm": None, "status": None}

    def block(text):
        out["reason"] = text
        return out
    if not m.get("picks"):
        return block("market not picked")
    if m.get("sides", "both") not in ("both", side):
        return block("this side is never picked")
    mm = inp.model(pid, team, g["id"], mkey, line)
    if mm is None:
        return block("no model for this player or market")
    out["mm"] = mm
    status = inp.status(g, team, pid, pair.get("name"))
    out["status"] = status
    if status in v["status_no_pick"]:
        return block("listed %s" % status.lower())
    flagged = inp.flagged(pid, team, pair.get("name"))
    if flagged in v["status_no_pick"]:
        return block("listed %s this week" % flagged.lower())
    if mm["missed"] >= 1 and (settled == "early" or status is not None):
        return block("missed his last game: waits for the injury report")
    if side == "over":
        qb = inp.qb_block(g, team)
        if qb:
            return block(qb)
    if not model.pick_ok(mm["flags"], side, v["elig"].get("role_cv_blocks", "over")):
        return block("eligibility: %s" % ", ".join(f for f in mm["flags"] if f != "role_jump"))
    rc = inp.room_change(g, team, mkey)
    if rc:
        return block("room change: %s" % rc)
    wind = inp.wind_blocks(g, mkey, side)
    if wind:
        return block(wind)
    if settled != "inactives":
        ko = inp.key_out(g, team, mkey, pid)
        if ko:
            return block("key teammate %s is out: waits for inactives" % ko)
    return out


# ---------------------------------------------------------------- pull reasons (sentences shown on the page)
def pull_reason(code, **kw):
    if code == "inactive":
        return "Inactive today."
    if code == "status":
        return "Listed %s." % kw["status"].lower()
    if code == "line_moved":
        return "Hard Rock moved the line to %s." % fmt_line(kw["line"])
    if code == "line_gone":
        return "Hard Rock took this line down."
    if code == "market_gone":
        return "Hard Rock took this market down."
    if code == "wind":
        return "Strong %s." % kw["wind"]
    return "Pulled."


# ---------------------------------------------------------------- the safety switch
def safety_pause(events, cfg):
    """The page sentence when the new era's graded singles (not pulled) are down ops.safety_pause.units or more, else None.
    New picks stop; shown picks are still looked after."""
    sp = (cfg["ops"].get("safety_pause") or {})
    if sp.get("units") is None:
        return None
    units = record.era_units(events, gg.era(cfg))
    return sp.get("text") if units <= sp["units"] + EPS else None


# ---------------------------------------------------------------- focus markets (tight tier: C only where it matters)
def focus_markets(inp, by_game, open_items, near=True):
    """{game_id: set(feed keys)} for pull C when credits are tight: markets holding an open pick, and markets with a side that
    passes every veto and clears the gap line by pull_ev (near-pick), on the newest usable read."""
    cfg, t = inp.cfg, gg.T(inp.cfg)
    out = {}
    for it in open_items:
        sh = it["shown"]
        feed = mdef(cfg, sh["mkey"]).get("feed_key")
        if feed:
            out.setdefault(sh["game_id"], set()).add(feed)
    if not near:
        return out
    for gid, g in inp.games.items():
        if inp.started(g):
            continue
        f = gg.newest_read(by_game.get(gid, []), g, cfg)
        if f is None:
            continue
        for mname in gg.market_names(cfg):
            pairs, others = gg.hr_market(f, mname, cfg), gg.other_lines(f, mname, cfg)
            for (pid, line), pair in (pairs or {}).items():
                if pair["over"] is None or pair["under"] is None or pair["why"] is not None:
                    continue
                for side in ("over", "under"):
                    a = gg.assess(side, line, pair[side], (others or {}).get((pid, line)), lambda: None, t)
                    ok_gap = a["step1"] or (set(a["fails"]) == {"gap"} and a["gap_ev"] is not None
                                            and a["gap_ev"] >= t["pull_ev"] - EPS)
                    if ok_gap and not pre_check(inp, g, mname, pid, line, pair, side, inp.settled(g, pair["team"]))["reason"]:
                        out.setdefault(gid, set()).add(mdef(cfg, mname)["feed_key"])
    return out


# ---------------------------------------------------------------- one run of the engine
def _count_toward_caps(items, slate, cfg):
    """This slate's new-era singles that count toward the caps: open, locked, and pulled (but a pull for a line move does not)."""
    mine = []
    for it in items.values():
        sh = it["shown"]
        if it["kind"] != "single" or it["slate"] != slate or sh.get("era") != gg.era(cfg):
            continue
        if it["pulled"] and it["pulled"].get("code") == "line_moved":
            continue
        mine.append({"pid": sh["player_id"], "game_id": sh["game_id"], "team": sh["team"]})
    return mine


def evaluate(inp, by_game, block_new=False, log_items=None):
    """Returns (new Record events, extra). Writes nothing. extra: live picks, ranks, the research-log events, what was blocked.
    by_game: gg.reads_by_game(fetches, now). block_new: stats problems or the safety pause (nothing new, shown picks still
    looked after)."""
    v, cfg, now = inp.v, inp.cfg, inp.now
    t = gg.T(cfg)
    at = common.iso(now)
    items = record.fold(inp.events)
    log_items = log_items if log_items is not None else {}
    new, live = [], {}

    # ---- 0. old open items (retired rules, or any slate that is over): lock them once their kickoff has passed
    for i, it in sorted(items.items()):
        if it["slate"] == inp.slate or not record.is_open(it):
            continue
        start = common.parse_iso((it["shown"].get("start") or "")) if it["shown"].get("start") else None
        if start is not None and now >= start:
            new.append({"t": "locked", "id": i, "at": at, "state": it["state"]})

    # ---- 1. new-era picks already shown this slate: lock, pull, or carry on
    week = {i: it for i, it in items.items() if it["slate"] == inp.slate and it["kind"] == "single"}
    for i, it in sorted(week.items()):
        if not record.is_open(it):
            continue
        sh = it["shown"]
        g = inp.games.get(sh["game_id"])
        if g is None or inp.started(g):
            new.append({"t": "locked", "id": i, "at": at, "state": it["state"]})
            continue
        if sh.get("era") != gg.era(cfg):
            continue                                        # a retired-rules pick of this slate: only locked (above on kickoff)
        status = inp.status(g, sh["team"], sh["player_id"], sh.get("feed_name"))
        if status == "Inactive":
            new.append(_pull(i, at, "inactive", pull_reason("inactive"), sh, it, status=status))
            continue
        if status in v["status_no_pick"]:
            new.append(_pull(i, at, "injury", pull_reason("status", status=status), sh, it, status=status))
            continue
        settled = inp.settled(g, sh["team"])
        if SETTLED_RANK.get(settled, 0) < SETTLED_RANK.get(it["state"], 0):
            settled = it["state"]                           # only moves forward
        wind = inp.wind_blocks(g, sh["mkey"], sh["side"]) if (v.get("weather") or {}).get("pulls_shown") else None
        if wind:
            new.append(_pull(i, at, "wind", pull_reason("wind", wind=wind), sh, it))
            continue
        mm = inp.model(sh["player_id"], sh["team"], sh["game_id"], sh["mkey"], sh["line"])
        tight = None
        if mm is not None and "role_unstable" in mm["flags"] and \
                not model.pick_ok(mm["flags"], sh["side"], v["elig"].get("role_cv_blocks", "over")):
            tight = "his role swings too much game to game"
        if tight is None:
            tight = inp.room_change(g, sh["team"], sh["mkey"])
        if tight:
            new.append(_pull(i, at, "rules", tight[0].upper() + tight[1:] + ".", sh, it))
            continue
        # a newer read can only take the pick back (it keeps its frozen price)
        f = gg.newest_read(by_game.get(sh["game_id"], []), g, cfg)
        if f is not None and common.parse_iso(f["at"]) > common.parse_iso(sh.get("read_at") or it["shown_at"]):
            pulled = _judge_read(inp, g, f, sh, it, i, at, t)
            if pulled:
                new.append(pulled)
                continue
        if settled != it["state"]:
            new.append({"t": "state", "id": i, "at": at, "state": settled})
        live[i] = {"final_ev": sh.get("final_ev") or 0.0, "settled": settled, "price": it["prices"][0]["price"],
                   "pid": sh["player_id"], "team": sh["team"], "game_id": sh["game_id"], "start": sh["start"]}

    # ---- 2. new picks
    found = {"flags": [], "vetoes": [], "capped": [], "blocked": {}, "considered": 0}
    if not block_new:
        items_now = record.fold(inp.events + new)
        posted = {"ids": set(items_now), "mine": _count_toward_caps(items_now, inp.slate, cfg)}
        found = gg.find_flags(inp, SELF, by_game, posted, log_items)
    for a in found["flags"]:
        g = a["game"]
        live[a["id"]] = {"final_ev": a["final_ev"], "settled": a["settled"], "price": a["price"], "pid": a["pid"],
                         "team": a["team"], "game_id": g["id"], "start": g["start"]}
    order = sorted(live, key=lambda i: rank_key(live[i]["final_ev"], live[i]["settled"], i))
    ranks = {i: n + 1 for n, i in enumerate(order)}
    for a in found["flags"]:
        new.append(_shown(inp, a, ranks[a["id"]], at))

    # ---- 3. research log: vetoes, capped rows, closes
    log = [gg.log_event("veto", dict(a, slate=inp.slate), now, guard="veto") for a in found["vetoes"]]
    log += [gg.log_event("capped", dict(a, slate=inp.slate), now, cap=a["cap"]) for a in found["capped"]]
    merged = {i: dict(d) for i, d in log_items.items()}
    for e in log:
        merged.setdefault(e["id"], {}).setdefault(e["t"], e)
    log += gg.close_events(inp, record.fold(inp.events + new), merged, by_game, cfg, now)
    return new, {"ranks": ranks, "live": live, "log": log, "blocked": found["blocked"], "considered": found["considered"]}


def _pull(i, at, code, text, sh, it, status=None, yours=None, now_price=None):
    e = {"t": "pulled", "id": i, "at": at, "code": code, "reason": text}
    if sh.get("era"):
        e["if_bet"] = gg.if_bet_text(code, it["prices"][0]["price"], now_price, yours, status=status)
        if now_price is not None:
            e["now_price"] = now_price
        if yours and yours.get("yours_ev") is not None:
            e["yours_ev"] = round(yours["yours_ev"], 4)
    return e


def _judge_read(inp, g, f, sh, it, i, at, t):
    """A newer R / C read for a pick that is alive: a pulled event, or None."""
    cfg = inp.cfg
    mname = sh["mkey"]
    pairs = gg.hr_market(f, mname, cfg)
    if pairs is None:
        hr_any = any(mk.get("outcomes") for mk in (f.get("props") or {}).values())
        if hr_any:
            return _pull(i, at, "market_gone", pull_reason("market_gone"), sh, it)
        return None                                         # Hard Rock sent no props on this read: a feed gap, not news
    pair = pairs.get((sh["player_id"], sh["line"]))
    side = sh["side"]
    if pair is None or pair.get(side) is None:
        moved = sorted(l for (pid, l), p in pairs.items()
                       if pid == sh["player_id"] and l != sh["line"] and p.get(side) is not None)
        if moved:
            return _pull(i, at, "line_moved", pull_reason("line_moved", line=moved[0]), sh, it)
        return _pull(i, at, "line_gone", pull_reason("line_gone"), sh, it)
    if pair["why"] is not None or pair.get("over") is None or pair.get("under") is None:
        return None                                         # a stale / odd Hard Rock read says nothing
    entry = (gg.other_lines(f, mname, cfg) or {}).get((sh["player_id"], sh["line"]))
    mm = inp.model(sh["player_id"], sh["team"], sh["game_id"], mname, sh["line"])
    fn = (lambda: model_side(inp, mm, side, mname)) if mm is not None else (lambda: None)
    frozen = it["prices"][0]["price"]
    reason, a = gg.judge_alive(side, sh["line"], pair[side], frozen, entry, fn, t)
    if not reason:
        return None
    yours = gg.at_your_price(side, sh["line"], frozen, entry, fn, t)
    return _pull(i, at, reason[0], reason[1], sh, it, yours=yours, now_price=pair[side])


def _shown(inp, a, rank, at):
    cfg, v = inp.cfg, inp.v
    g, f, mm = a["game"], a["read"], a["mm"]
    full, short = inp.name_parts(a["pid"], a["name"])
    mname = a["mname"]
    market, period = contract_market(cfg, mname)
    price = a["price"]
    reason = reason_text(mm["why"], v.get("reason_max_chars", 120), a["side"]) or \
        "Hard Rock's price beats what %d other books say is fair." % a["n_books"]
    opp = g["away"] if a["team"] == g["home"] else g["home"]
    body = {
        "id": a["id"], "era": gg.era(cfg), "rank": rank, "game_id": g["id"], "player": full, "player_short": short,
        "feed_name": a["name"], "player_id": a["pid"], "pos": inp.pos(a["pid"]), "team": a["team"], "opp": opp,
        "home": a["team"] == g["home"], "start": g["start"], "mkey": mname, "market": market, "period": period,
        "market_label": a["market_label"], "line": a["line"], "side": a["side"],
        "bet": bet_text(a["side"], a["line"], mname, cfg), "book": "Hard Rock", "price": price,
        "price_at": f["at"], "read_at": f["at"], "read_pull": f["pull"],
        "p": round(a["final"], 4), "breakeven": round(odds.break_even(price), 4), "fair": round(a["market_fair"], 4),
        "p_pct": pct_floor(a["final"]), "breakeven_pct": pct_round(odds.break_even(price)),
        "market_fair": round(a["market_fair"], 5), "n_books": a["n_books"], "books": a["books"],
        "pin_fair": round(a["pin_fair"], 5), "pin_ev": round(a["pin_ev"], 5), "gap_ev": round(a["gap_ev"], 5),
        "model_side": round(a["model_side"], 5), "final": round(a["final"], 5), "final_ev": round(a["final_ev"], 5),
        "guard": "pass", "reason": reason, "state": a["settled"], "thin": bool(mm["thin"]), "mu": round(mm["mu"], 2),
        "games_used": mm["n"], "games_this_season": mm["n_season"], "flags": mm["flags"], "lines_from": mm["ctx_source"],
    }
    return {"t": "shown", "kind": "single", "id": a["id"], "at": at, "slate": inp.slate, "pick": body}


def explain(inp, by_game):
    return gg.explain_rows(inp, SELF, by_game)
