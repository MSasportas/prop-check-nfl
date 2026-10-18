"""Odds: price arithmetic, the real Hard Rock payload, the key pool (rotation, refused keys, ledger by position),
the pull planner (tiers, M / F / D / R / C, sentinel, started games) and the budget."""
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
LISTED = {SEA_DEN: CFG["ops"]["pull_full_keys"] + ["player_receptions_h1_fake"]}


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
        rec = odds.normalize(self.payload, THU, self.matcher(), ["player_receptions"], "F", at, 40, CFG)
        self.assertEqual(rec["book"], "hardrockbet_fl")
        self.assertEqual(rec["unmatched"], [])                        # every feed name matched a gsis id
        self.assertIn("spreads_h1@draftkings", rec["lines"])          # DraftKings kept only for display_only keys
        self.assertNotIn("h2h@draftkings", rec["lines"])
        self.assertEqual(rec["props"]["player_anytime_td"]["outcomes"][0]["side"], "over")    # "Yes" -> over
        view = odds.build_view([dict(rec, asked=list(rec["props"]) + list(rec["lines"]))], CFG)[SEA_DEN]
        rec_mv = view["props"]["rec"]
        pr = rec_mv["pairs"][(helpers.WILSON, 1.5)]
        self.assertEqual((pr["over"], pr["under"], pr["ok"]), (-120, -110, True))
        self.assertIn((helpers.WILSON, 2.5), view["props"]["rec"]["alt"] or {(helpers.WILSON, 2.5): 1})
        self.assertEqual(view["props"]["anytime_td"]["pairs"][(helpers.WILSON, 0.5)]["under"], None)
        gc = odds.game_context(view, THU)
        self.assertEqual((gc["total"], gc["spread"]["SEA"], gc["spread"]["DEN"], gc["source"]),
                         (41.5, 2.5, -2.5, "hardrock"))

    def test_stale_and_gone(self):
        at = T("2026-10-13T10:05:00-04:00")
        old = helpers.fresh(self.payload, "2026-10-13T04:27:00Z")     # 5.6 hours old at read time
        rec = odds.normalize(old, THU, self.matcher(), ["player_receptions"], "F", at, 1, CFG)
        view = odds.build_view([dict(rec, asked=["player_receptions"])], CFG)[SEA_DEN]
        self.assertFalse(view["props"]["rec"]["pairs"][(helpers.WILSON, 1.5)]["ok"])
        # a newer read that asked for the market and did not get it: gone
        good = odds.normalize(self.payload, THU, self.matcher(), ["player_receptions"], "F", at, 1, CFG)
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
        tiers = CFG["values"]["tiers"]
        self.assertEqual(t(tiers["full_plus"] * 10, 10), "full_plus")
        self.assertEqual(t(tiers["full"] * 10, 10), "full")
        self.assertEqual(t(tiers["standard"] * 10, 10), "standard")
        self.assertEqual(t(tiers["basic"] * 10, 10), "basic")
        self.assertEqual(t(tiers["lite"] * 10, 10), "lite")
        self.assertEqual(t(tiers["lite"] * 10 - 1, 10), "below")
        self.assertEqual(odds.make_plan(2300, 29, CFG)["tier"], "full_plus")      # the real pool on Oct 13
        self.assertEqual(odds.make_plan(2300, 29, CFG)["pulls"], "MFDRC")

    def test_check_slots(self):
        self.assertIsNone(odds.check_slot(T("2026-10-09T12:00:00-04:00"), THU, CFG))   # before the window
        self.assertEqual(odds.check_slot(T("2026-10-13T10:05:00-04:00"), THU, CFG), T("2026-10-13T10:00:00-04:00"))
        self.assertEqual(odds.check_slot(T("2026-10-13T09:55:00-04:00"), THU, CFG), T("2026-10-12T16:00:00-04:00"))
        self.assertEqual(odds.next_slot(T("2026-10-13T10:05:00-04:00"), THU, CFG), T("2026-10-13T16:00:00-04:00"))


