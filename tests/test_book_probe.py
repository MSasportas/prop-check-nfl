"""book_probe: the summary on a small mocked payload, and the whole probe against a mocked HTTP layer (no network)."""
import os
import shutil
import tempfile
import unittest
import urllib.parse

import helpers   # noqa: F401  (puts scripts/ on the path)
import book_probe as bp
import common
import odds


def o(name, who, pt, price):
    return {"name": name, "description": who, "point": pt, "price": price}


def book(key, mkts):
    return {"key": key, "title": key.title(), "markets": [
        {"key": mk, "last_update": "2026-10-30T20:00:00Z", "outcomes": outs} for mk, outs in mkts.items()]}


def two(who, pt, over, under):
    return [o("Over", who, pt, over), o("Under", who, pt, under)]


YDS = "player_pass_yds"
PAYLOAD = {"bookmakers": [
    book("hardrockbet_fl", {YDS: two("A", 250.5, -115, -115) + two("B", 230.5, -110, -120),
                             "player_anytime_td": [o("Yes", "A", None, 150)]}),
    book("pinnacle", {YDS: two("A", 250.5, -105, -125) + two("B", 230.5, -110, -110)}),
    book("b3", {YDS: two("A", 250.5, -110, -110)}),
    book("b4", {YDS: two("A", 250.5, -110, -110) + two("B", 232.5, -110, -110)}),
    book("b5", {YDS: two("A", 250.5, -120, -100)}),
    book("b6", {YDS: two("A", 250.5, -110, -110) + [o("Over", "C", 200.5, -110)]}),
    book("onlyover", {YDS: [o("Over", "A", 250.5, -110)]}),
]}


class Summary(unittest.TestCase):
    def setUp(self):
        self.books = bp.collect(PAYLOAD)
        self.pm, self.pb = bp.summarize(self.books, [YDS, "player_anytime_td", "player_rush_yds"])

    def test_two_sided_only(self):
        m = self.pm[YDS]
        self.assertTrue(m["hard_rock"]["present"] and m["pinnacle"]["present"])
        self.assertNotIn("onlyover", m["books"])                      # one side only is not a pair
        self.assertEqual(m["books"]["b6"]["pairs"], 1)                # C has an over only
        self.assertEqual(m["books_two_sided"], 6)
        self.assertEqual(m["books"]["hardrockbet_fl"]["pairs"], 2)

    def test_gap_vs_pinnacle(self):
        b = self.pm[YDS]["books"]
        pin_fair = odds.fair_two_sided(-105, -125)
        self.assertAlmostEqual(b["b3"]["gap_abs"], round(0.5 - pin_fair, 4), places=4)
        self.assertAlmostEqual(b["b3"]["gap_signed"], round(0.5 - pin_fair, 4), places=4)
        self.assertEqual(b["b4"]["shared_with_pinnacle"], 1)          # B at 232.5 is a different line
        self.assertEqual(b["pinnacle"]["gap_abs"], 0.0)
        self.assertAlmostEqual(b["hardrockbet_fl"]["hold_mean"], round((odds.hold(-115, -115) + odds.hold(-110, -120)) / 2, 4), places=4)

    def test_the_number_that_matters(self):
        m = self.pm[YDS]
        # A at 250.5: Hard Rock + Pinnacle + b3, b4, b5, b6 (4 others). B at 230.5: no other book.
        self.assertEqual(m["hr_pin_4plus_players"], 1)
        self.assertEqual(m["hr_pin_4plus_pairs"], 1)
        self.assertEqual(m["hr_and_pinnacle_pairs"], 2)

    def test_main_line(self):
        ml = self.pm[YDS]["main_lines"]
        self.assertEqual(ml["A"], {"line": 250.5, "books": 6})
        self.assertEqual(ml["B"]["books"], 2)                         # 230.5 (HR, Pinnacle) beats 232.5 (b4)
        self.assertEqual(ml["B"]["line"], 230.5)

    def test_one_sided_and_empty_markets(self):
        td = self.pm["player_anytime_td"]
        self.assertTrue(td["hard_rock"]["present"])
        self.assertFalse(td["pinnacle"]["present"])
        self.assertEqual(td["hr_pin_4plus_players"], 0)
        self.assertEqual(self.pm["player_rush_yds"]["books_usable"], [])
        self.assertFalse(self.pm["player_rush_yds"]["hard_rock"]["present"])

    def test_book_rollup(self):
        self.assertEqual(self.pb["pinnacle"]["pairs"], 2)
        self.assertEqual(self.pb["hardrockbet_fl"]["markets"], sorted([YDS, "player_anytime_td"]))


