#!/usr/bin/env python3
"""model.py  Prop Check NFL probability-of-over estimator (stdlib only).

One sentence: "his expected chances tonight (targets, carries or pass attempts, recent games count most,
pulled a little toward his position's average), times how much he does with each chance (pulled
harder toward the position average), nudged for the opponent and the betting total, turned into a
chance of clearing the number."

Everything uses ONLY games before the game being predicted: callers pass prior rows; rows dated on or after
ctx['date'] are dropped anyway as a safety net.

ROWS: per-player-game dicts from nflrows.load() (keys: gid, date, season, team, pa pc py ptd pi ra ry rt tg rc
      rcy rct lr lu otd and the same with 'h_' for the 1st half). Any order.

API
  prob_over(rows, market, period, line, ctx) -> dict(mu, p_raw, p, n, n_season, thin, why_parts, parts)
        P(under) = 1 - p (lines are x.5; for a whole-number line the push chance is ignored)
  expect(rows, market, period, ctx)          -> dict(mu, parts...)   expected value only
  p_from_mu(market, period, mu, line)        -> uncalibrated P(over) from an expected value
  opp_table(team_rows, date, season)         -> {team: {key: (factor, rank)}} defense allowed, shrunk;
                                                rank 1 = allows the most
  implied_total(game_total, spread_for_team) -> team points implied by the betting total and spread
  break_even(american)                       -> probability needed to break even
  pick_ok(flags, side)                       -> False when an eligibility flag blocks 'over' / 'under'
  role_ok(rows, market, ctx)                 -> True when he is a normal prop subject (books' role filter)

MARKETS  FG: pass_yds pass_att pass_cmp pass_td pass_int rush_yds rush_att rec rec_yds rush_rec_yds pass_rush_yds
             anytime_td long_rec long_rush
         1H: pass_yds rush_yds rec_yds anytime_td rec  (every market technically runs for 1H; only these are fitted
             and backtested; rec 1H is for Research only, Hard Rock has no market)
PERIOD   'FG' (whole game incl. OT) or '1H' (quarters 1-2)

CTX FIELDS (all optional except pos)
  pos          'QB' | 'RB' | 'WR' | 'TE'   (FB counts as RB)
  season       int, tonight's season (default: latest season in rows)
  date         'YYYY-MM-DD' tonight; rows on/after it are ignored
  team_total   implied team points from the Hard Rock game total and spread (implied_total()); None = neutral
  spread       team's expected margin in points, + = his team favored (Hard Rock spread -3.5 -> +3.5); None = 0
  opp          {key: factor} for tonight's defense from opp_table()[opp_team] (factor or (factor, rank)); missing = 1.0
  opp_name     'DEN' etc. for the reason text
  games_with_team int  games he has played for his current team (thin flag)
  missed_last  int  team games missed right before tonight (injury return flag)
  qb_changed   bool his team's starting QB tonight is not last game's starter (eligibility flag)
"""
import json
import math
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS_PATH = os.path.join(_HERE, "model_params.json")

