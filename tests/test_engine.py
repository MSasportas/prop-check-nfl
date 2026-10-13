"""The engine: the edge arithmetic, the Sharp's entry rules in order (judge), caps, parlays, pulls, the config guard,
and the page files (slate.json / research / rules.json) against the UX contract."""
import copy
import json
import os
import unittest

import helpers
from helpers import SEA_DEN, WILSON, JSN, World
import common
import engine
import odds

CFG = helpers.cfg()
V = CFG["values"]


class Edge(unittest.TestCase):
    def test_numbers(self):
        e = engine.edge(0.6844, "under", "rec", "FG", -110, -120, V)
        self.assertAlmostEqual(e["fair"], odds.fair_two_sided(-110, -120))
        self.assertAlmostEqual(e["model_adj"], 0.6844 - V["shrink"]["rec|FG|U"])
        self.assertAlmostEqual(e["p"], V["model_weight"] * e["model_adj"] + V["market_weight"] * e["fair"])
        self.assertAlmostEqual(e["gap"], e["model_adj"] - e["fair"])           # gap_uses = model_adj
        self.assertAlmostEqual(e["margin"], e["p"] - odds.break_even(-110))

    def test_jump_penalty_only_on_overs(self):
        a = engine.edge(0.7, "over", "rec", "FG", -110, -110, V, jump=True)
        b = engine.edge(0.7, "over", "rec", "FG", -110, -110, V)
        self.assertAlmostEqual(b["model_adj"] - a["model_adj"], V["jump_penalty"])
        c = engine.edge(0.7, "under", "rec", "FG", -110, -110, V, jump=True)
        d = engine.edge(0.7, "under", "rec", "FG", -110, -110, V)
        self.assertEqual(c["model_adj"], d["model_adj"])

    def test_qualifies_in_order(self):
        e = engine.edge(0.75, "under", "rec", "FG", -110, -110, V)
        self.assertEqual(engine.qualifies(e, -210, "early", V), (False, "price outside -200..+%d" % V["max_price"]))
        self.assertEqual(engine.qualifies(e, 130, "early", V)[0], False)
        low = engine.edge(0.60, "under", "rec", "FG", -110, -110, V)
        self.assertEqual(engine.qualifies(low, -110, "inactives", V), (False, "model chance under the minimum"))
        big = engine.edge(0.80, "under", "rec", "FG", -110, -110, V)                 # gap 0.24 > too_good
        self.assertEqual(engine.qualifies(big, -110, "inactives", V), (False, "too good: assume the book knows"))
        mid = engine.edge(0.72, "under", "rec", "FG", -110, -110, V)                 # gap 0.16 > news_gap
        self.assertEqual(engine.qualifies(mid, -110, "early", V), (False, "big gap waits for the injury report"))
        self.assertTrue(engine.qualifies(mid, -110, "injury_report", V)[0])

    def test_margins_by_settledness(self):
        self.assertEqual(engine.margin_need("early", V), V["margin_early"])
        self.assertEqual(engine.margin_need("injury_report", V), V["margin_injury"])
        self.assertEqual(engine.margin_need("inactives", V), V["margin_inactives"])
        self.assertEqual(engine.margin_need("early", V, 0.03), V["margin_early"] + 0.03)

    def test_gate_is_inclusive(self):
        self.assertTrue(engine.inside_gate(-200, V))
        self.assertFalse(engine.inside_gate(-201, V))
        self.assertTrue(engine.inside_gate(V["max_price"], V))
        self.assertFalse(engine.inside_gate(V["max_price"] + 1, V))


class ConfigGuard(unittest.TestCase):
    def test_customer_rules_cannot_be_configured_away(self):
        engine.check_config(CFG)
        for k, val in (("price_gate", -250), ("parlay_leg_min_price", -220), ("parlay_max_legs", 4),
                       ("parlay_min_legs", 1)):
            c = copy.deepcopy(CFG)
            c["values"][k] = val
            with self.assertRaises(engine.ConfigError, msg=k):
                engine.check_config(c)