class Due(unittest.TestCase):
    PLAN = {"tier": "full_plus", "pulls": "MFDRC", "allowance": 80}

    def due(self, now, games=(THU,), done=None, listed=None, m_done=None, focus=None, held=None, report=None,
            started=None, plan=None):
        return [(d["pull"], d["game"]["id"], len(d["keys"])) for d in odds.due_pulls(
            T(now), list(games), plan or self.PLAN, CFG, done or {}, focus or {}, held or {}, report or {},
            started or {}, listed if listed is not None else {}, m_done or {})]

    def test_m_first_then_f_and_never_an_unlisted_key(self):
        self.assertEqual(self.due("2026-10-13T10:05:00-04:00"), [("M", SEA_DEN, 0)])
        out = odds.due_pulls(T("2026-10-13T10:05:00-04:00"), [THU], self.PLAN, CFG, {}, {}, {}, {}, {}, LISTED,
                             {SEA_DEN: T("2026-10-13T10:05:00-04:00")})
        self.assertEqual(out[0]["pull"], "F")
        self.assertNotIn("player_receptions_h1", out[0]["keys"])
        self.assertTrue(set(out[0]["keys"]) <= set(LISTED[SEA_DEN]))
        self.assertEqual(out[0]["books"], ["hardrockbet_fl", "draftkings"])    # one call: lines include spreads_h1

    def test_no_props_yet_waits_for_the_next_slot(self):
        listed = {SEA_DEN: ["h2h", "spreads", "totals"]}
        m = {SEA_DEN: T("2026-10-13T10:05:00-04:00")}
        self.assertEqual(self.due("2026-10-13T12:00:00-04:00", listed=listed, m_done=m), [])
        self.assertEqual(self.due("2026-10-13T16:05:00-04:00", listed=listed, m_done=m), [("M", SEA_DEN, 0)])

    def test_sentinel_gates_the_rest_of_the_day(self):
        listed = {HOU_JAX: ["h2h", "spreads"]}                        # the 9:30 AM game shows no props yet
        m = {HOU_JAX: T("2026-10-14T10:01:00-04:00")}
        out = self.due("2026-10-14T10:05:00-04:00", games=(SUN1, SUN2), listed=listed, m_done=m)
        self.assertEqual(out, [])                                     # CHI at ATL waits for its sentinel
        listed = {HOU_JAX: CFG["ops"]["pull_full_keys"]}
        out = self.due("2026-10-14T10:05:00-04:00", games=(SUN1, SUN2), listed=listed, m_done=m)
        self.assertEqual([x[:2] for x in out], [("F", HOU_JAX), ("M", "2026_06_CHI_ATL")])

    def test_f_done_once_and_r_after_the_report(self):
        f = {"pull": "F", "at": T("2026-10-13T10:05:00-04:00"), "asked": LISTED[SEA_DEN],
             "returned": CFG["ops"]["pull_full_keys"][:20]}
        done = {SEA_DEN: [f]}
        m = {SEA_DEN: T("2026-10-13T10:05:00-04:00")}
        rep = {SEA_DEN: T("2026-10-14T16:00:00-04:00")}
        self.assertEqual(self.due("2026-10-13T16:05:00-04:00", done=done, listed=LISTED, m_done=m, report=rep), [])
        self.assertEqual(self.due("2026-10-14T16:30:00-04:00", done=done, listed=LISTED, m_done=m, report=rep), [])
        out = self.due("2026-10-14T17:01:00-04:00", done=done, listed=LISTED, m_done=m, report=rep)
        self.assertEqual(out[0][:2], ("R", SEA_DEN))
        r = {"pull": "R", "at": T("2026-10-14T17:01:00-04:00"), "asked": [], "returned": ["player_receptions"]}
        done = {SEA_DEN: [f, r]}
        self.assertEqual(self.due("2026-10-14T17:30:00-04:00", done=done, listed=LISTED, m_done=m, report=rep), [])

    def test_c_and_d_only_for_markets_that_matter(self):
        f = {"pull": "F", "at": T("2026-10-13T10:05:00-04:00"), "asked": [], "returned": CFG["ops"]["pull_full_keys"]}
        done = {SEA_DEN: [f]}
        m = {SEA_DEN: T("2026-10-13T10:05:00-04:00")}
        held = {SEA_DEN: {"player_receptions"}}
        out = self.due("2026-10-14T10:05:00-04:00", done=done, listed=LISTED, m_done=m, held=held)
        self.assertEqual(out, [("D", SEA_DEN, 1)])
        out = self.due("2026-10-15T19:01:00-04:00", done=done, listed=LISTED, m_done=m, focus=held)
        self.assertEqual(out, [("C", SEA_DEN, 3)])                   # receptions + spreads + totals
        self.assertEqual(self.due("2026-10-15T19:01:00-04:00", done=done, listed=LISTED, m_done=m), [])

    def test_started_games_are_never_pulled(self):
        self.assertEqual(self.due("2026-10-15T20:00:00-04:00", listed=LISTED), [])     # inside c_latest_min
        self.assertEqual(self.due("2026-10-13T10:05:00-04:00", started={SEA_DEN: True}), [])

    def test_lite_tier_has_no_full_pull(self):
        plan = {"tier": "lite", "pulls": "MRC", "allowance": 25}
        m = {SEA_DEN: T("2026-10-13T10:05:00-04:00")}
        self.assertEqual(self.due("2026-10-13T10:06:00-04:00", listed=LISTED, m_done=m, plan=plan), [])
        out = self.due("2026-10-14T17:01:00-04:00", listed=LISTED, m_done=m, plan=plan,
                       report={SEA_DEN: T("2026-10-14T16:00:00-04:00")})
        self.assertEqual(out[0][:2], ("R", SEA_DEN))


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
        js, cost = client.event_odds("ev1", THU, ["player_receptions", "player_receptions_h1"], ["hardrockbet_fl"], "F")
        self.assertEqual([c[0] for c in self.calls], ["1111", "2222", "3333"])
        self.assertNotIn("player_receptions_h1", self.urls[-1])      # never requested, even if asked
        self.assertNotIn("regions=", self.urls[-1])
        self.assertEqual(cost, 1)
        self.assertEqual([(s["left"], s["status"]) for s in pool.state], [(0, "empty"), (None, "limited"), (88, "ok")])
        self.assertEqual(pool.public(), [{"n": 1, "left": 0, "status": "empty", "shared": False}, {"n": 2, "left": None, "status": "limited", "shared": False},
                                         {"n": 3, "left": 88, "status": "ok", "shared": False}])
        self.calls.clear()
        client.event_odds("ev1", THU, ["player_receptions"], ["hardrockbet_fl"], "F")
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
        client.event_odds("ev1", THU, ["player_receptions"], ["hardrockbet_fl"], "F")
        self.assertEqual(pool.state[0]["left"], 17)

    def test_refused_key_is_unknown_not_zero(self):
        self.fake(lambda key, url: common.Http(401, {}, b'{"message":"Invalid API key"}', "HTTP 401"))
        pool = odds.Pool(odds.load_keys(), T("2026-10-13T10:05:00-04:00"))
        client = odds.Client(pool, CFG)
        js, _ = client.event_odds("ev1", THU, ["player_receptions"], ["hardrockbet_fl"], "F")
        self.assertIsNone(js)
        self.assertTrue(pool.all_refused())
        self.assertEqual(pool.total_left(), 0)
        self.assertFalse(pool.known())

    def test_network_failure_does_not_burn_through_keys(self):
        self.fake(lambda key, url: common.Http(0, {}, None, "TimeoutError"))
        pool = odds.Pool(odds.load_keys(), T("2026-10-13T10:05:00-04:00"))
        client = odds.Client(pool, CFG)
        js, _ = client.event_odds("ev1", THU, ["player_receptions"], ["hardrockbet_fl"], "F")
        self.assertIsNone(js)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([s["status"] for s in pool.state], ["ok", "ok", "ok"])

    def test_markets_endpoint(self):
        body = json.dumps({"bookmakers": [{"key": "hardrockbet_fl", "markets": [{"key": "h2h"}, {"key": "player_receptions"}]}]}).encode()
        self.fake(lambda key, url: common.Http(200, {"x-requests-remaining": "99", "x-requests-last": "1"}, body))
        pool = odds.Pool(odds.load_keys(), T("2026-10-13T10:05:00-04:00"))
        client = odds.Client(pool, CFG)
        self.assertEqual(client.event_markets("ev1", SEA_DEN), ["h2h", "player_receptions"])
        self.assertIn("/events/ev1/markets", self.urls[0])
        self.assertIn("bookmakers=hardrockbet_fl", self.urls[0])


