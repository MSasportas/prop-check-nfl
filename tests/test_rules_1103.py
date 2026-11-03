"""The rules shipped 2026-11-03 (Matt: "A, let's get this idea live by Thursday"): the Pinnacle-moves rule is the live pick
method, reads are timed off kickoff (R 24 h, W 3 h, C 70 min before), and a pick needs Pinnacle's de-vigged chance to move
1 to under 5 pts toward a side between two reads with Hard Rock at or better than Pinnacle's POSTED price on that side.

SEA at DEN (Thu Oct 15, 8:15 PM ET): R Wed ~8:20 PM, W Thu ~5:20 PM, C Thu ~7:05 PM."""
import os
import unittest

import helpers
from helpers import SEA_DEN, WILSON, World
import common
import watch
from test_watch import build, inactives, events

R_AT, W_AT, C_AT = "2026-10-14T20:20", "2026-10-15T17:20", "2026-10-15T19:05"
LU_R, LU_W, LU_C = "2026-10-15T00:10:00Z", "2026-10-15T21:10:00Z", "2026-10-15T23:00:00Z"


def cfg():
    c = helpers.cfg_1103()
    c["values"]["elig"]["role_cv_blocks"] = "over"        # the world's Wilson has a jumpy role (see helpers.legacy_role_cfg)
    return c


class Shipped(unittest.TestCase):
    def test_shipped_values(self):
        c = helpers.shipped()
        o = c["ops"]
        self.assertIs(o["pin_solo"]["live"], True)
        t = o["watch"]["tracks"]["pin_solo"]
        self.assertEqual((t["min_move"], t["max_move"], t["price_test"], t["others_min"], t["others_max"]),
                         (0.02, 0.05, "fair", 0, None))
        self.assertGreater(t["min_ev_vs_pin"], 0)                 # Hard Rock must BEAT Pinnacle's fair, not tie it
        self.assertIs(t["skip_whole_lines"], True)
        self.assertEqual((o["pulls"]["r_before_min"], o["pulls"]["w_target_min"], o["pulls"]["c_target_min"]), (1440, 180, 70))
        self.assertEqual(o["tiers"]["full"], 18)
        g = o["gap_guard"]
        self.assertEqual((c["values"].get("price_gate", g.get("price_gate")),), (-150,)) if "price_gate" in c["values"] else None