class Wording(unittest.TestCase):
    def test_bet_and_reason(self):
        self.assertEqual(engine.bet_text("under", 1.5, "rec", CFG), "Under 1.5 receptions")
        self.assertEqual(engine.bet_text("over", 30.5, "rush_yds_1h", CFG), "Over 30.5 rushing yards")
        self.assertEqual(engine.bet_text("over", 0.5, "anytime_td", CFG), "Scores a TD")
        r = engine.reason_text(["7.4 catches a game his last 5 (3 of 5 over 6.5)", "DEN allows the 4th fewest catches to WRs"])
        self.assertEqual(r, "7.4 catches a game his last 5 (3 of 5 over 6.5). DEN allows the 4th fewest catches to WRs.")
        long = engine.reason_text(["x" * 100, "y" * 40], 120)
        self.assertEqual(long, "X" + "x" * 99 + ".")                                # the second clause is dropped
        self.assertNotIn("—", engine.reason_text(["a — b"]))

    def test_ids_follow_the_contract(self):
        self.assertEqual(engine.pick_id("2026-W06", SEA_DEN, WILSON, "rec", "FG", 1.5, "under"),
                         "2026-W06:2026_06_SEA_DEN:00-0038797:rec:FG:1.5:under")
        self.assertTrue(engine.parlay_id("2026-W06", ["a", "b"]).startswith("2026-W06:P:"))
        self.assertEqual(engine.parlay_id("2026-W06", ["a", "b"]), engine.parlay_id("2026-W06", ["b", "a"]))


class Caps(unittest.TestCase):
    def cand(self, i, pid, team, gid, p, mkey="rec"):
        return {"id": i, "pid": pid, "team": team, "game_id": gid, "mkey": mkey, "settled": "early",
                "e": {"p": p, "b": 0.5}}

    def test_one_per_player_two_per_team_three_per_game(self):
        c = [self.cand("a", "p1", "SEA", "g", 0.70), self.cand("b", "p1", "SEA", "g", 0.69, "rec_yds"),
             self.cand("c", "p2", "SEA", "g", 0.68), self.cand("d", "p3", "SEA", "g", 0.67),
             self.cand("e", "p4", "DEN", "g", 0.66), self.cand("f", "p5", "DEN", "g", 0.65)]
        out = [x["id"] for x in engine.apply_caps(c, V, {})]
        self.assertEqual(out, ["a", "c", "e"])
        # a pulled player counts toward his 1 per player
        out = [x["id"] for x in engine.apply_caps(c, V, {}, pulled_players={"p1"})]
        self.assertEqual(out, ["c", "d", "e"])


class OnTheRealPayload(unittest.TestCase):
    """judge() on SEA at DEN with the real ESPN injuries (Oct 13)."""

    def setUp(self):
        self.w = World()

    def tearDown(self):
        self.w.close()

    def explain(self, now="2026-10-13T10:05"):
        self.w.at(now)
        import run
        r = run.Run(CFG, common.now(), readonly=True)
        r.load_schedule()
        return engine.explain(r.build_inputs())

    def test_verdicts(self):
        rows = self.explain()
        by = {(x["player"], x["market"], x["side"]): x["verdict"] for x in rows}
        self.assertEqual(by[("Emanuel Wilson", "rec", "under")], "QUALIFIES")
        self.assertEqual(by[("Jaxon Smith-Njigba", "rec", "over")], "listed questionable")
        self.assertTrue(by[("Sam Darnold", "pass_att", "over")].startswith("market not picked"))
        self.assertEqual(by[("Sam Darnold", "pass_int", "over")], "this side is never picked")
        self.assertTrue(by[("Emanuel Wilson", "anytime_td", "over")].startswith("market not picked"))
        self.assertEqual(by[("Troy Franklin", "rec", "under")], "big gap waits for the injury report")
        self.assertTrue(by[("Troy Franklin", "rec", "over")].startswith("eligibility: role_unstable"))
        # every verdict is one of the known rules (no surprises in explain output)
        known = ("QUALIFIES", "listed", "market not picked", "this side", "eligibility", "model chance", "chance under",
                 "margin too small", "price outside", "too good", "big gap", "feed check", "one-sided", "missed his",
                 "starting QB", "wind", "no model", "Hard Rock has no", "player not on")
        for x in rows:
            self.assertTrue(x["verdict"].startswith(known), x)

    def test_wind_blocks_passing_overs_outdoors(self):
        self.w.at("2026-10-13T10:05")
        helpers.write(self.w.path("state", "weather.json"),
                      {"games": {SEA_DEN: {"roof": "outdoors", "wind_mph": 22, "at": "2026-10-14T09:00:00-04:00"}}})
        import run
        r = run.Run(CFG, common.parse_iso("2026-10-14T09:00:00-04:00"), readonly=True)
        r.load_schedule()
        inp = r.build_inputs()
        g = inp.games[SEA_DEN]
        self.assertEqual(inp.wind_blocks(g, "pass_yds", "over"), "wind 22 mph at kickoff")
        self.assertIsNone(inp.wind_blocks(g, "pass_yds", "under"))
        self.assertIsNone(inp.wind_blocks(g, "rush_yds", "over"))

    def test_qb_out_blocks_team_overs(self):
        self.w.set_injury(26, "Sam Darnold", "Out", pos="QB", espn_id="3912547")
        rows = self.explain()
        sea_overs = [x for x in rows if x["team"] == "SEA" and x["side"] == "over" and
                     x["verdict"].startswith("starting QB")]
        self.assertTrue(sea_overs)
        self.assertEqual(sea_overs[0]["verdict"], "starting QB is out: no overs")


