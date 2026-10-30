"""The engine: the model's guard input, the config guard, wording, the NFL pre-price vetoes on the real payload (with a synthetic
consensus), the caps, and the page files (slate.json / rules.json) against docs/SPEC.md."""
import copy
import json
import os
import unittest

import helpers
from helpers import SEA_DEN, WILSON, JSN, World, add_books
import common
import engine
import gg
import odds

CFG = helpers.cfg()
V = CFG["values"]
T = gg.T(CFG)


class ModelSide(unittest.TestCase):
    """The guard's input: the model's side chance minus the DS shrink (minus the jump penalty on a role-jump over)."""

    def mm(self, p_over, jump=False):
        return {"p_over": p_over, "jump": jump}

    class Inp:
        v = V
        cfg = CFG

    def test_shrink_and_side(self):
        i = self.Inp()
        self.assertAlmostEqual(engine.model_side(i, self.mm(0.30), "under", "rec"), 0.70 - V["shrink"]["rec|FG|U"])
        self.assertAlmostEqual(engine.model_side(i, self.mm(0.70), "over", "rec"), 0.70 - V["shrink"]["rec|FG|O"])

    def test_jump_penalty_only_on_overs(self):
        i = self.Inp()
        a = engine.model_side(i, self.mm(0.7, True), "over", "rec")
        b = engine.model_side(i, self.mm(0.7), "over", "rec")
        self.assertAlmostEqual(b - a, V["jump_penalty"])
        self.assertEqual(engine.model_side(i, self.mm(0.3, True), "under", "rec"),
                         engine.model_side(i, self.mm(0.3), "under", "rec"))

    def test_clamped(self):
        i = self.Inp()
        self.assertEqual(engine.model_side(i, self.mm(0.0), "over", "rec"), 0.001)
        self.assertEqual(engine.model_side(i, self.mm(1.0), "under", "rec"), 0.001)


class ConfigGuard(unittest.TestCase):
    def test_customer_rules_cannot_be_configured_away(self):
        engine.check_config(CFG)
        for key, val in (("price_gate", -151), ("price_gate", -250), ("books_min", 3), ("price_gate", 100),
                         ("max_price", 99)):
            c = copy.deepcopy(CFG)
            c["ops"]["gap_guard"]["thresholds"][key] = val
            with self.assertRaises(engine.ConfigError, msg=(key, val)):
                engine.check_config(c)
        c = copy.deepcopy(CFG)
        c["ops"]["gap_guard"]["thresholds"]["flag_pulls"] = ["R", "C", "F"]
        with self.assertRaises(engine.ConfigError):
            engine.check_config(c)
        c = copy.deepcopy(CFG)
        c["ops"]["gap_guard"]["thresholds"]["model_weight"] = 0.5
        with self.assertRaises(engine.ConfigError):
            engine.check_config(c)

    def test_the_customer_rules_are_in_the_config(self):
        self.assertEqual((T["price_gate"], T["max_price"], T["gap_ev"], T["books_min"], T["pin_ev_min"], T["final_ev"]),
                         (-150, 120, 0.03, 4, 0.0, 0.02))
        self.assertEqual((T["market_weight"], T["model_weight"], T["flag_pulls"]), (0.8, 0.2, ["R", "C"]))
        self.assertEqual(gg.market_names(CFG), ["rec", "rec_yds", "rush_yds", "rush_att", "pass_yds", "pass_td"])
        self.assertEqual(odds.pick_keys(CFG), ["player_receptions", "player_reception_yds", "player_rush_yds",
                                               "player_rush_attempts", "player_pass_yds", "player_pass_tds"])
        self.assertEqual(CFG["ops"]["gap_guard"]["live_from"], "2026-W08")
        self.assertEqual(CFG["ops"]["safety_pause"]["units"], -10)

    def test_nothing_obsolete_is_left(self):
        raw = json.dumps(CFG)
        for gone in ("parlay", "min_model", "\"min_p\"", "margin_early", "news_gap", "too_good", "rank_by", "pause_legs",
                     "board_", "full_plus", "pull_full_keys", "display_only", "alt_lines_picks", "_1h",
                     "anytime_td", "player_pass_interceptions", "spreads", "alt_key"):
            self.assertNotIn(gone, raw, gone)
        for m in CFG["ops"]["markets"].values():
            self.assertEqual(m["period"], "FG")
            self.assertTrue(m["picks"])