# ---------------------------------------------------------------- market specs
# opp: opportunity column. eff: (made column, per opportunity column). allow: defense key ('POS' -> player pos).
SPEC = {
    "pass_att": dict(kind="count", opp="pa", eff=None, allow="pa"),
    "pass_cmp": dict(kind="count", opp="pa", eff="pc", allow="rc"),
    "pass_yds": dict(kind="yards", opp="pa", eff="py", allow="py"),
    "pass_td": dict(kind="count", opp="pa", eff="ptd", allow="td_rec"),
    "pass_int": dict(kind="count", opp="pa", eff="pi", allow=None),
    "rush_att": dict(kind="count", opp="ra", eff=None, allow="ra_POS"),
    "rush_yds": dict(kind="yards", opp="ra", eff="ry", allow="ry_POS"),
    "rec": dict(kind="count", opp="tg", eff="rc", allow="rc_POS"),
    "rec_yds": dict(kind="yards", opp="tg", eff="rcy", allow="rcy_POS"),
    "rush_td": dict(kind="part", opp="ra", eff="rt", allow="td_rush"),
    "rec_td": dict(kind="part", opp="tg", eff="rct", allow="td_rec"),
    "rush_rec_yds": dict(kind="yards", sum=("rush_yds", "rec_yds")),
    "pass_rush_yds": dict(kind="yards", sum=("pass_yds", "rush_yds")),
    "anytime_td": dict(kind="td", sum=("rush_td", "rec_td")),
    "long_rec": dict(kind="long", stat="lr", opp="tg", allow="rcy_POS"),
    "long_rush": dict(kind="long", stat="lu", opp="ra", allow="ry_POS"),
}
ACTUAL = {  # market -> columns summed for the actual value
    "pass_att": ("pa",), "pass_cmp": ("pc",), "pass_yds": ("py",), "pass_td": ("ptd",), "pass_int": ("pi",),
    "rush_att": ("ra",), "rush_yds": ("ry",), "rec": ("rc",), "rec_yds": ("rcy",), "rush_td": ("rt",),
    "rec_td": ("rct",), "rush_rec_yds": ("ry", "rcy"), "pass_rush_yds": ("py", "ry"), "anytime_td": ("rt", "rct", "otd"),
    "long_rec": ("lr",), "long_rush": ("lu",),
}
NOUN = {"pass_att": "attempts", "pass_cmp": "completions", "pass_yds": "passing yards", "pass_td": "TD passes",
        "pass_int": "INTs", "rush_att": "carries", "rush_yds": "rushing yards", "rec": "catches",
        "rec_yds": "receiving yards", "rush_rec_yds": "rush+rec yards", "pass_rush_yds": "pass+rush yards",
        "anytime_td": "TDs", "long_rec": "yards on his longest catch", "long_rush": "yards on his longest run"}
ALLOW_NOUN = {"pa": "pass attempts", "rc": "completions", "py": "passing yards", "td_rec": "TD passes",
              "rc_WR": "catches to WRs", "rc_TE": "catches to TEs", "rc_RB": "catches to RBs",
              "rcy_WR": "receiving yards to WRs", "rcy_TE": "receiving yards to TEs", "rcy_RB": "receiving yards to RBs",
              "ry_RB": "rushing yards to RBs", "ra_RB": "carries to RBs", "ry_QB": "QB rushing yards",
              "ra_QB": "QB carries", "td_rush": "rushing TDs", "ry_WR": "rushing yards", "ra_WR": "carries"}

# ---------------------------------------------------------------- default parameters (overwritten by model_params.json)
DEFAULT = {
    "hist": {"H_opp": 4.0, "g": 0.6, "K_opp": 1.0, "H_eff": 16.0},
    "prior_opp": {}, "prior_eff": {}, "K_eff": {}, "lg": {}, "beta": {}, "disp": {}, "ratioq": {}, "calib": {},
    "team": {"H": 16.0, "g": 0.4, "K": 4.0},
    "lg_team_total": 22.5,
    "thin": {"n": 4, "games_with_team": 3},
}


def _load_params():
    try:
        with open(PARAMS_PATH) as f:
            p = json.load(f)
        out = dict(DEFAULT)
        out.update(p)
        return out
    except (OSError, ValueError):
        return dict(DEFAULT)


PARAMS = _load_params()


# ---------------------------------------------------------------- small helpers
def break_even(american):
    """-200 -> 0.6667, +120 -> 0.4545"""
    return (-american) / (-american + 100.0) if american < 0 else 100.0 / (american + 100.0)


def implied_total(game_total, spread_for_team):
    """Team points implied by the game total and his team's expected margin (+ = favored)."""
    if game_total is None:
        return None
    return game_total / 2.0 + (spread_for_team or 0.0) / 2.0


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sig(x):
    return 1.0 / (1.0 + math.exp(-x))


def _pkey(period):
    return "h_" if period == "1H" else ""


def _pos(ctx):
    p = (ctx or {}).get("pos", "WR")
    return "RB" if p in ("FB", "HB") else p


def _prior_rows(rows, ctx):
    d = (ctx or {}).get("date")
    rs = [r for r in rows if not d or r["date"] < d]
    season = (ctx or {}).get("season") or (max(r["season"] for r in rs) if rs else 0)
    rs = [r for r in rs if r["season"] >= season - 1]
    rs.sort(key=lambda r: (r["date"], r["gid"]), reverse=True)
    return rs, season


