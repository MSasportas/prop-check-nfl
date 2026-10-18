"""record.py: THE RECORD.

data/record/events.jsonl is the source of truth: one JSON object per line, only ever added at the end.
  shown    a pick (or parlay) exactly as first shown on the page
  price    a new price seen for a live pick (with the chance and break-even shown with it)
  update   the shown chance or reason changed without a new price
  state    how settled it is: early -> injury_report -> inactives
  pulled   with the reason shown
  locked   kickoff
  graded   result, what happened, the graded price (worst price shown) and units

data/record.json and data/record/<slate>.json (older weeks) are views rebuilt from the log (`run.py --rebuild-record`
does exactly that and nothing else). They contain no fact that is not in the log.

Units: 1 unit staked per pick and per parlay. Win = decimal(worst price shown) - 1, loss = -1,
push / void = 0. A parlay drops void and push legs and is graded on the rest.
"""
import json
import os

import common
import odds

EVENT_TYPES = ("shown", "price", "update", "state", "pulled", "locked", "graded")


class RecordError(Exception):
    pass


def events_path():
    return common.dpath("record", "events.jsonl")


def read_raw():
    try:
        with open(events_path(), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b""


def parse(raw):
    """bytes -> list of events. A line that cannot be read stops everything: the Record is never guessed."""
    out = []
    if raw and not raw.endswith(b"\n"):
        raise RecordError("events.jsonl does not end with a newline (a write was cut short)")
    for i, line in enumerate(raw.decode("utf-8").split("\n")):
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            raise RecordError("events.jsonl line %d is not valid JSON" % (i + 1))
        if not isinstance(e, dict) or e.get("t") not in EVENT_TYPES or "id" not in e or "at" not in e:
            raise RecordError("events.jsonl line %d is not a Record event" % (i + 1))
        out.append(e)
    return out


def read_events():
    return parse(read_raw())


def append(events):
    """Add events at the end. The old bytes are written back untouched, then the new lines."""
    if not events:
        return 0
    raw = read_raw()
    parse(raw)                                              # refuse to build on a damaged log
    lines = []
    for e in events:
        if e.get("t") not in EVENT_TYPES or "id" not in e or "at" not in e:
            raise RecordError("refusing to write a malformed event")
        lines.append(json.dumps(e, separators=(",", ":"), ensure_ascii=True, sort_keys=True))
    common.write_bytes(events_path(), raw + ("\n".join(lines) + "\n").encode("utf-8"))
    return len(lines)


# ---------------------------------------------------------------- folding the log
def fold(events):
    """Replay the log. Returns {id: item}. An item is everything known about one pick or parlay."""
    items = {}
    for e in events:
        t, i = e["t"], e["id"]
        if t == "shown":
            if i in items:
                continue                                    # a second "shown" for the same id is ignored
            body = e.get("pick") or e.get("parlay") or {}
            items[i] = {"id": i, "kind": e.get("kind", "single"), "slate": e.get("slate"), "shown_at": e["at"],
                        "shown": body,
                        "prices": [{"at": body.get("price_at") or e["at"], "price": body.get("price"),
                                    "legs": body.get("leg_prices")}],
                        "p_pct": body.get("p_pct"), "breakeven_pct": body.get("breakeven_pct"),
                        "p": body.get("p"), "breakeven": body.get("breakeven"), "reason": body.get("reason"),
                        "fair": body.get("fair"),
                        "state": body.get("state"), "states": [{"at": e["at"], "state": body.get("state")}],
                        "pulled": None, "locked_at": None, "graded": None}
            continue
        it = items.get(i)
        if it is None:
            continue
        if t == "price":
            it["prices"].append({"at": e.get("price_at") or e["at"], "price": e["price"], "legs": e.get("leg_prices")})
            for k in ("p", "breakeven", "reason", "fair", "p_pct", "breakeven_pct"):
                if e.get(k) is not None:
                    it[k] = e[k]
        elif t == "update":
            for k in ("p", "breakeven", "reason", "fair", "p_pct", "breakeven_pct"):
                if e.get(k) is not None:
                    it[k] = e[k]
        elif t == "state":
            it["state"] = e["state"]
            it["states"].append({"at": e["at"], "state": e["state"]})
            if e.get("reason") is not None:
                it["reason"] = e["reason"]
        elif t == "pulled":
            if it["pulled"] is None and it["locked_at"] is None:
                it["pulled"] = {"at": e["at"], "reason": e.get("reason"), "code": e.get("code"), "legs": e.get("legs")}
        elif t == "locked":
            if it["locked_at"] is None:
                it["locked_at"] = e["at"]                   # the settledness at lock stays as it was
        elif t == "graded":
            if it["graded"] is None:
                it["graded"] = {k: e.get(k) for k in ("at", "result", "actual", "actual_text", "price", "units", "legs",
                                                      "note", "source")}
    return items


def worst_price(item):
    """The lowest-paying price ever shown for this pick."""
    return min((p["price"] for p in item["prices"] if p["price"] is not None), key=odds.decimal)


def parlay_snapshot(item):
    """The parlay price snapshot it is graded at: the WORST parlay price shown (lowest payout), with the leg
    prices it was made of. None for a parlay recorded before snapshots were stored."""
    snaps = [p for p in item["prices"] if p.get("legs") and p["price"] is not None]
    return min(snaps, key=lambda p: odds.decimal(p["price"])) if snaps else None


def first_price(item):
    return item["prices"][0]["price"]


def last_price(item):
    return item["prices"][-1]["price"]


def is_open(item):
    """Still bettable: not pulled, not locked, not graded."""
    return item["pulled"] is None and item["locked_at"] is None and item["graded"] is None


# ---------------------------------------------------------------- grading arithmetic
def single_units(result, price, stake=1):
    if result == "W":
        return round((odds.decimal(price) - 1.0) * stake, 4)
    if result == "L":
        return -float(stake)
    return 0.0


def grade_parlay(legs, stake=1):
    """legs: [(result, graded price)] for every leg. Returns (result, price, units).
    A void or push leg drops out. Any losing leg loses. No legs left = void."""
    alive = [(r, p) for r, p in legs if r in ("W", "L")]
    if any(r is None for r, _ in legs):
        return None, None, None
    if not alive:
        return "VOID", None, 0.0
    dec = 1.0
    for _, p in alive:
        dec *= odds.decimal(p)
    price = odds.american(dec)
    if any(r == "L" for r, _ in alive):
        return "L", price, -float(stake)
    return "W", price, round((odds.decimal(price) - 1.0) * stake, 4)    # paid at the price as shown (whole number)


# ---------------------------------------------------------------- views for the page (CONTRACT.md section 2)
def on_record(it):
    """An entry joins the Record when its game kicks off (locked) or when it is pulled, whichever is first."""
    return it["locked_at"] is not None or it["pulled"] is not None or it["graded"] is not None


def grade_price(it):
    if it["graded"] and it["graded"].get("price") is not None:
        return it["graded"]["price"]
    if it["kind"] == "parlay":
        snap = parlay_snapshot(it)
        return snap["price"] if snap else last_price(it)
    return worst_price(it)


def _pulled(it):
    if not it["pulled"]:
        return None
    out = {"at": it["pulled"]["at"], "reason": it["pulled"]["reason"]}
    return out


def _game_text(sh):
    if not sh.get("team"):
        return None
    away, home = (sh["opp"], sh["team"]) if sh.get("home") else (sh["team"], sh["opp"])
    return "%s at %s" % (away, home)


def _leg(it):
    sh, g = it["shown"], it["graded"] or {}
    return {"id": it["id"], "kind": "leg", "player": sh.get("player_short") or sh.get("player"), "bet": sh.get("bet"),
            "period": sh.get("period"), "price": grade_price(it), "result": g.get("result"),
            "actual": g.get("actual_text")}


def _entry(it, items):
    sh, g = it["shown"], it["graded"] or {}
    if it["kind"] == "parlay":
        legs = [items[l] for l in sh.get("legs", []) if l in items]
        dates = sorted((l["shown"].get("start") or "")[:10] for l in legs if l["shown"].get("start"))
        return {"id": it["id"], "kind": "parlay", "date": dates[0] if dates else (sh.get("start") or "")[:10] or None,
                "game": None, "player": None, "team": None, "market": "parlay", "period": None,
                "bet": "%d legs" % len(sh.get("legs", [])), "price": grade_price(it), "result": g.get("result"),
                "actual": g.get("actual_text"), "units": g.get("units"), "pulled": _pulled(it),
                "legs": [_leg(l) for l in legs]}
    return {"id": it["id"], "kind": "single", "date": (sh.get("start") or "")[:10] or None, "game": _game_text(sh),
            "player": sh.get("player_short") or sh.get("player"), "team": sh.get("team"), "market": sh.get("market"),
            "period": sh.get("period"), "bet": sh.get("bet"), "price": grade_price(it), "result": g.get("result"),
            "actual": g.get("actual_text"), "units": g.get("units"), "pulled": _pulled(it), "legs": []}


def _row(key, label, entries):
    w = sum(1 for e in entries if e["result"] == "W")
    l = sum(1 for e in entries if e["result"] == "L")
    need = [odds.break_even(e["price"]) for e in entries if e["result"] in ("W", "L") and e["price"] is not None]
    return {"key": key, "label": label, "w": w, "l": l,
            "push": sum(1 for e in entries if e["result"] == "PUSH"),
            "void": sum(1 for e in entries if e["result"] == "VOID"),
            "n": w + l, "units": round(sum(e["units"] or 0 for e in entries if e["result"]), 4),
            "hit_rate": round(w / float(w + l), 4) if (w + l) else None,
            "needed": round(sum(need) / len(need), 4) if need else None}


def _label(cfg, market, period):
    for k, m in cfg["ops"]["markets"].items():
        if (m.get("model") or k) == market and m.get("period", "FG") == period:
            lab = m["label"]
            if period == "1H":
                lab = lab.replace("1H ", "")
                return "%s (1st half)" % (lab[0].upper() + lab[1:])
            return lab
    return "%s %s" % (market, period)


def _week_no(slate):
    try:
        return int(str(slate).split("-W")[1])
    except (IndexError, ValueError):
        return None


def build_views(events, cfg):
    """-> (record.json dict, {"2026-W05": {"weeks": [week]}, ...}) per CONTRACT.md section 2."""
    items = fold(events)
    by_week = {}
    for it in items.values():
        if on_record(it):
            by_week.setdefault(it["slate"], []).append(it)
    weeks = []
    for slate in sorted(by_week, key=lambda s: (str(s)), reverse=True):
        its = sorted(by_week[slate], key=lambda x: (x["shown_at"], x["kind"] != "single", x["id"]))
        entries = [_entry(x, items) for x in its]
        counted = [e for e in entries if e["kind"] == "single" and not e["pulled"]]
        wk = _week_no(slate)
        weeks.append({"week": wk, "slate": slate, "label": "Week %d" % wk if wk else str(slate),
                      "graded": all(e["result"] is not None for e in entries),
                      "w": sum(1 for e in counted if e["result"] == "W"),
                      "l": sum(1 for e in counted if e["result"] == "L"),
                      "units": round(sum(e["units"] or 0 for e in counted), 4), "entries": entries})
    every = [e for w in weeks for e in w["entries"]]
    singles = [e for e in every if e["kind"] == "single" and not e["pulled"]]
    rows = []
    keys = []
    for e in singles:
        k = "%s|%s" % (e["market"], e["period"])
        if k not in keys:
            keys.append(k)
    for k in keys:
        sub = [e for e in singles if "%s|%s" % (e["market"], e["period"]) == k]
        if any(e["result"] for e in sub):
            m, _, per = k.partition("|")
            rows.append(_row(k, _label(cfg, m, per), sub))
    par = [e for e in every if e["kind"] == "parlay" and not e["pulled"]]
    if any(e["result"] for e in par):
        r = _row("parlays", "Parlays", par)
        r["needed"] = None
        rows.append(r)
    pulled = [e for e in every if e["pulled"]]
    if any(e["result"] for e in pulled):
        rows.append(_row("pulled", "Pulled", pulled))
    head = _row("headline", "Singles", singles)
    head.pop("key"); head.pop("label")
    pr = [e for e in par if e["result"]]
    head["parlays"] = {"w": sum(1 for e in pr if e["result"] == "W"), "l": sum(1 for e in pr if e["result"] == "L"),
                       "units": round(sum(e["units"] or 0 for e in pr), 4)}
    keep = cfg["ops"].get("record_weeks", 4)
    older = {}
    for w in weeks[keep:]:
        older[w["slate"]] = {"weeks": [w]}
    season = None
    for slate in by_week:
        try:
            season = int(str(slate)[:4])
        except ValueError:
            pass
    rec = {"generated_at": events[-1]["at"] if events else None, "season": season,
           "summary": {"headline": head, "rows": rows}, "weeks": weeks[:keep],
           "older": ["record/%s.json" % s for s in sorted(older, reverse=True)]}
    return rec, older


def write_views(cfg, events=None):
    """Rebuild record.json and the older-week files from the log. Writes only files whose content changed."""
    events = read_events() if events is None else events
    rec, older = build_views(events, cfg)
    changed = []
    targets = [(common.dpath("record.json"), rec)] + \
              [(common.dpath("record", "%s.json" % k), doc) for k, doc in older.items()]
    for path, doc in targets:
        if common.read_json(path) != doc:
            common.write_json(path, doc)
            changed.append(os.path.basename(path))
    return changed
