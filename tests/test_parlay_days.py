"""Parlays: legs from one game day only, and the parlay cap counts per game day (bug found Oct 19: three
week-6 parlays mixed Sunday legs with the Monday night game)."""
import types
import unittest

import helpers
import common
import engine

V = helpers.cfg()["values"]


def leg(i, day, game, p=0.66, price=-120):
    return i, {"p": p, "b": 0.5455, "price": price, "settled": "inactives", "price_at": common.parse_iso(day + "T10:00:00-04:00"),
               "pid": "p" + i, "team": "T" + game, "game_id": game, "start": day + "T13:00:00-04:00"}


class SameDay(unittest.TestCase):
    def run_parlays(self, legs):
        inp = types.SimpleNamespace(v=V, slate="2026-W07")
        return [e for e in engine._parlays(inp, {}, dict(legs), "2026-10-22T10:00:00-04:00")
                if e["t"] == "shown"]

    def test_no_mixed_days(self):
        legs = [leg("a", "2026-10-25", "g1"), leg("b", "2026-10-25", "g2"), leg("c", "2026-10-25", "g3"),
                leg("d", "2026-10-26", "g4"), leg("e", "2026-10-22", "g5")]
        shown = self.run_parlays(legs)
        self.assertTrue(shown)
        live = dict(legs)
        for e in shown:
            self.assertEqual(len({live[l]["start"][:10] for l in e["parlay"]["legs"]}), 1, e["parlay"]["legs"])

    def test_one_game_days_get_none(self):
        legs = [leg("a", "2026-10-22", "g1"), leg("b", "2026-10-22", "g1"), leg("d", "2026-10-26", "g4")]
        self.assertEqual(self.run_parlays(legs), [])

    def test_cap_is_per_day(self):
        sun = [leg(c, "2026-10-25", "g" + c) for c in "abcdefg"]
        mon = [leg(c, "2026-10-26", "h" + c) for c in "xyz"]
        shown = self.run_parlays(sun + mon)
        live = dict(sun + mon)
        per = {}
        for e in shown:
            d = live[e["parlay"]["legs"][0]]["start"][:10]
            per[d] = per.get(d, 0) + 1
        self.assertLessEqual(per.get("2026-10-25", 0), V["parlay_cap"])
        self.assertGreaterEqual(per.get("2026-10-26", 0), 1)


if __name__ == "__main__":
    unittest.main()
