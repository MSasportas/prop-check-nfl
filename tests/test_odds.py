"""Odds: price arithmetic, the real Hard Rock payload plus a synthetic consensus, the key pool (rotation, refused keys, ledger
by position, x-requests-last), the pull planner (tiers, R / C, started games) and the budget."""
import json
import os
import unittest
from datetime import timedelta

import helpers
from helpers import SEA_DEN, HOU_JAX, World
import common
import names
import odds
import sched

CFG = helpers.cfg()
T = common.parse_iso


def game(gid, start, away="SEA", home="DEN"):
    return {"id": gid, "start": start, "away": away, "home": home, "date": start[:10], "week": 6, "season": 2026}


THU = game(SEA_DEN, "2026-10-15T20:15:00-04:00")
SUN1 = game(HOU_JAX, "2026-10-18T09:30:00-04:00", "HOU", "JAX")
SUN2 = game("2026_06_CHI_ATL", "2026-10-18T13:00:00-04:00", "CHI", "ATL")


class Arithmetic(unittest.TestCase):
    def test_prices(self):
        self.assertAlmostEqual(odds.break_even(-200), 2 / 3.0)
        self.assertAlmostEqual(odds.break_even(120), 100 / 220.0)
        self.assertAlmostEqual(odds.decimal(-200), 1.5)
        self.assertEqual(odds.american(2.65), 165)
        self.assertEqual(odds.american(1.5), -200)
        self.assertAlmostEqual(odds.fair_two_sided(-110, -110), 0.5)
        self.assertAlmostEqual(odds.hold(-110, -110), 0.0476, places=4)