class PageFiles(unittest.TestCase):
    """slate.json, research and rules.json shapes against /home/claude/work/ux/CONTRACT.md v1."""

    def setUp(self):
        self.w = World()
        self.s = self.w.at("2026-10-13T10:05")

    def tearDown(self):
        self.w.close()

    def test_slate_shape(self):
        s = self.s
        for k in ("season", "week", "label", "generated_at", "valid_until", "next_check", "status", "banners", "empty",
                  "games", "picks", "parlays"):
            self.assertIn(k, s)
        self.assertEqual(set(s["status"]), {"odds", "injuries", "inactives", "stats", "pool", "last_run"})
        self.assertEqual(set(s["status"]["pool"]), {"credits_left", "keys", "keys_live", "days_left", "low", "empty"})
        self.assertEqual(s["valid_until"], "2026-10-20T06:00:00-04:00")
        g = s["games"][0]
        self.assertEqual(set(g), {"id", "away", "home", "kickoff", "day", "roof", "started", "final", "score", "settled",
                                  "props_state", "props_at", "lines_at", "note", "picks"})
        p = s["picks"][0]
        want = {"id", "rank", "game_id", "day", "kickoff", "player", "player_short", "player_id", "pos", "team", "opp",
                "home", "market", "period", "side", "line", "bet", "book", "price", "price_at", "first_price",
                "first_price_at", "grade_price", "p", "breakeven", "p_pct", "breakeven_pct", "reason", "settled",
                "state", "thin", "pulled", "graded", "history"}
        self.assertEqual(set(p), want)
        self.assertIsInstance(p["price"], int)
        raw = json.dumps(s)
        self.assertNotIn("—", raw)
        self.assertNotIn("fixture", s)
        for g in s["games"]:
            if g["props_state"] != "ok":
                self.assertTrue(g["note"])

    def test_research_shape(self):
        idx = helpers.load(self.w.path("research", "index.json"))
        self.assertEqual(idx["player_cols"], ["id", "name", "pos", "team", "game_id", "lines"])
        gi = next(g for g in idx["games"] if g["id"] == SEA_DEN)
        self.assertEqual(gi["spread"], {"team": "SEA", "line": -2.5})
        self.assertEqual(gi["total"], 41.5)
        self.assertEqual(gi["file"], "research/%s.json" % SEA_DEN)
        self.assertGreater(gi["props"], 50)
        doc = helpers.load(self.w.path("research", "%s.json" % SEA_DEN))
        kinds = {(l["kind"], l["period"], l["book"]) for l in doc["lines"]}
        self.assertIn(("spread", "1H", "DraftKings"), kinds)
        self.assertIn(("spread", "FG", "Hard Rock"), kinds)
        self.assertIn(("moneyline", "FG", "Hard Rock"), kinds)
        self.assertNotIn(("spread", "1H", "Hard Rock"), kinds)
        pl = next(p for p in doc["players"] if p["id"] == JSN)
        self.assertEqual(pl["status"], "Questionable")
        self.assertTrue(pl["lines"])
        self.assertEqual(set(pl["log"][0]), {"season", "week", "opp", "home", "played", "snap_pct", "fg", "h1"})
        self.assertEqual(set(pl["log"][0]["fg"]), set(["pass_att", "pass_cmp", "pass_yds", "pass_td", "pass_int",
                                                       "rush_att", "rush_yds", "targets", "rec", "rec_yds", "long_rec",
                                                       "long_rush", "td"]))
        self.assertLessEqual(len(pl["log"]), 10)
        w = next(p for p in doc["players"] if p["id"] == WILSON)
        self.assertEqual(w["picks"], ["2026-W06:2026_06_SEA_DEN:00-0038797:rec:FG:1.5:under"])
        self.assertTrue(doc["defense"]["DEN"])
        self.assertIn(doc["injuries"]["state"], ("ok", "not_yet", "down"))
        self.assertIn("Zach Charbonnet", [x["player"] for x in doc["injuries"]["list"]])

    def test_rules_text(self):
        r = helpers.load(self.w.path("rules.json"))
        self.assertIn("min_sample", r["values"])
        self.assertTrue(all("—" not in t for t in r["text"]))
        self.assertTrue(r["text"][0].startswith("Every pick needs a real Hard Rock price of -200 or better"))


if __name__ == "__main__":
    unittest.main()