class Wording(unittest.TestCase):
    def test_bet_and_reason(self):
        self.assertEqual(engine.bet_text("under", 1.5, "rec", CFG), "Under 1.5 receptions")
        self.assertEqual(engine.bet_text("over", 30.5, "rush_yds", CFG), "Over 30.5 rushing yards")
        r = engine.reason_text(["7.4 catches a game his last 5 (3 of 5 over 6.5)", "DEN allows the 4th fewest catches to WRs"])
        self.assertEqual(r, "7.4 catches a game his last 5 (3 of 5 over 6.5). DEN allows the 4th fewest catches to WRs.")
        long = engine.reason_text(["x" * 100, "y" * 40], 120)
        self.assertEqual(long, "X" + "x" * 99 + ".")                                # the second clause is dropped
        self.assertNotIn("—", engine.reason_text(["a — b"]))
        self.assertNotIn("chance", " ".join(engine.reason_text(["3.0 catches a game his last 5"]).split()))

    def test_ids_include_slate_game_player_market_line_side(self):
        self.assertEqual(gg.pick_id("2026-W08", "g1", WILSON, "rec_yds", 9.5, "under"),
                         "2026-W08:g1:00-0038797:rec_yds:9.5:under")
        self.assertNotEqual(gg.pick_id("2026-W08", "g1", WILSON, "rec_yds", 9.5, "under"),
                            gg.pick_id("2026-W08", "g1", WILSON, "rec_yds", 10.5, "under"))


class Caps(unittest.TestCase):
    def test_one_per_player_and_a_line_move_pull_does_not_count(self):
        mine = [{"pid": "p1", "game_id": "g", "team": "SEA"}]
        self.assertEqual(gg.cap_reason({"pid": "p1"}, mine, T, "g", "SEA"), "player")
        self.assertIsNone(gg.cap_reason({"pid": "p2"}, mine, T, "g", "SEA"))
        self.assertIsNone(gg.cap_reason({"pid": "p2"}, mine * 5, T, "g", "SEA"), "no team or game cap in v2.0")
        items = {}
        self.assertEqual(engine._count_toward_caps(items, "2026-W08", CFG), [])

    def test_team_and_game_caps_work_when_configured(self):
        t = dict(T, max_per_team=1, max_per_game=2)
        mine = [{"pid": "p1", "game_id": "g", "team": "SEA"}]
        self.assertEqual(gg.cap_reason({"pid": "p2"}, mine, t, "g", "SEA"), "team")
        mine.append({"pid": "p3", "game_id": "g", "team": "DEN"})
        self.assertEqual(gg.cap_reason({"pid": "p4"}, mine, dict(t, max_per_team=2), "g", "DEN"), "game")


class OnTheRealPayload(unittest.TestCase):
    """pre_check / explain on SEA at DEN with the real ESPN injuries (Oct 13) and a synthetic consensus."""

    def setUp(self):
        self.w = World()
        self.w.set_odds(SEA_DEN, add_books(self.w.sea_den, {("player_reception_yds", "Emanuel Wilson", "under"): 0.04}),
                        "2026-10-14T21:00:00Z")

    def tearDown(self):
        self.w.close()

    def explain(self, now="2026-10-14T17:10"):
        self.w.at(now)
        import run
        r = run.Run(helpers.cfg(), common.now(), readonly=True)
        r.load_schedule()
        return engine.explain(r.build_inputs(), r.by_game())

    def test_verdicts(self):
        rows = self.explain()
        by = {(x["player"], x["market"], x["side"]): x["verdict"] for x in rows}
        self.assertTrue(rows)
        self.assertEqual(set(x["market"] for x in rows), set(gg.market_names(CFG)))
        self.assertTrue(by[("Emanuel Wilson", "rec_yds", "under")].startswith("PASSES") or
                        by[("Emanuel Wilson", "rec_yds", "under")].startswith("blocked: eligibility"))
        self.assertEqual(by[("Jaxon Smith-Njigba", "rec", "over")], "blocked: listed questionable")
        for x in rows:
            self.assertFalse(x["verdict"].startswith("PASSES") and x["gap_ev"] < 0.03, x)
        # every verdict is one of the known rules (no surprises in explain output)
        known = ("PASSES", "no gap", "blocked: ", "vetoed by our model", "no model", "feed check", "team excluded",
                 "waits for")
        for x in rows:
            self.assertTrue(x["verdict"].startswith(known), x)

    def test_planted_pick_is_blocked_when_he_is_listed_questionable(self):
        self.w.set_injury(26, "Emanuel Wilson", "Questionable", pos="RB", espn_id="4887558")
        rows = self.explain()
        row = next(x for x in rows if x["player"] == "Emanuel Wilson" and x["market"] == "rec_yds" and x["side"] == "under")
        self.assertEqual(row["verdict"], "blocked: listed questionable")

    def test_wind_blocks_passing_overs_outdoors(self):
        self.w.at("2026-10-13T10:05")
        helpers.write(self.w.path("state", "weather.json"),
                      {"games": {SEA_DEN: {"roof": "outdoors", "wind_mph": 22, "at": "2026-10-14T09:00:00-04:00"}}})
        import run
        r = run.Run(helpers.cfg(), common.parse_iso("2026-10-14T09:00:00-04:00"), readonly=True)
        r.load_schedule()
        inp = r.build_inputs()
        g = inp.games[SEA_DEN]
        self.assertEqual(inp.wind_blocks(g, "pass_yds", "over"), "wind 22 mph at kickoff")
        self.assertIsNone(inp.wind_blocks(g, "pass_yds", "under"))
        self.assertIsNone(inp.wind_blocks(g, "rush_yds", "over"))

    def test_qb_out_blocks_team_overs(self):
        self.w.set_injury(26, "Sam Darnold", "Out", pos="QB", espn_id="3912547")
        rows = self.explain()
        blocked = [x for x in rows if x["team"] == "SEA" and x["side"] == "over" and
                   x["verdict"] == "blocked: starting QB is out: no overs"]
        self.assertTrue(blocked)