class RealPayload(unittest.TestCase):
    """The probe's SEA at DEN answer (35 Hard Rock markets + game lines + DraftKings 1H)."""

    def setUp(self):
        self.w = World()
        self.payload = helpers.fresh(helpers.load(os.path.join(helpers.WORLD, "odds", "odds_%s.json" % SEA_DEN)),
                                     "2026-10-13T14:00:00Z")

    def tearDown(self):
        self.w.close()

    def matcher(self):
        import roster
        ppl = roster.parse(open(os.path.join(helpers.WORLD, "roster_weekly_2026.csv")).read(), 2026)
        return names.Matcher([{"id": k, "first": p["first"], "last": p["last"], "team": p["team"]}
                              for k, p in ppl.items() if p["team"] in ("SEA", "DEN")])

    def test_normalize_and_view(self):
        at = T("2026-10-13T10:05:00-04:00")
        rec = odds.normalize(self.payload, THU, self.matcher(), ["player_receptions"], "R", at, 6, CFG)
        self.assertEqual(rec["book"], "hardrockbet_fl")
        self.assertEqual(rec["unmatched"], [])                        # every feed name matched a gsis id
        self.assertEqual(rec["others"], {})                           # draftkings only had game lines: no player market
        self.assertEqual(rec["props"]["player_anytime_td"]["outcomes"][0]["side"], "over")    # "Yes" -> over
        view = odds.build_view([dict(rec, asked=["player_receptions"])], CFG)[SEA_DEN]
        pr = view["props"]["rec"]["pairs"][(helpers.WILSON, 1.5)]
        self.assertEqual((pr["over"], pr["under"], pr["ok"]), (-120, -110, True))
        gc = odds.game_context(view, THU)
        self.assertEqual((gc["total"], gc["spread"]["SEA"], gc["spread"]["DEN"], gc["source"]),
                         (41.5, 2.5, -2.5, "hardrock"))
        self.assertIn(odds.game_context({}, THU)["source"], (None, "schedule"))  # no game lines at all: never invented

    def test_others_are_normalized_by_book_and_feed_key(self):
        at = T("2026-10-14T17:10:00-04:00")
        pay = helpers.add_books(self.payload, {}, books=("pinnacle", "fanduel"))
        pay["bookmakers"].append({"key": "hardrockbet", "title": "Hard Rock Bet", "markets": [
            dict(m, key=m["key"]) for m in pay["bookmakers"][0]["markets"] if m["key"] == "player_receptions"]})
        keys = ["player_receptions", "player_reception_yds"]
        rec = odds.normalize(pay, THU, self.matcher(), keys, "R", at, 2, CFG)
        self.assertEqual(sorted(rec["others"]), ["fanduel", "pinnacle"])          # never the national 'hardrockbet' key
        self.assertEqual(sorted(rec["others"]["pinnacle"]), sorted(helpers.SIX))
        row = next(o for o in rec["others"]["pinnacle"]["player_receptions"]["outcomes"] if o["pid"] == helpers.WILSON)
        self.assertEqual(set(row), {"pid", "side", "line", "price"})              # compact rows
        self.assertNotIn("player_receptions_alternate", rec["others"]["pinnacle"])  # main lines only
        self.assertEqual(rec["book"], "hardrockbet_fl")

    def test_request_books_hard_rock_first_ten_in_all_and_never_the_national_key(self):
        books = odds.request_books(CFG)
        self.assertEqual(books[0], "hardrockbet_fl")
        self.assertEqual(len(books), 10)
        self.assertEqual(books[1], "pinnacle")
        self.assertNotIn("hardrockbet", books)
        self.assertNotIn("novig", books)
        self.assertNotIn("prophetx", books)
        self.assertEqual(odds.request_books(CFG, others=False), ["hardrockbet_fl"])
        self.assertEqual(odds.regions_billed(10), 1)
        self.assertEqual(odds.regions_billed(11), 2)

    def test_stale_and_gone(self):
        at = T("2026-10-13T10:05:00-04:00")
        old = helpers.fresh(self.payload, "2026-10-13T04:27:00Z")     # 5.6 hours old at read time
        rec = odds.normalize(old, THU, self.matcher(), ["player_receptions"], "R", at, 1, CFG)
        view = odds.build_view([dict(rec, asked=["player_receptions"])], CFG)[SEA_DEN]
        self.assertFalse(view["props"]["rec"]["pairs"][(helpers.WILSON, 1.5)]["ok"])
        # a newer read that asked for the market and did not get it: gone
        good = odds.normalize(self.payload, THU, self.matcher(), ["player_receptions"], "R", at, 1, CFG)
        empty = dict(good, at=common.iso(at + timedelta(hours=2)), props={}, returned=[])
        view = odds.build_view([dict(good, asked=["player_receptions"]), dict(empty, asked=["player_receptions"])], CFG)
        self.assertTrue(view[SEA_DEN]["props"]["rec"]["gone"])

    def test_events_matched_by_teams_and_time(self):
        ev = helpers.load(os.path.join(helpers.WORLD, "odds", "events.json"))
        m, unknown = odds.match_events(ev, [THU, SUN1], 6)
        self.assertEqual(m, {SEA_DEN: "855f58fad5f0f14b239f4f63df27998b", HOU_JAX: "57418b2cf5aca558b3c98eafac1e264a"})
        self.assertEqual(unknown, [])


