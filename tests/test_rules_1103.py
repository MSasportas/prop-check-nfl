"""The rules shipped 2026-11-03 (Matt: "A, let's get this idea live by Thursday"): the Pinnacle-moves rule is the live pick
method, reads are timed off kickoff (R 24 h, W 3 h, C 70 min before), and a pick needs Pinnacle's de-vigged chance to move
1 to under 5 pts toward a side between two reads with Hard Rock at or better than Pinnacle's POSTED price on that side.

SEA at DEN (Thu Oct 15, 8:15 PM ET): R Wed ~8:20 PM, W Thu ~5:20 PM, C Thu ~7:05 PM."""
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
                         (0.01, 0.05, "posted", 0, None))
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

    def shown(self, w):
        return [e for e in w.events() if e["t"] == "shown"]

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

    def test_three_pt_move_hard_rock_at_or_better_than_posted_is_a_pick_at_w(self):
        w, s = self.run_w(-0.03)
        try:
            sh = self.shown(w)
            self.assertEqual(len(sh), 1)
            b = sh[0]["pick"]
            self.assertEqual((b["method"], b["era"], b["player_id"], b["side"], b["line"], b["price"], b["read_pull"]),
                             ("pin_solo", "ps1", WILSON, "under", 17.5, 105, "W"))
            self.assertRegex(b["reason"], r"^Pinnacle moved 3 pts toward Under 17\.5 carries since yesterday; Hard Rock is "
                                          r"still at \+105, (better than|level with) Pinnacle's [+-]\d+\.$")
            p = [x for x in s["picks"] if x["state"] == "open"][0]
            self.assertEqual(p["pin_solo"]["pin_price"], b["pin_price"])
            self.assertRegex(p["vs"], r"^Pinnacle moved \+3 pts · Pinnacle [+-]\d+, Hard Rock \+105$")
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

    def test_under_one_pt_is_nothing(self):
        w, _ = self.run_w(-0.005)
        try:
            self.assertEqual(self.shown(w), [])
            self.assertEqual(self.cand(w), [])
        finally:
            w.close()

    def test_hard_rock_worse_than_pinnacles_posted_price_is_not_a_pick(self):
        w = World(pool=(500, 500, 500, 500), config=cfg())
        try:
            build(w, hr_under=105, lu=LU_R)
            w.at(R_AT)
            build(w, pin=-0.03, hr_under=105, lu=LU_W)                     # Pinnacle's under is now about -104
            w.set_price(SEA_DEN, "player_rush_attempts", "Emanuel Wilson", "under", -110, last_update_z=LU_W)   # Hard Rock only
            w.at(W_AT)
            self.assertEqual(self.shown(w), [])
            self.assertEqual([c["gate"] for c in self.cand(w)], ["worse_than_posted"])
        finally:
            w.close()

    def test_pick_survives_c_and_locks_into_its_own_record_line(self):
        w, _ = self.run_w(-0.03)
        try:
            inactives(w)
            build(w, pin=-0.03, others=0.0, hr_under=105, lu=LU_C)
            s = w.at(C_AT)
            self.assertEqual(len(self.shown(w)), 1)                       # C adds nothing for the same player
            p = [x for x in s["picks"] if x["method"] == "pin_solo"][0]
            self.assertEqual((p["state"], p["settled_text"]), ("open", "Inactives confirmed"))
            self.assertEqual(common.read_json(w.path("record.json"))["weeks"], [])     # nothing in gg1
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