class FakeHttp:
    """Stands in for common.http_get: one key with 500 credits, player_rush_longest refused by the feed."""

    def __init__(self):
        self.calls, self.left = [], 500

    def __call__(self, url, **kw):
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        self.calls.append((u.path, q))
        if u.path.endswith("/events"):
            ev = [{"id": "later", "commence_time": "2026-11-01T18:00:00Z", "home_team": "X", "away_team": "Y"},
                  {"id": "early", "commence_time": "2026-11-01T14:00:00Z", "home_team": "Green Bay Packers",
                   "away_team": "Carolina Panthers"},
                  {"id": "gone", "commence_time": "2026-10-29T14:00:00Z", "home_team": "P", "away_team": "Q"}]
            return common.Http(200, {"x-requests-remaining": str(self.left), "x-requests-last": "0"}, common.dumps(ev).encode())
        mk = q["markets"][0].split(",")
        assert "bookmakers" not in q and q["regions"] == ["us,us2,us_ex,eu,uk"], q
        if "player_rush_longest" in mk:
            return common.Http(422, {}, b'{"message":"Invalid market"}', "HTTP 422")
        have = [m for m in mk if m in (YDS, "player_anytime_td")]
        cost = len(have) * 5
        self.left -= cost
        payload = {"bookmakers": [dict(b, markets=[m for m in b["markets"] if m["key"] in mk]) for b in PAYLOAD["bookmakers"]]}
        return common.Http(200, {"x-requests-remaining": str(self.left), "x-requests-last": str(cost)},
                           common.dumps(payload).encode())


class WholeProbe(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("PC_DATA_DIR", "PC_NOW")}
        self.tmp = tempfile.mkdtemp(prefix="pcn_probe_")
        os.environ["PC_DATA_DIR"] = self.tmp
        self.orig = common.http_get
        self.http = common.http_get = FakeHttp()

    def tearDown(self):
        common.http_get = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_probe_picks_earliest_game_drops_bad_key_and_logs_spend(self):
        now = common.parse_iso("2026-10-30T16:45:00-04:00")
        pool = odds.Pool(["secretkey123"], now)
        client = odds.Client(pool, common.load_config())
        out, pairs = bp.run_probe(now, common.load_config(), pool, client)
        self.assertEqual(out["game"]["event_id"], "early")
        self.assertEqual(out["markets_rejected"], ["player_rush_longest"])
        self.assertEqual(out["credits_spent"], 10)                    # 2 markets returned x 5 regions
        self.assertLessEqual(out["credits_spent"], bp.CAP)
        self.assertEqual(out["per_market"][YDS]["hr_pin_4plus_players"], 1)
        self.assertEqual(sum(e["cost"] for e in pool.spent if e["pull"] == "P"), 10)
        self.assertEqual(pool.state[0]["left"], 490)
        self.assertTrue(all(len(p) == 6 for p in pairs) and pairs)
        bp.write_results(out, pairs)
        self.assertEqual(common.read_json(bp.path())["credits_spent"], 10)
        self.assertEqual(len(common.read_json(bp.path())["markets_asked"]), 14)
        self.assertNotIn("secretkey123", common.dumps(common.read_json(bp.path())))

    def test_cap_stops_calls(self):
        now = common.parse_iso("2026-10-30T16:45:00-04:00")
        pool = odds.Pool(["secretkey123"], now)
        client = odds.Client(pool, common.load_config())
        out, _ = bp.run_probe(now, common.load_config(), pool, client, {"cap": 30})
        calls = [c for c in self.http.calls if not c[0].endswith("/events")]
        self.assertTrue(all(len(c[1]["markets"][0].split(",")) * 5 <= 30 for c in calls))
        self.assertLessEqual(out["credits_spent"], 30)


if __name__ == "__main__":
    unittest.main()