class Plan(unittest.TestCase):
    def test_tiers(self):
        t = lambda credits, games: odds.make_plan(credits, games, CFG)["tier"]
        tiers = CFG["ops"]["tiers"]
        self.assertEqual(t(tiers["full"] * 10, 10), "full")
        self.assertEqual(t(tiers["full"] * 10 - 1, 10), "tight")
        self.assertEqual(t(tiers["tight"] * 10, 10), "tight")
        self.assertEqual(t(tiers["tight"] * 10 - 1, 10), "below")
        self.assertEqual(odds.make_plan(690, 58, CFG)["pulls"], "Rc")                 # 11.9 a game: R everywhere, C where it matters
        self.assertEqual(odds.make_plan(700, 58, CFG)["pulls"], "RC")
        self.assertEqual(odds.make_plan(1000, 58, CFG)["tier"], "full")
        self.assertEqual(odds.make_plan(498, 0, CFG)["tier"], "full")                 # no games before the reset: all of it

    def test_monthly_cap_limits_the_plan_not_the_pool(self):
        pool = odds.Pool(["k1", "k2"], T("2026-11-03T10:00:00-05:00"))
        pool.state[0].update(left=900, status="ok")
        pool.state[1].update(left=900, status="ok")
        pool.spent = [{"at": "2026-11-02T10:00:00-05:00", "date": "2026-11-02", "pull": "R", "game_id": "x", "cost": 300,
                       "key": 1}]
        st = {}
        plan = odds._set_plan(st, pool, 50, CFG, T("2026-11-03T10:00:00-05:00"))
        self.assertEqual(plan["pool_at_plan"], 700)                                   # 1000 cap - 300 spent
        self.assertEqual(plan["pool_raw"], 1800)
        self.assertEqual(plan["allowance"], 14.0)
        self.assertEqual(plan["tier"], "full")