def actual(row, market, period="FG"):
    pre = _pkey(period)
    if market in ("long_rec", "long_rush"):
        return row[pre + ACTUAL[market][0]]
    return sum(row.get(pre + c, 0) for c in ACTUAL[market])


# ---------------------------------------------------------------- distributions
def nb_sf(k, mu, r=None):
    """P(X >= k) for X ~ NegBin(mean mu, size r); Poisson when r is None."""
    if k <= 0:
        return 1.0
    if mu <= 0:
        return 0.0
    cdf, k = 0.0, int(k)
    if r is None:
        term = math.exp(-mu)
        for i in range(k):
            cdf += term
            term *= mu / (i + 1)
    else:
        p = r / (r + mu)
        term = p ** r
        for i in range(k):
            cdf += term
            term *= (i + r) / (i + 1) * (1 - p)
    return min(1.0, max(0.0, 1.0 - cdf))


def binom_sf(k, mu, n):
    """P(X >= k) for X ~ Binomial(n, mu/n) (under-dispersed counts like pass attempts)."""
    n = max(int(round(n)), int(math.ceil(mu)) + 1)
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    q = mu / n
    cdf = 0.0
    lp, lq = math.log(q), math.log(1 - q)
    for i in range(int(k)):
        cdf += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(1.0, max(0.0, 1.0 - cdf))


def count_sf(k, mu, kdisp):
    """var = mu + kdisp*mu^2. kdisp > 0 negative binomial, < 0 binomial, ~0 Poisson."""
    if abs(kdisp) < 1e-4:
        return nb_sf(k, mu)
    if kdisp > 0:
        return nb_sf(k, mu, 1.0 / kdisp)
    return binom_sf(k, mu, -1.0 / kdisp)


def _ratio_sf(qtab, x):
    """P(R > x) from a table of equally spaced quantiles of R = actual / expected."""
    n = len(qtab) - 1
    if x < qtab[0]:
        return 1.0
    if x >= qtab[-1]:
        return 0.0
    # last index with q <= x
    lo, hi = 0, n
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if qtab[mid] <= x:
            lo = mid
        else:
            hi = mid
    # handle flat runs (point masses, e.g. zero yards): CDF jumps to the end of the run
    j = lo
    while j + 1 <= n and qtab[j + 1] <= x:
        j += 1
    if j >= n:
        return 0.0
    a, b = qtab[j], qtab[j + 1]
    frac = (x - a) / (b - a) if b > a else 1.0
    return 1.0 - (j + frac) / n


def p_from_mu(market, period, mu, line, P=None):
    """Uncalibrated P(actual > line) given expected value mu."""
    P = P or PARAMS
    kind = SPEC[market]["kind"]
    key = market + "|" + period
    if mu <= 0:
        return 0.0 if line >= 0 else 1.0
    if kind == "td":
        return 1.0 - math.exp(-mu)
    if kind == "count" or kind == "part":
        return count_sf(math.floor(line) + 1, mu, P["disp"].get(key, 0.0))
    # yards / long: empirical ratio tables by expected-value bin, interpolated in log(mu)
    tabs = P["ratioq"].get(key)
    if not tabs:  # fallback: gamma-ish via lognormal sd 0.6
        z = (math.log(max(line, 0.5)) - math.log(mu) + 0.18) / 0.6
        return 0.5 * math.erfc(z / math.sqrt(2))
    centers = [t[0] for t in tabs]
    x = line / mu
    if mu <= centers[0]:
        return _ratio_sf(tabs[0][1], x)
    if mu >= centers[-1]:
        return _ratio_sf(tabs[-1][1], x)
    for i in range(len(centers) - 1):
        if centers[i] <= mu <= centers[i + 1]:
            w = (math.log(mu) - math.log(centers[i])) / (math.log(centers[i + 1]) - math.log(centers[i]))
            return (1 - w) * _ratio_sf(tabs[i][1], x) + w * _ratio_sf(tabs[i + 1][1], x)
    return _ratio_sf(tabs[-1][1], x)