class Live(unittest.TestCase):
    def run_w(self, pin, others=0.0, hr_under=105):
        w = World(pool=(500, 500, 500, 500), config=cfg())
        build(w, hr_under=hr_under, lu=LU_R)
        w.at(R_AT)
        build(w, pin=pin, others=others, hr_under=hr_under, lu=LU_W)
        s = w.at(W_AT)
        return w, s

    def shown(self, w, book="hardrock"):
        return [e for e in w.events() if e["t"] == "shown" and (book is None or e["pick"].get("book_key", "hardrock") == book)]

    def cand(self, w):
        return [e for e in events(w) if e["t"] == "cand" and e["track"] == "pin_solo" and e["pid"] == WILSON
                and e["market"] == "rush_att"]

    def test_reads_r_then_w_on_kickoff_time(self):
        w, s = self.run_w(-0.03)
        try:
            led = common.read_json(w.path("state", "credits.json"))
            self.assertEqual([e["pull"] for e in led["spent"] if e["game_id"] == SEA_DEN], ["R", "W"])
        finally:
            w.close()

    def test_three_pt_move_hard_rock_beating_pinnacles_fair_is_a_pick_at_w(self):
        w, s = self.run_w(-0.03)
        try:
            sh = self.shown(w)
            self.assertEqual(len(sh), 1)
            b = sh[0]["pick"]
            self.assertEqual((b["method"], b["era"], b["player_id"], b["side"], b["line"], b["price"], b["read_pull"]),
                             ("pin_solo", "ps1", WILSON, "under", 17.5, 105, "W"))
            self.assertRegex(b["reason"], r"^Pinnacle moved 3 pts toward Under 17\.5 carries since yesterday; Hard Rock is "
                                          r"still at \+105, better than Pinnacle's fair [+-]\d+\.$")
            p = [x for x in s["picks"] if x["state"] == "open"][0]
            self.assertEqual(p["pin_solo"]["pin_price"], b["pin_price"])
            self.assertRegex(p["vs"], r"^Pinnacle moved \+3 pts · Pinnacle fair [+-]\d+, Hard Rock \+105$")
        finally:
            w.close()

    def test_the_other_books_moving_too_does_not_block_it(self):
        w, _ = self.run_w(-0.03, others=-0.03)
        try:
            self.assertEqual(len(self.shown(w)), 1)
        finally:
            w.close()

    def test_a_five_pt_move_is_too_big(self):
        w, _ = self.run_w(-0.06)
        try:
            self.assertEqual(self.shown(w), [])
            self.assertEqual([c["gate"] for c in self.cand(w)], ["too_big"])
        finally:
            w.close()

    def test_a_one_and_a_half_pt_move_is_not_enough(self):
        w, _ = self.run_w(-0.015)
        try:
            self.assertEqual(self.shown(w), [])
            self.assertEqual(self.cand(w), [])
        finally:
            w.close()

    def test_under_one_pt_is_nothing(self):
        w, _ = self.run_w(-0.005)
        try:
            self.assertEqual(self.shown(w), [])
            self.assertEqual(self.cand(w), [])
        finally:
            w.close()

    def test_hard_rock_not_beating_pinnacles_fair_price_is_not_a_pick(self):
        w = World(pool=(500, 500, 500, 500), config=cfg())
        try:
            build(w, hr_under=105, lu=LU_R)
            w.at(R_AT)
            build(w, pin=-0.03, hr_under=105, lu=LU_W)                     # Pinnacle's under is now about -104
            w.set_price(SEA_DEN, "player_rush_attempts", "Emanuel Wilson", "under", -110, last_update_z=LU_W)   # Hard Rock only
            w.at(W_AT)
            self.assertEqual(self.shown(w), [])
            self.assertEqual(len(self.shown(w, "draftkings")), 1)              # DraftKings still beats Pinnacle's fair: its own pick
            self.assertEqual([c["gate"] for c in self.cand(w)], ["worse_than_fair"])
        finally:
            w.close()

    def test_pick_survives_c_and_locks_into_its_own_record_line(self):
        w, _ = self.run_w(-0.03)
        try:
            inactives(w)
            build(w, pin=-0.03, others=0.0, hr_under=105, lu=LU_C)
            s = w.at(C_AT)
            self.assertEqual(len(self.shown(w)), 1)                       # C adds nothing for the same player
            p = [x for x in s["picks"] if x["method"] == "pin_solo" and x["book_key"] == "hardrock"][0]
            self.assertEqual((p["state"], p["settled_text"]), ("open", "Inactives confirmed"))
            self.assertEqual(common.read_json(w.path("record.json"))["weeks"], [])     # nothing in gg1
        finally:
            w.close()