class Due(unittest.TestCase):
    PLAN = {"tier": "full", "pulls": "RC", "allowance": 20}
    REPORT = {SEA_DEN: T("2026-10-14T17:00:00-04:00")}

    def due(self, now, games=(THU,), done=None, focus=None, report=None, started=None, plan=None, ok=None, conf=()):
        rep = self.REPORT if report is None else report
        return [(d["pull"], d["game"]["id"], len(d["keys"])) for d in odds.due_pulls(
            T(now), list(games), plan or self.PLAN, CFG, done or {}, focus or {}, started or {}, rep,
            set(rep) if ok is None else ok, set(conf))]

    def read(self, pull, at, hr=True, ina=False):
        return {"pull": pull, "at": T(at), "asked": [], "returned": ["player_receptions"],
                "hr": ["player_receptions"] if hr else [], "ina": ina}

    def test_nothing_before_the_final_injury_report(self):
        self.assertEqual(self.due("2026-10-13T10:05:00-04:00"), [])
        self.assertEqual(self.due("2026-10-14T16:59:00-04:00"), [])
        self.assertEqual(self.due("2026-10-14T17:00:00-04:00", ok=set()), [])        # injury read not fresh after the report yet

    def test_r_once_the_report_counts_in_six_markets_one_call(self):
        out = odds.due_pulls(T("2026-10-14T17:01:00-04:00"), [THU], self.PLAN, CFG, {}, {}, {}, self.REPORT, {SEA_DEN}, set())
        self.assertEqual([(d["pull"], len(d["keys"])) for d in out], [("R", 6)])
        self.assertEqual(out[0]["keys"], odds.pick_keys(CFG))
        self.assertEqual(out[0]["books"], odds.request_books(CFG))
        self.assertEqual(odds.est_cost(out[0]), 6)
        done = {SEA_DEN: [self.read("R", "2026-10-14T17:01:00-04:00")]}
        self.assertEqual(self.due("2026-10-14T17:30:00-04:00", done=done), [])

    def test_r_retries_only_while_hard_rock_has_not_posted(self):
        done = {SEA_DEN: [self.read("R", "2026-10-14T17:01:00-04:00", hr=False)]}
        self.assertEqual(self.due("2026-10-14T18:00:00-04:00", done=done), [])
        self.assertEqual(self.due("2026-10-14T21:05:00-04:00", done=done), [("R", SEA_DEN, 6)])   # r_retry_min later
        done[SEA_DEN] += [self.read("R", "2026-10-14T21:05:00-04:00", hr=False), self.read("R", "2026-10-15T01:10:00-04:00", hr=False)]
        self.assertEqual(self.due("2026-10-15T09:00:00-04:00", done=done), [], "r_max_reads is 3")

    def test_c_at_70_minutes_when_inactives_are_in_else_45(self):
        done = {SEA_DEN: [self.read("R", "2026-10-14T17:01:00-04:00")]}
        self.assertEqual(self.due("2026-10-15T19:04:00-04:00", done=done, conf=[SEA_DEN]), [])
        self.assertEqual(self.due("2026-10-15T19:05:00-04:00", done=done, conf=[SEA_DEN]), [("C", SEA_DEN, 6)])
        self.assertEqual(self.due("2026-10-15T19:05:00-04:00", done=done), [])         # inactives not in: wait for 45
        self.assertEqual(self.due("2026-10-15T19:30:00-04:00", done=done), [("C", SEA_DEN, 6)])
        # a C made before inactives were in is made again once they are
        done[SEA_DEN].append(self.read("C", "2026-10-15T19:30:00-04:00", ina=False))
        self.assertEqual(self.due("2026-10-15T19:40:00-04:00", done=done), [])
        self.assertEqual(self.due("2026-10-15T19:40:00-04:00", done=done, conf=[SEA_DEN]), [("C", SEA_DEN, 6)])
        done[SEA_DEN].append(self.read("C", "2026-10-15T19:40:00-04:00", ina=True))
        self.assertEqual(self.due("2026-10-15T19:50:00-04:00", done=done, conf=[SEA_DEN]), [])

    def test_tight_tier_asks_c_only_where_a_pick_or_near_pick_sits(self):
        plan = {"tier": "tight", "pulls": "Rc", "allowance": 9}
        done = {SEA_DEN: [self.read("R", "2026-10-14T17:01:00-04:00")]}
        self.assertEqual(self.due("2026-10-15T19:30:00-04:00", plan=plan, done=done), [])
        focus = {SEA_DEN: {"player_receptions", "player_rush_yds"}}
        self.assertEqual(self.due("2026-10-15T19:30:00-04:00", plan=plan, focus=focus, done=done), [("C", SEA_DEN, 2)])
        self.assertEqual(self.due("2026-10-14T17:01:00-04:00", plan=plan), [("R", SEA_DEN, 6)])   # R is everywhere

    def test_started_games_are_never_pulled(self):
        self.assertEqual(self.due("2026-10-15T20:00:00-04:00", conf=[SEA_DEN]), [])       # inside c_latest_min
        self.assertEqual(self.due("2026-10-14T17:05:00-04:00", started={SEA_DEN: True}), [])

    def test_below_tier_is_r_only(self):
        plan = {"tier": "below", "pulls": "R", "allowance": 3}
        self.assertEqual(self.due("2026-10-15T19:30:00-04:00", plan=plan), [("R", SEA_DEN, 6)])
        done = {SEA_DEN: [self.read("R", "2026-10-14T17:01:00-04:00")]}
        self.assertEqual(self.due("2026-10-15T19:30:00-04:00", plan=plan, done=done), [])

    def test_next_check(self):
        n = odds.next_check_at(T("2026-10-13T10:00:00-04:00"), [THU], self.PLAN, CFG, {}, self.REPORT, {})
        self.assertEqual(n, T("2026-10-14T17:00:00-04:00"))
        done = {SEA_DEN: [self.read("R", "2026-10-14T17:01:00-04:00")]}
        n = odds.next_check_at(T("2026-10-14T18:00:00-04:00"), [THU], self.PLAN, CFG, done, self.REPORT, {})
        self.assertEqual(n, T("2026-10-15T19:30:00-04:00"))