def calibrate(p_raw, market, period, P=None):
    """p = sigmoid(a + bo*max(x,0) + bu*min(x,0)), x = logit(p_raw). Fit on out-of-sample predictions, so the
    over side and the under side are shrunk separately. Two-value entries (a, b) are plain logistic."""
    P = P or PARAMS
    c = P["calib"].get(market + "|" + period, (0.0, 1.0))
    x = _logit(p_raw)
    if len(c) == 2:
        return _sig(c[0] + c[1] * x)
    return _sig(c[0] + c[1] * max(x, 0.0) + c[2] * min(x, 0.0))


# ---------------------------------------------------------------- expected value
def _wsum(rows, season, cols, H, g, pre):
    """Exponentially weighted sums over prior games (newest first). Returns (sum w, {col: sum w*x})."""
    w = 0.0
    s = dict.fromkeys(cols, 0.0)
    for i, r in enumerate(rows):
        wi = (1.0 if r["season"] == season else g) * 0.5 ** (i / H)
        w += wi
        for c in cols:
            s[c] += wi * r[pre + c]
    return w, s


def _ctx_factor(market, period, pos, ctx, P):
    """Opponent and game-environment multiplier for a base market. Returns (factor, opp_f)."""
    spec = SPEC[market]
    b = P["beta"].get(market + "|" + period, {})
    f = 1.0
    opp_f = 1.0
    allow = spec.get("allow")
    if allow:
        allow = allow.replace("POS", pos)
        o = (ctx.get("opp") or {}).get(allow)
        if o is not None:
            opp_f = o[0] if isinstance(o, (tuple, list)) else o
            f *= opp_f ** b.get("opp", 0.0)
    tt = ctx.get("team_total")
    if tt:
        f *= (tt / P["lg_team_total"]) ** b.get("tt", 0.0)
    sp = ctx.get("spread")
    if sp:
        f *= math.exp(b.get("sp", 0.0) * max(-14.0, min(14.0, sp)))
    return f * b.get("scale", 1.0), opp_f


def _base(rows, season, market, period, pos, ctx, P):
    spec = SPEC[market]
    pre = _pkey(period)
    h = P["hist"]
    oc = spec["opp"]
    ok = "%s|%s|%s" % (oc, pos, period)
    w, s = _wsum(rows, season, [oc], h["H_opp"], h["g"], pre)
    prior_o = P["prior_opp"].get(ok, 0.0)
    e_opp = (h["K_opp"] * prior_o + s[oc]) / (h["K_opp"] + w) if (w + h["K_opp"]) > 0 else 0.0
    out = dict(e_opp=e_opp, opp_col=oc)
    if spec["kind"] == "long":
        st = spec["stat"]
        lk = "%s|%s|%s" % (st, pos, period)
        w2, s2 = _wsum(rows, season, [st, oc], h["H_eff"], h["g"], pre)
        # longest per opportunity-adjusted game: scale history average by tonight's expected chances
        Ke = P["K_eff"].get(lk, 4.0)
        avg = (Ke * P["prior_eff"].get(lk, 10.0) + s2[st]) / (Ke + w2)
        hist_opp = (Ke * prior_o + s2[oc]) / (Ke + w2) if prior_o or s2[oc] else 0.0
        ratio = (e_opp / hist_opp) ** 0.5 if hist_opp > 0 else 1.0
        out.update(mu0=avg * ratio, eff=None)
        return out
    if spec["eff"]:
        ec = spec["eff"]
        ek = "%s|%s|%s" % (ec, pos, period)
        w2, s2 = _wsum(rows, season, [ec, oc], h["H_eff"], h["g"], pre)
        Ke = P["K_eff"].get(ek, 50.0)
        pe = P["prior_eff"].get(ek, 0.0)
        eff = (Ke * pe + s2[ec]) / (Ke + s2[oc])
        out.update(mu0=e_opp * eff, eff=eff)
    else:
        out.update(mu0=e_opp, eff=None)
    return out