class DraftKings(unittest.TestCase):
    """DraftKings (Matt 2026-11-03, for someone out of state): same move, judged on DraftKings' own price; both books show
    when both qualify; one Record entry per book (era pd1, own line)."""

    def run_w(self, pin, others=0.0, config=None):
        w = World(pool=(500, 500, 500, 500), config=config or cfg())
        build(w, hr_under=105, lu=LU_R)
        w.at(R_AT)
        build(w, pin=pin, others=others, hr_under=105, lu=LU_W)
        return w, w.at(W_AT)

    def test_both_books_qualify_both_show_one_entry_each(self):
        w, s = self.run_w(-0.03)
        try:
            sh = [e for e in w.events() if e["t"] == "shown"]
            self.assertEqual(sorted((e["pick"]["book"], e["pick"]["era"], e["id"][-6:]) for e in sh),
                             [("DraftKings", "pd1", ":ps:dk"), ("Hard Rock", "ps1", "der:ps")])
            dk = next(p for p in s["picks"] if p["book_key"] == "draftkings")
            self.assertEqual(dk["book"], "DraftKings")
            self.assertTrue(dk["link"]["url"].startswith("https://sportsbook.draftkings.com"))
            self.assertIn("DraftKings is still at", dk["reason"])
            w.at("2026-10-15T20:30")                                   # kicked off: both lock onto the Record
            rec = common.read_json(w.path("record.json"))
            self.assertIsNotNone(rec["draftkings"])
            self.assertIsNotNone(rec["pin_solo"])
            self.assertEqual([e["book"] for wk in rec["draftkings"]["weeks"] for e in wk["entries"]], ["DraftKings"])
            self.assertEqual([e["book"] for wk in rec["pin_solo"]["weeks"] for e in wk["entries"]], ["Hard Rock"])
            self.assertEqual(rec["draftkings"]["era"], "pd1")
            self.assertEqual(rec["weeks"], [])                         # never in gg1
        finally:
            w.close()

    def test_other_books_moving_with_pinnacle_means_no_draftkings_pick(self):
        w, s = self.run_w(-0.03, others=-0.03)                       # DraftKings moved too: no longer beats the fair
        try:
            self.assertEqual([e["pick"]["book"] for e in w.events() if e["t"] == "shown"], ["Hard Rock"])
        finally:
            w.close()

    def test_draftkings_off_makes_no_draftkings_pick(self):
        c = cfg()
        c["ops"]["draftkings"]["live"] = False
        w, s = self.run_w(-0.03, config=c)
        try:
            self.assertEqual([e["pick"]["book"] for e in w.events() if e["t"] == "shown"], ["Hard Rock"])
            self.assertFalse(s["methods"]["draftkings"]["live"])
        finally:
            w.close()

    def test_draftkings_moving_its_line_pulls_only_its_pick(self):
        w, s = self.run_w(-0.03)
        try:
            inactives(w)
            js = helpers.load(os.path.join(w.odds, "odds_%s.json" % SEA_DEN))
            for b in js["bookmakers"]:
                if b["key"] == "draftkings":
                    for m in b["markets"]:
                        for o in m["outcomes"]:
                            if o["description"] == "Emanuel Wilson" and m["key"] == "player_rush_attempts":
                                o["point"] = 16.5
                    b["last_update"] = LU_C
                    for m in b["markets"]:
                        m["last_update"] = LU_C
            w.set_odds(SEA_DEN, js, LU_C)
            w.at(C_AT)
            pulled = [e for e in w.events() if e["t"] == "pulled"]
            self.assertEqual(len(pulled), 1)
            self.assertTrue(pulled[0]["id"].endswith(":ps:dk"))
            self.assertEqual(pulled[0]["code"], "line_moved")
            self.assertIn("DraftKings", pulled[0]["if_bet"])
        finally:
            w.close()

    def test_bet_link_prefers_the_betslip_and_fills_the_state(self):
        c = cfg()
        f = {"props": {"player_receptions": {"outcomes": [{"pid": "p", "side": "over", "line": 4.5,
                                                           "link": "https://hr.example/{state}/slip/1"}]}},
             "others": {"draftkings": {"player_receptions": {"outcomes": [
                 {"pid": "p", "side": "over", "line": 4.5, "price": -110, "link": "https://dk.example/{state}/slip/9"}]}}},
             "book_links": {"draftkings": "https://dk.example/event/5"}}
        self.assertEqual(watch.bet_link(f, "hardrock", "player_receptions", "p", 4.5, "over", c),
                         {"url": "https://hr.example/fl/slip/1", "kind": "betslip"})
        self.assertEqual(watch.bet_link(f, "draftkings", "player_receptions", "p", 4.5, "over", c),
                         {"url": "https://dk.example/event/5", "kind": "page"})        # no state set: the event page
        c["ops"]["draftkings"]["state"] = "nj"
        self.assertEqual(watch.bet_link(f, "draftkings", "player_receptions", "p", 4.5, "over", c)["url"],
                         "https://dk.example/nj/slip/9")
        self.assertEqual(watch.bet_link({}, "draftkings", "player_receptions", "p", 4.5, "over", c)["kind"], "home")


if __name__ == "__main__":
    unittest.main()