class KeyRotation(unittest.TestCase):
    def setUp(self):
        self.w = World()
        os.environ.pop("PC_ODDS_FIXTURE_DIR")                         # the real client, network replaced below
        os.environ["ODDS_API_KEYS"] = "aaaaaaaaaaaaaaaa1111\nbbbbbbbbbbbbbbbb2222, cccccccccccccccc3333"
        self.real_get = common.http_get
        self.calls = []

    def tearDown(self):
        common.http_get = self.real_get
        self.w.close()

    def fake(self, behaviour):
        def get(url, **kw):
            key = url.split("apiKey=")[1].split("&")[0]
            self.calls.append((key[-4:], url.split("?")[0].rsplit("/", 1)[-1]))
            self.urls.append(url)
            return behaviour(key, url)
        self.urls = []
        common.http_get = get

    def test_rotates_past_used_up_and_refused_keys(self):
        payload = json.dumps({"bookmakers": [{"key": "hardrockbet_fl", "markets": [{"key": "player_receptions",
                                                                                      "outcomes": []}]}]}).encode()

        def behaviour(key, url):
            if key.endswith("1111"):
                return common.Http(401, {}, b'{"error_code":"OUT_OF_USAGE_CREDITS"}', "HTTP 401")
            if key.endswith("2222"):
                return common.Http(429, {}, b'{"message":"Too many requests"}', "HTTP 429")
            return common.Http(200, {"x-requests-remaining": "88", "x-requests-last": "1"}, payload)
        self.fake(behaviour)
        now = T("2026-10-13T10:05:00-04:00")
        keys = odds.load_keys()
        self.assertEqual(len(keys), 3)
        pool = odds.Pool(keys, now)
        client = odds.Client(pool, CFG)
        js, cost = client.event_odds("ev1", THU, ["player_receptions"], ["hardrockbet_fl"], "R")
        self.assertEqual([c[0] for c in self.calls], ["1111", "2222", "3333"])
        self.assertNotIn("regions=", self.urls[-1])
        self.assertIn("bookmakers=hardrockbet_fl", self.urls[-1])
        self.assertEqual(cost, 1)
        self.assertEqual([(s["left"], s["status"]) for s in pool.state], [(0, "empty"), (None, "limited"), (88, "ok")])
        self.assertEqual(pool.public(), [{"n": 1, "left": 0, "status": "empty", "shared": False}, {"n": 2, "left": None, "status": "limited", "shared": False},
                                         {"n": 3, "left": 88, "status": "ok", "shared": False}])
        self.calls.clear()
        client.event_odds("ev1", THU, ["player_receptions"], ["hardrockbet_fl"], "R")
        self.assertEqual([c[0] for c in self.calls], ["3333"])
        pool.save()
        with open(odds.ledger_path()) as f:
            raw = f.read()
        for k in keys:
            self.assertNotIn(k, raw)
            self.assertNotIn(k[-4:], raw)

    def test_the_header_is_trusted_over_our_arithmetic(self):
        payload = json.dumps({"bookmakers": []}).encode()
        self.fake(lambda key, url: common.Http(200, {"x-requests-remaining": "17", "x-requests-last": "0"}, payload))
        pool = odds.Pool(odds.load_keys(), T("2026-10-13T10:05:00-04:00"))
        pool.state[0]["left"] = 400                                   # what we thought (another tool spent the rest)
        client = odds.Client(pool, CFG)
        client.event_odds("ev1", THU, ["player_receptions"], ["hardrockbet_fl"], "R")
        self.assertEqual(pool.state[0]["left"], 17)

    def test_refused_key_is_unknown_not_zero(self):
        self.fake(lambda key, url: common.Http(401, {}, b'{"message":"Invalid API key"}', "HTTP 401"))
        pool = odds.Pool(odds.load_keys(), T("2026-10-13T10:05:00-04:00"))
        client = odds.Client(pool, CFG)
        js, _ = client.event_odds("ev1", THU, ["player_receptions"], ["hardrockbet_fl"], "R")
        self.assertIsNone(js)
        self.assertTrue(pool.all_refused())
        self.assertEqual(pool.total_left(), 0)
        self.assertFalse(pool.known())

    def test_network_failure_does_not_burn_through_keys(self):
        self.fake(lambda key, url: common.Http(0, {}, None, "TimeoutError"))
        pool = odds.Pool(odds.load_keys(), T("2026-10-13T10:05:00-04:00"))
        client = odds.Client(pool, CFG)
        js, _ = client.event_odds("ev1", THU, ["player_receptions"], ["hardrockbet_fl"], "R")
        self.assertIsNone(js)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([s["status"] for s in pool.state], ["ok", "ok", "ok"])

    def test_ten_books_one_call_cost_is_read_from_the_header(self):
        body = json.dumps({"bookmakers": [{"key": "hardrockbet_fl", "markets": [{"key": "player_receptions", "outcomes": []}]},
                                          {"key": "pinnacle", "markets": [{"key": "player_receptions", "outcomes": []}]}]}).encode()
        self.fake(lambda key, url: common.Http(200, {"x-requests-remaining": "94", "x-requests-last": "1"}, body))
        pool = odds.Pool(odds.load_keys(), T("2026-10-14T17:10:00-04:00"))
        client = odds.Client(pool, CFG)
        books = odds.request_books(CFG)
        js, cost = client.event_odds("ev1", THU, ["player_receptions"], books, "R")
        self.assertEqual(cost, 1)                                     # one market, one region, however many books
        self.assertEqual(client.errors, [])
        self.assertEqual(self.urls[-1].count("bookmakers="), 1)
        self.assertIn("pinnacle", self.urls[-1])
        self.assertNotIn("hardrockbet%2C", self.urls[-1])             # the national key is never asked

    def test_a_read_billed_more_than_one_region_is_noted(self):
        body = json.dumps({"bookmakers": [{"key": "hardrockbet_fl", "markets": [{"key": "player_receptions", "outcomes": []}]}]}).encode()
        self.fake(lambda key, url: common.Http(200, {"x-requests-remaining": "90", "x-requests-last": "3"}, body))
        pool = odds.Pool(odds.load_keys(), T("2026-10-14T17:10:00-04:00"))
        client = odds.Client(pool, CFG)
        js, cost = client.event_odds("ev1", THU, ["player_receptions"], odds.request_books(CFG), "R")
        self.assertEqual(cost, 3)
        self.assertTrue(any("billed 3 credits for 1 market" in e for e in client.errors), client.errors)