def expect(rows, market, period="FG", ctx=None, P=None):
    """Expected value tonight plus parts used by the reason text."""
    P = P or PARAMS
    ctx = ctx or {}
    pos = _pos(ctx)
    rs, season = _prior_rows(rows, ctx)
    spec = SPEC[market]
    if "sum" in spec:
        parts = [expect(rs, m, period, dict(ctx, season=season), P) for m in spec["sum"]]
        return dict(mu=sum(p["mu"] for p in parts), comps=parts, n=len(rs))
    b = _base(rs, season, market, period, pos, ctx, P)
    f, opp_f = _ctx_factor(market, period, pos, ctx, P)
    mu = b["mu0"] * f
    return dict(mu=mu, mu0=b["mu0"], e_opp=b["e_opp"], eff=b["eff"], factor=f, opp_f=opp_f, n=len(rs))


def role_ok(rows, market, ctx):
    """Books' prop-subject filter: starting QB, RB with 10+ carries, WR/TE with 4+ targets (expected, FG)."""
    pos = _pos(ctx)
    rs, season = _prior_rows(rows, ctx)
    if not rs:
        return False
    h = PARAMS["hist"]
    def eo(col):
        w, s = _wsum(rs, season, [col], h["H_opp"], h["g"], "")
        return s[col] / w if w else 0.0
    if pos == "QB":
        return eo("pa") >= 20
    if pos == "RB":
        return eo("ra") >= 10 or (market in ("rec", "rec_yds", "long_rec") and eo("tg") >= 4)
    return eo("tg") >= 4


# ---------------------------------------------------------------- probability
def prob_over(rows, market, period, line, ctx=None, P=None):
    """Chance he goes OVER `line` in `market` for `period` ('FG' or '1H')."""
    P = P or PARAMS
    ctx = ctx or {}
    rs, season = _prior_rows(rows, ctx)
    ctx = dict(ctx, season=season)
    e = expect(rs, market, period, ctx, P)
    mu = e["mu"]
    if SPEC[market]["kind"] == "td":
        p_raw = 1.0 - math.exp(-mu)
    else:
        p_raw = p_from_mu(market, period, mu, line, P)
    p = calibrate(p_raw, market, period, P)
    n = len(rs)
    cv = role_cv(rs, market, ctx)
    n_season = sum(1 for r in rs if r["season"] == season)
    th = P["thin"]
    thin = (n < th["n"] or ctx.get("games_with_team", th["games_with_team"]) < th["games_with_team"]
            or ctx.get("missed_last", 0) >= 2)
    flags = eligibility(n, cv, ctx, P, role_jump(rs, market, ctx))
    return dict(mu=mu, p_raw=p_raw, p=p, n=n, n_season=n_season, thin=thin, role_cv=cv, flags=flags,
                why_parts=why(rs, market, period, line, ctx, e), parts=e)


ROLE_COL = {"QB": "pa", "RB": "ra"}


def role_cv(rs, market, ctx):
    """Spread of his chances (targets for receivers, carries for RBs, attempts for QBs) over his last 6 games:
    std / mean. None with fewer than 4 games. Above ELIG['max_role_cv'] the role is too unstable for an over."""
    pos = _pos(ctx)
    col = SPEC.get(market, {}).get("opp") or ("tg" if market in ("rush_rec_yds",) else ROLE_COL.get(pos, "tg"))
    if market in ("anytime_td", "rush_rec_yds", "pass_rush_yds"):
        col = ROLE_COL.get(pos, "tg")
    # partial games (left early, snap share under 30%) say nothing about his role: skip them
    v = [r[col] for r in rs if r.get("snap") is None or r["snap"] >= 0.3][:6]
    if len(v) < 4 or sum(v) <= 0:
        return None
    mu = sum(v) / len(v)
    return (sum((x - mu) ** 2 for x in v) / (len(v) - 1)) ** 0.5 / mu


def role_jump(rs, market, ctx):
    """Last game's chances vs the 4 before it, as a fraction (+0.5 = 50% more). 0.0 with < 3 games."""
    col = ROLE_COL.get(_pos(ctx), "tg") if market in ("anytime_td", "rush_rec_yds", "pass_rush_yds") \
        else SPEC[market].get("opp", "tg")
    if len(rs) < 3:
        return 0.0
    prev = [r[col] for r in rs[1:5]]
    base = sum(prev) / len(prev)
    return (rs[0][col] - base) / max(base, 1.0)


