"""book_probe.py: ONE-TIME discovery of which books post which NFL player prop markets, two-sided (research only).

Why: Hard Rock is compared with Pinnacle and other books, no-vig, as in Prop Check NHL. Before the NFL markets and the
sharp-book list are chosen we need evidence of who actually posts what. This asks the event-odds endpoint with NO
bookmakers= filter across regions us, us2, us_ex, eu, uk for the full-game player prop MAIN markets and records every
book's answer.

Cost: markets x regions (14 x 5 = about 70 credits), in chunks of CHUNK markets (one call per chunk), hard cap CAP (90).
A market no book returns is not billed. Every call goes through the repo's own Client and key pool, so the credit
ledger (data/state/credits.json, pull "P") stays right. Never in a unit test (they mock the HTTP layer).

Game: the earliest NFL event from the free /events call that has not kicked off (override: probe/request.json
{"event_id": "..."}). A second run is refused once a result exists unless probe/request.json says {"force": true}.

Writes (nothing reads them: no pick, page or Record):
  data/state/book_probe.json         per market and per book numbers + the call's cost
  data/state/book_probe_pairs.json.gz  [book, market, player, line, over price, under price] for every pair

Never prints, writes or commits a key (common.scrub / install_scrubbers).
"""
import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import timedelta

import common
import odds

MARKETS = ["player_pass_yds", "player_pass_tds", "player_pass_attempts", "player_pass_completions",
           "player_pass_interceptions", "player_rush_yds", "player_rush_attempts", "player_receptions",
           "player_reception_yds", "player_rush_reception_yds", "player_reception_longest", "player_rush_longest",
           "player_pass_longest_completion", "player_anytime_td"]
REGIONS = ["us", "us2", "us_ex", "eu", "uk"]
CHUNK = 7
CAP = 90
PULL = "P"
HARD_ROCK = "hardrockbet_fl"
PINNACLE = "pinnacle"
MIN_OTHERS = 4
HR_FAMILY = (HARD_ROCK, "hardrockbet")      # national Hard Rock is the same operator: never counted as an "other" book
ONE_SIDED = {"player_anytime_td"}          # Yes-only market: no under to de-vig; compared on raw break-even


def path():
    return common.dpath("state", "book_probe.json")


def pairs_path():
    return common.dpath("state", "book_probe_pairs.json.gz")


def request_path():
    return os.path.join(common.ROOT, "probe", "request.json")


# ---------------------------------------------------------------- reading the feed's answer (pure)
def collect(payload, books=None):
    """One event-odds answer -> books: {book key: {"title", "markets": {market: {"last_update", "pairs": {(player, point):
    [over, under]}}}}}. Merges into `books` when given (several chunks). anytime TD: Yes goes in the over slot."""
    books = books if books is not None else {}
    for b in (payload or {}).get("bookmakers", []) or []:
        if not isinstance(b, dict) or not b.get("key"):
            continue
        rec = books.setdefault(b["key"], {"title": b.get("title"), "markets": {}})
        for m in b.get("markets", []) or []:
            if not isinstance(m, dict) or not m.get("key"):
                continue
            mk = rec["markets"].setdefault(m["key"], {"last_update": m.get("last_update"), "pairs": {}})
            for o in m.get("outcomes", []) or []:
                who, pt, price = o.get("description"), o.get("point"), o.get("price")
                side = (o.get("name") or "").lower()
                if m["key"] in ONE_SIDED:
                    side = {"yes": "over", "no": "under"}.get(side, side)
                    pt = 0.5 if pt is None else pt
                if who is None or pt is None or side not in ("over", "under") or not isinstance(price, (int, float)):
                    continue
                mk["pairs"].setdefault((who, float(pt)), [None, None])[0 if side == "over" else 1] = price
    return books


def _two(v):
    return v[0] is not None and v[1] is not None


def _fair(market, v):
    """The book's fair over chance. Two-sided: de-vigged. One-sided (anytime TD): raw break-even of the Yes price."""
    if market in ONE_SIDED and v[1] is None:
        return odds.break_even(v[0])
    return odds.fair_two_sided(v[0], v[1])