class Pulls(unittest.TestCase):
    def test_budget_and_one_write_per_read(self):
        w = World(pool=(4,))                                          # 4 credits: a 6-credit R read cannot be paid
        try:
            w.at("2026-10-14T17:10")
            st = w.status()
            self.assertEqual(st["tier"], "below")                     # 4 credits across the week's games
            self.assertEqual(st["pool_total"], 4)
            self.assertEqual(w.slate()["picks"], [])
        finally:
            w.close()

    def test_pool_empty_and_no_keys_banners(self):
        w = World(pool=(0, 0))
        try:
            s = w.at("2026-10-14T17:10")
            self.assertEqual(s["status"]["odds"]["state"], "pool_empty")
            self.assertTrue(s["status"]["pool"]["empty"])
            self.assertTrue(any("Out of odds keys" in b["text"] for b in s["banners"]))
        finally:
            w.close()
        w = World()
        try:
            os.environ.pop("PC_ODDS_FIXTURE_DIR")
            s = w.at("2026-10-14T17:10")
            self.assertTrue(any("no odds keys are set" in b["text"] for b in s["banners"]))
            self.assertEqual(s["status"]["odds"]["state"], "not_yet")
        finally:
            w.close()

    def test_every_saved_read_is_our_price_history(self):
        w = World()
        try:
            w.at("2026-10-14T17:10")
            doc = helpers.load(w.path("odds", "2026-10-14.json"))
            f = [x for x in doc["fetches"] if x["game_id"] == SEA_DEN]
            self.assertEqual(len(f), 1)
            self.assertEqual(f[0]["pull"], "R")
            self.assertEqual(sorted(f[0]["props"]), sorted(helpers.SIX))        # only the six picked markets are asked
            self.assertEqual(f[0]["cost"], 6)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