def eligibility(n, cv, ctx, P=None, jump=0.0):
    """Reasons this leg should not be picked (empty = fine). See REPORT.md for the evidence.
      few_games      < 6 prior games (this season + last): no pick either side
      short_history  < 8 prior games: no OVER
      injury_return  missed 2+ team games right before tonight: no OVER
      new_team       < 3 games with his current team: no OVER
      qb_change      his team's starting QB tonight is not last game's starter: no OVER
      role_unstable  chances std/mean over last 6 full games >= 0.60: no OVER
      role_jump      last game's chances moved 40%+ vs the 4 before: soft, add ELIG['jump_penalty'] to an OVER's shrink
    Unders are only blocked by few_games: flagged unders hit MORE often than stated."""
    e = (P or PARAMS).get("elig", ELIG)
    out = []
    if n < e["min_games"]:
        out.append("few_games")
    if n < e["min_games_over"]:
        out.append("short_history")
    if ctx.get("missed_last", 0) >= e["max_missed"]:
        out.append("injury_return")
    if ctx.get("games_with_team", 99) < e["min_games_with_team"]:
        out.append("new_team")
    if ctx.get("qb_changed"):
        out.append("qb_change")
    if cv is not None and cv >= e["max_role_cv"]:
        out.append("role_unstable")
    if abs(jump) >= e["jump"]:
        out.append("role_jump")
    return out


OVER_BLOCK = ("few_games", "short_history", "injury_return", "new_team", "qb_change", "role_unstable")
UNDER_BLOCK = ("few_games",)


def pick_ok(flags, side):
    """True when no flag blocks this side ('over' / 'under'). role_jump never blocks (soft)."""
    block = OVER_BLOCK if side == "over" else UNDER_BLOCK
    return not any(f in block for f in flags)


ELIG = {"min_games": 6, "min_games_over": 8, "max_missed": 2, "min_games_with_team": 3, "max_role_cv": 0.60,
        "jump": 0.40, "jump_penalty": 0.03}


def why(rs, market, period, line, ctx, e=None):
    """Plain reason pieces, e.g. ['6.8 catches a game his last 5', 'DEN allows the 4th most catches to WRs']."""
    pos = _pos(ctx)
    last = rs[:5]
    out = []
    half = " in the 1st half" if period == "1H" else ""
    if last:
        vals = [actual(r, market, period) for r in last]
        if market == "anytime_td":
            k = sum(1 for v in vals if v > 0)
            out.append("scored%s in %d of his last %d" % (half, k, len(vals)))
        else:
            avg = sum(vals) / len(vals)
            over = sum(1 for v in vals if v > line)
            fmt = "%.1f" if avg < 20 else "%.0f"
            out.append(("%s %s a game%s his last %d (%d of %d over %s)" %
                        (fmt % avg, NOUN[market], half, len(vals), over, len(vals), _fmt_line(line))))
    else:
        out.append("no games yet this season or last")
    spec = SPEC[market]
    base = spec["sum"][-1] if "sum" in spec else market
    allow = SPEC[base].get("allow")
    opp = ctx.get("opp") or {}
    if allow and ctx.get("opp_name"):
        k = allow.replace("POS", pos)
        o = opp.get(k)
        raw = (ctx.get("opp_raw") or {}).get(k)                 # same numbers Research shows, when given
        if raw:
            o = (None, raw)
        if isinstance(o, (tuple, list)) and len(o) > 1 and o[1]:
            rank = o[1]
            nm = ALLOW_NOUN.get(k, k)
            nteams = max(len(opp) and 32, 32)
            if rank <= 8:
                out.append("%s allows the %smost %s" % (ctx["opp_name"], "" if rank == 1 else _ord(rank) + " ", nm))
            elif rank >= nteams - 7:
                k = nteams + 1 - rank
                out.append("%s allows the %sfewest %s" % (ctx["opp_name"], "" if k == 1 else _ord(k) + " ", nm))
    tt = ctx.get("team_total")
    if tt and abs(tt - PARAMS["lg_team_total"]) >= 3:
        out.append("his team is projected for %.1f points" % tt)
    if ctx.get("missed_last", 0) >= 1:
        out.append("back after missing %d game%s" % (ctx["missed_last"], "s" if ctx["missed_last"] > 1 else ""))
    return out