class Pulls(unittest.TestCase):
    def test_budget_and_one_write_per_read(self):
        w = World(pool=(60,))                                         # 60 credits / 29 games -> "below": M + R only
        try:
            w.at("2026-10-13T10:05")
            st = w.status()
            self.assertEqual(st["tier"], "below")
            self.assertTrue(all(d["pull"] == "M" for d in st["due"]))
            self.assertEqual(st["pool_total"], 60 - len(st["due"]))
            self.assertEqual(w.slate()["picks"], [])
        finally:
            w.close()

    def test_pool_empty_and_no_keys_banners(self):
        w = World(pool=(0, 0))
        try:
            s = w.at("2026-10-13T10:05")
            self.assertEqual(s["status"]["odds"]["state"], "pool_empty")
            self.assertTrue(s["status"]["pool"]["empty"])
            self.assertTrue(any("Out of odds keys" in b["text"] for b in s["banners"]))
        finally:
            w.close()
        w = World()
        try:
            os.environ.pop("PC_ODDS_FIXTURE_DIR")
            s = w.at("2026-10-13T10:05")
            self.assertTrue(any("no odds keys are set" in b["text"] for b in s["banners"]))
            self.assertEqual(s["status"]["odds"]["state"], "not_yet")
        finally:
            w.close()

    def test_every_saved_read_is_our_price_history(self):
        w = World()
        try:
            w.at("2026-10-13T10:05")
            doc = helpers.load(w.path("odds", "2026-10-13.json"))
            f = [x for x in doc["fetches"] if x["game_id"] == SEA_DEN]
            self.assertEqual(len(f), 1)
            self.assertEqual(f[0]["pull"], "F")
            self.assertGreaterEqual(len(f[0]["props"]), 15)
            self.assertEqual(f[0]["cost"], 40)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