class PageFiles(unittest.TestCase):
    """slate.json and rules.json shapes against docs/SPEC.md (schema 2)."""

    def setUp(self):
        self.w = World()
        self.w.set_odds(SEA_DEN, add_books(self.w.sea_den, {("player_rush_attempts", "Emanuel Wilson", "under"): 0.04}),
                        "2026-10-14T21:00:00Z")
        self.s = self.w.at("2026-10-14T17:10")

    def tearDown(self):
        self.w.close()

    def test_slate_shape(self):
        s = self.s
        for k in ("schema", "era", "season", "week", "label", "generated_at", "status_at", "valid_until", "next_check",
                  "status", "banners", "empty", "games", "picks"):
            self.assertIn(k, s)
        for gone in ("parlays", "research", "toggle", "h1"):
            self.assertNotIn(gone, s)
        self.assertEqual((s["schema"], s["era"]), (2, "gg1"))
        self.assertEqual(set(s["status"]), {"odds", "injuries", "inactives", "stats", "pool", "last_run"})
        self.assertEqual(set(s["status"]["pool"]), {"credits_left", "keys", "keys_live", "keys_shared", "days_left", "low", "empty"})
        g = s["games"][0]
        self.assertEqual(set(g), {"id", "away", "home", "kickoff", "day", "roof", "started", "final", "score", "settled",
                                  "props_state", "props_at", "note", "picks"})
        self.assertEqual(len(s["picks"]), 1)
        p = s["picks"][0]
        want = {"id", "era", "rank", "state", "game_id", "day", "kickoff", "game", "player", "player_short", "player_id",
                "pos", "team", "opp", "home", "market", "market_label", "side", "line", "bet", "book", "price", "read_at",
                "read_pull", "vs", "gap_pct", "n_books", "pinnacle", "final_ev_pct", "reason", "settled", "settled_text",
                "flags", "pulled", "graded", "history", "method", "pin_solo"}
        self.assertEqual(set(p), want)
        self.assertEqual((p["method"], p["pin_solo"]), ("gap_guard", None))
        self.assertEqual(s["methods"], {"gap_guard": {"era": "gg1", "live": True}, "pin_solo": {"era": "ps1", "live": False}})
        self.assertEqual(set(p["flags"]), {"price_final", "weather"})
        self.assertIsInstance(p["price"], int)
        self.assertEqual(p["market_label"], "Carries")
        raw = json.dumps(s)
        self.assertNotIn("—", raw)
        for term in ("parlay", "Early", "1st half", "our chance"):
            self.assertNotIn(term, raw)

    def test_rules_text(self):
        r = helpers.load(self.w.path("rules.json"))
        self.assertTrue(all("—" not in t for t in r["text"]))
        self.assertTrue(r["text"][0].startswith("Every pick is a full game player prop at Hard Rock (Florida), main line only"))
        self.assertIn("-150", r["text"][0])
        self.assertNotIn("parlay", " ".join(r["text"][1:]).lower())
        self.assertEqual(r["gap_guard"]["live_from"], helpers.cfg()["ops"]["gap_guard"]["live_from"])


if __name__ == "__main__":
    unittest.main()