def _ok(market, v):
    """A usable pair for this market: two-sided, or a Yes price for a one-sided market."""
    return v[0] is not None if market in ONE_SIDED else _two(v)


def main_lines(pairs_by_book):
    """{player: (line, n_books)}: the line most books share (ties: the line whose books' fair over chances sit closest
    to 50%). pairs_by_book: {book: {(player, point): fair over chance}}, usable pairs only."""
    seen = defaultdict(lambda: defaultdict(list))
    for book, prs in pairs_by_book.items():
        for (who, pt), fair in prs.items():
            seen[who][pt].append(fair)
    out = {}
    for who, lines in seen.items():
        best = min(lines, key=lambda pt: (-len(lines[pt]), abs(sum(lines[pt]) / len(lines[pt]) - 0.5), pt))
        out[who] = (best, len(lines[best]))
    return out


def summarize(books, markets_asked):
    """Per market and per book numbers (pure)."""
    per_market, per_book = {}, {}
    book_gaps = defaultdict(list)
    book_signed = defaultdict(list)
    book_holds = defaultdict(list)
    book_pairs = Counter()
    book_markets = defaultdict(set)
    for mk in markets_asked:
        usable = {}                                   # book -> {(player, pt): [over, under]}
        anyp = {}                                     # book -> number of players with any price
        for k, rec in books.items():
            m = rec["markets"].get(mk)
            if not m:
                continue
            anyp[k] = len({pl[0] for pl in m["pairs"]})
            u = {pl: v for pl, v in m["pairs"].items() if _ok(mk, v)}
            if u:
                usable[k] = u
        fair = {k: {pl: _fair(mk, v) for pl, v in u.items()} for k, u in usable.items()}
        pin = fair.get(PINNACLE) or {}
        rows = {}
        for k, u in usable.items():
            holds = [odds.hold(v[0], v[1]) for v in u.values() if _two(v)]
            shared = [fair[k][pl] - pin[pl] for pl in u if pl in pin]
            rows[k] = {"title": books[k]["title"], "pairs": len(u), "players": len({pl[0] for pl in u}),
                       "hold_mean": round(sum(holds) / len(holds), 4) if holds else None,
                       "shared_with_pinnacle": len(shared),
                       "gap_abs": round(sum(abs(x) for x in shared) / len(shared), 4) if shared else None,
                       "gap_signed": round(sum(shared) / len(shared), 4) if shared else None,
                       "last_update": books[k]["markets"][mk].get("last_update")}
            book_pairs[k] += len(u)
            book_markets[k].add(mk)
            book_holds[k] += holds
            if k != PINNACLE and k != "hardrockbet":
                book_gaps[k] += [abs(x) for x in shared]
                book_signed[k] += shared
        # the number that matters: Hard Rock AND Pinnacle AND >= MIN_OTHERS other books on the SAME (player, line)
        hr, others_n = usable.get(HARD_ROCK, {}), defaultdict(int)
        for k, u in usable.items():
            if k in HR_FAMILY or k == PINNACLE:
                continue
            for pl in u:
                others_n[pl] += 1
        full = [pl for pl in hr if pl in pin and others_n[pl] >= MIN_OTHERS]
        hr_pin = [pl for pl in hr if pl in pin]
        others = [k for k in usable if k not in HR_FAMILY and k != PINNACLE]
        ml = main_lines({k: f for k, f in fair.items() if k != "hardrockbet"})
        per_market[mk] = {
            "hard_rock": {"present": HARD_ROCK in anyp, "pairs": len(hr), "players": len({pl[0] for pl in hr}),
                          "any_price_players": anyp.get(HARD_ROCK, 0)},
            "pinnacle": {"present": PINNACLE in anyp, "pairs": len(pin), "players": len({pl[0] for pl in pin})},
            "books_any_price": sorted(anyp),
            "books_two_sided": len([k for k in usable if k != "hardrockbet"]) if mk not in ONE_SIDED else None,
            "books_usable": sorted(usable),
            "basis": "yes price break-even (one-sided market)" if mk in ONE_SIDED else "two-sided, de-vigged",
            "hr_and_pinnacle_pairs": len(hr_pin),
            "hr_pin_4plus_pairs": len(full),
            "hr_pin_4plus_players": len({pl[0] for pl in full}),
            "mean_gap_abs_others_vs_pinnacle": _mean([rows[k]["gap_abs"] for k in others if rows[k]["gap_abs"] is not None],
                                                     weights=[rows[k]["shared_with_pinnacle"] for k in others
                                                              if rows[k]["gap_abs"] is not None]),
            "main_line_players": len(ml),
            "main_line_books_median": _median([n for _, n in ml.values()]),
            "main_lines": {who: {"line": ln, "books": n} for who, (ln, n) in sorted(ml.items())},
            "books": rows}
    for k in sorted(book_pairs):
        h = book_holds[k]
        per_book[k] = {"pairs": book_pairs[k], "markets": sorted(book_markets[k]),
                       "hold_mean": round(sum(h) / len(h), 4) if h else None,
                       "shared_with_pinnacle": len(book_gaps.get(k, [])),
                       "gap_abs": round(sum(book_gaps[k]) / len(book_gaps[k]), 4) if book_gaps.get(k) else None,
                       "gap_signed": round(sum(book_signed[k]) / len(book_signed[k]), 4) if book_signed.get(k) else None}
    return per_market, per_book