def _fmt_line(x):
    return ("%.1f" % x).rstrip("0").rstrip(".")


def _ord(n):
    return "%d%s" % (n, "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


# ---------------------------------------------------------------- defense table
TEAM_KEYS = ["pa", "rc", "py", "td_rec", "td_rush", "rc_WR", "rc_TE", "rc_RB", "rcy_WR", "rcy_TE", "rcy_RB",
             "ry_RB", "ra_RB", "ry", "ra"]



def raw_ranks(team_rows, date, season, period="FG"):
    """Plain this-season ranks, the same numbers Research shows: per-game average each defense allowed in
    regular-season games before `date`. Returns {defense: {key: rank}} with rank 1 = MOST allowed
    (Research's 'rank' is 1 = fewest; rank_most = teams + 1 - rank_fewest, ties broken by team code)."""
    pre = _pkey(period)
    rs = [t for t in team_rows if t["date"] < date and t["season"] == season and t.get("stype", "REG") == "REG"]
    if not rs:
        return {}
    rs = [dict(t, **{p + "rc": t[p + "rc_WR"] + t[p + "rc_TE"] + t[p + "rc_RB"] for p in ("", "h_")}) for t in rs]
    by = {}
    for t in rs:
        by.setdefault(t["opp"], []).append(t)
    out = {d: {} for d in by}
    for k in TEAM_KEYS:
        avg = {d: sum(t[pre + k] for t in lst) / float(len(lst)) for d, lst in by.items()}
        order = sorted(avg, key=lambda d: (avg[d], d))          # fewest first, as Research
        n = len(order)
        for i, d in enumerate(order):
            out[d][k] = n - i
    return out

def opp_table(team_rows, date, season, period="FG", P=None):
    """Shrunk 'allowed' factors for every defense from team-game rows (nflrows) before `date`.
    team_rows are OFFENSE rows; a defense's allowed = rows where row['opp'] == defense.
    Returns {defense: {key: (factor, rank)}} with QB rushing mapped as ry_QB/ra_QB -> team ry/ra."""
    P = P or PARAMS
    tp = P["team"]
    pre = _pkey(period)
    rs = [t for t in team_rows if t["date"] < date and t["season"] >= season - 1 and t.get("stype", "REG") in ("REG", "POST")]
    if not rs:
        return {}
    rs = [dict(t, **{p + "rc": t[p + "rc_WR"] + t[p + "rc_TE"] + t[p + "rc_RB"] for p in ("", "h_")}) for t in rs]
    cur = [t for t in rs if t["season"] == season] or rs
    lg = {k: sum(t[pre + k] for t in cur) / len(cur) for k in TEAM_KEYS}
    by = {}
    for t in rs:
        by.setdefault(t["opp"], []).append(t)
    out = {}
    for d, lst in by.items():
        lst.sort(key=lambda t: t["date"], reverse=True)
        w = 0.0
        s = dict.fromkeys(TEAM_KEYS, 0.0)
        for i, t in enumerate(lst):
            wi = (1.0 if t["season"] == season else tp["g"]) * 0.5 ** (i / tp["H"])
            w += wi
            for k in TEAM_KEYS:
                s[k] += wi * t[pre + k]
        out[d] = {k: ((tp["K"] * lg[k] + s[k]) / (tp["K"] + w) / lg[k] if lg[k] else 1.0) for k in TEAM_KEYS}
    for k in TEAM_KEYS:
        order = sorted(out, key=lambda d: -out[d][k])
        for i, d in enumerate(order):
            out[d][k] = (out[d][k], i + 1)
    for d in out:
        out[d]["ry_QB"] = out[d]["ry"]
        out[d]["ra_QB"] = out[d]["ra"]
        out[d]["ry_WR"] = out[d]["ry"]
        out[d]["ra_WR"] = out[d]["ra"]
        out[d]["ry_TE"] = out[d]["ry"]
        out[d]["ra_TE"] = out[d]["ra"]
    return out