def _mean(xs, weights=None):
    if not xs:
        return None
    if weights and sum(weights) > 0:
        return round(sum(x * w for x, w in zip(xs, weights)) / sum(weights), 4)
    return round(sum(xs) / len(xs), 4)


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2.0, 1)


# ---------------------------------------------------------------- the calls
def pick_event(events, now):
    """Earliest feed event that has not kicked off, or None."""
    up = []
    for e in events or []:
        try:
            when = common.parse_iso(e["commence_time"])
        except (KeyError, ValueError, TypeError):
            continue
        if when > now + timedelta(minutes=5) and e.get("id"):
            up.append((when, e["id"], e))
    return min(up, key=lambda x: (x[0], x[1]))[2] if up else None


def read_chunk(client, event_id, markets, regions, game_id, spent, cap, notes):
    """One event-odds call for these markets. Returns (payload or None, cost, rejected markets). A 422/404 (the feed
    refuses a market key) is retried one market at a time so only the bad key is dropped."""
    need = len(markets) * len(regions)
    if spent + need > cap:
        notes.append("skipped %s: would pass the %d credit cap" % (",".join(markets), cap))
        return None, 0, []
    params = {"regions": ",".join(regions), "markets": ",".join(markets), "oddsFormat": "american", "dateFormat": "iso"}
    for attempt in (1, 2):                             # a call that did not answer (network) is tried once more
        before = len(client.errors)
        js, cost = client._get("/events/%s/odds" % event_id, params, need, PULL, game_id)
        if js is not None:
            break
        notes.append("no answer for %d market(s) (try %d): %s" % (len(markets), attempt, "; ".join(client.errors[before:])))
    if js is None:
        return None, 0, []
    if js == {}:                                       # refused
        if len(markets) == 1:
            return {}, 0, list(markets)
        got, total, bad = [], 0, []
        for m in markets:
            p, c, r = read_chunk(client, event_id, [m], regions, game_id, spent + total, cap, notes)
            total += c
            bad += r
            if p:
                got.append(p)
        merged = {"bookmakers": []}
        books = {}
        for p in got:
            for b in p.get("bookmakers", []):
                tgt = books.setdefault(b["key"], {"key": b["key"], "title": b.get("title"), "markets": []})
                tgt["markets"] += b.get("markets", [])
        merged["bookmakers"] = list(books.values())
        return merged, total, bad
    return js, cost, []


def run_probe(now, cfg, pool, client, request=None):
    """The whole probe. Returns the dict that goes into book_probe.json. Spends at most CAP credits."""
    request = request or {}
    cap = int(request.get("cap") or CAP)
    notes = []
    events = client.refresh_keys()                     # free; also refreshes every key's remaining credits
    pool.save()
    ev = None
    if request.get("event_id"):
        ev = next((e for e in events or [] if e.get("id") == request["event_id"]), None)
    else:
        ev = pick_event(events, now)
    if ev is None:
        return {"status": "no_game", "at": common.iso(now), "notes": ["no upcoming NFL event from the free events call"] + client.errors}
    when = common.parse_iso(ev["commence_time"])
    game_id = "%s@%s" % (ev.get("away_team"), ev.get("home_team"))
    books, cost, rejected, spent = {}, 0, [], 0
    chunks = [MARKETS[i:i + CHUNK] for i in range(0, len(MARKETS), CHUNK)]
    for ch in chunks:
        p, c, bad = read_chunk(client, ev["id"], ch, REGIONS, game_id, spent, cap, notes)
        pool.save()                                    # credits were (or may have been) spent: ledger first
        spent += c
        rejected += bad
        if p:
            collect(p, books)
    returned = sorted({mk for rec in books.values() for mk in rec["markets"]})
    asked = [m for m in MARKETS if m not in rejected]
    per_market, per_book = summarize(books, asked)
    pairs = [[k, mk, who, pt, v[0], v[1]] for k, rec in sorted(books.items())
             for mk, m in sorted(rec["markets"].items()) for (who, pt), v in sorted(m["pairs"].items())]
    out = {"status": "ok" if books else "empty", "at": common.iso(now),
           "game": {"event_id": ev["id"], "away": ev.get("away_team"), "home": ev.get("home_team"),
                    "kickoff": common.iso(when), "minutes_to_kickoff": round((when - now).total_seconds() / 60.0)},
           "regions": REGIONS, "chunks": chunks, "cap": cap, "credits_spent": spent,
           "markets_asked": MARKETS, "markets_rejected": rejected, "markets_returned": returned,
           "markets_no_book": [m for m in asked if m not in returned],
           "books_returned": sorted(books), "per_market": per_market, "per_book": per_book,
           "notes": notes + client.errors}
    return out, pairs


def write_results(out, pairs):
    common.write_json(path(), out)
    if pairs is not None:
        common.write_bytes(pairs_path(), gzip.compress(json.dumps(pairs, separators=(",", ":")).encode("utf-8"), mtime=0))


def resummarize():
    """Rebuild book_probe.json's numbers from book_probe_pairs.json.gz (no credits, no network)."""
    prev = common.read_json(path(), {}) or {}
    rows = common.read_gz_json(pairs_path(), None)
    if not rows or prev.get("status") != "ok":
        common.log("nothing to resummarize")
        return 1
    titles = {}
    for m in (prev.get("per_market") or {}).values():
        for k, b in m.get("books", {}).items():
            titles[k] = b.get("title")
    books = {}
    for k, mk, who, pt, ov, un in rows:
        rec = books.setdefault(k, {"title": titles.get(k), "markets": {}})
        rec["markets"].setdefault(mk, {"last_update": None, "pairs": {}})["pairs"][(who, pt)] = [ov, un]
    prev["per_market"], prev["per_book"] = summarize(books, [m for m in prev["markets_asked"] if m not in prev["markets_rejected"]])
    prev["resummarized"] = True
    common.write_json(path(), prev)
    common.log("resummarized %d pairs" % len(rows))
    return 0


def main():
    if "--resummarize" in sys.argv:
        return resummarize()
    common.install_scrubbers()
    now = common.now()
    cfg = common.load_config()
    request = common.read_json(request_path(), {}) or {}
    prev = common.read_json(path(), {}) or {}
    if prev.get("status") == "ok" and not request.get("force"):
        common.log("book probe already done at %s; set force in probe/request.json to run again" % prev.get("at"))
        return 0
    keys = odds.load_keys()
    if not keys:
        common.write_json(path(), {"status": "no_keys", "at": common.iso(now), "notes": ["no odds keys in the environment"]})
        common.log("no odds keys")
        return 1
    pool = odds.Pool(keys, now)
    client = odds.Client(pool, cfg)
    res = run_probe(now, cfg, pool, client, request)
    pool.save()
    if isinstance(res, dict):
        common.write_json(path(), res)
        common.log("book probe: %s" % res["status"])
        return 1
    out, pairs = res
    write_results(out, pairs if out["status"] == "ok" else None)
    common.log("book probe: %s, %d credits, %d book(s) returned, %d market(s) rejected" %
               (out["status"], out["credits_spent"], len(out["books_returned"]), len(out["markets_rejected"])))
    return 0 if out["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
