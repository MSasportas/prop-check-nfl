"""Gap + Guard edge cases on numbers: books_min, Pinnacle required, whole lines, the -150 / +120 window, hold limits,
stale other books, the national Hard Rock key, no read before the injury report counts in, the safety pause, and the
one-time lock of old open items."""
import unittest

import helpers
from helpers import SEA_DEN, World, add_books
import common
import engine
import gg
import odds
import record

CFG = helpers.cfg()
T = gg.T(CFG)
GAME = {"id": SEA_DEN, "start": "2026-10-15T20:15:00-04:00", "away": "SEA", "home": "DEN", "date": "2026-10-15"}


def pairs_at(fair_over, n=5, hold=0.05, pin=True):
    """n books quoting the same pair at the same fair (over probability), each with `hold`."""
    def am(p):
        d = 1.0 / p
        return int(round((d - 1) * 100)) if d >= 2 else int(round(-100 / (d - 1)))
    po, pu = am(fair_over * (1 + hold)), am((1 - fair_over) * (1 + hold))
    names = (["pinnacle"] if pin else []) + ["draftkings", "fanduel", "betmgm", "betonlineag", "betrivers"]
    return {"pairs": {b: (po, pu) for b in names[:n]}, "best": {}, "stale": 0}


def assess(entry, price=-110, line=4.5, side="over", model=0.6):
    return gg.assess(side, line, price, entry, lambda: model, T)


class Step1(unittest.TestCase):
    def test_passes_with_a_real_gap_pinnacle_and_enough_books(self):
        a = assess(pairs_at(0.58), price=-110)                  # fair 58% at -110 (+10.7%), model agrees
        self.assertTrue(a["step1"], a["fails"])
        self.assertTrue(a["guard"])
        self.assertGreaterEqual(a["gap_ev"], 0.03)

    def test_four_other_books_needed(self):
        self.assertTrue(assess(pairs_at(0.58, n=4))["step1"])
        a = assess(pairs_at(0.58, n=3))
        self.assertIn("books", a["fails"])
        self.assertFalse(a["step1"])
        self.assertIsNone(a["guard"])                           # the model is not even asked

    def test_pinnacle_must_be_one_of_them(self):
        a = assess(pairs_at(0.58, n=5, pin=False))
        self.assertIn("pinnacle", a["fails"])
        self.assertFalse(a["step1"])

    def test_pinnacle_own_ev_must_not_be_negative(self):
        e = pairs_at(0.58)
        e["pairs"]["pinnacle"] = (-165, 135)                    # Pinnacle thinks the over is ~60%+, wait: make it say 40%
        e["pairs"]["pinnacle"] = (150, -175)
        a = assess(e)
        self.assertLess(a["pin_ev"], 0)
        self.assertIn("pinnacle", a["fails"])

    def test_gap_below_three_percent_fails(self):
        a = assess(pairs_at(0.535), price=-110)                 # fair 53.5% at -110: about +2.2%
        self.assertIn("gap", a["fails"])

    def test_whole_lines_are_skipped(self):
        a = assess(pairs_at(0.58), line=5.0)
        self.assertIn("line", a["fails"])
        self.assertFalse(a["step1"])

    def test_price_window_minus_150_to_plus_120(self):
        self.assertTrue(gg.inside_gate(-150, T))
        self.assertFalse(gg.inside_gate(-151, T))
        self.assertTrue(gg.inside_gate(120, T))
        self.assertFalse(gg.inside_gate(121, T))
        self.assertIn("price", assess(pairs_at(0.70), price=-160)["fails"])

    def test_the_guard_vetoes_when_the_model_disagrees(self):
        a = assess(pairs_at(0.58), price=-110, model=0.30)
        self.assertTrue(a["step1"])
        self.assertFalse(a["guard"])
        self.assertAlmostEqual(a["final"], 0.8 * a["market_fair"] + 0.2 * 0.30, places=9)

    def test_no_model_no_pick(self):
        a = gg.assess("over", 4.5, -110, pairs_at(0.58), lambda: None, T)
        self.assertTrue(a["step1"])
        self.assertIsNone(a["guard"])


class OtherBooks(unittest.TestCase):
    def read(self, others, at="2026-10-14T17:10:00-04:00"):
        return {"pull": "R", "at": at, "others": others, "book": "hardrockbet_fl"}

    def mk(self, lu="2026-10-14T21:00:00Z", pid="p1"):
        out = []
        for side, price in (("over", -110), ("under", -110)):
            out.append({"pid": pid, "side": side, "line": 4.5, "price": price})
        return {"player_receptions": {"outcomes": out, "last_update": lu}}

    def test_national_hard_rock_key_is_never_an_other_book(self):
        others = {"hardrockbet": self.mk(), "hardrockbet_fl": self.mk(), "pinnacle": self.mk()}
        out = gg.other_lines(self.read(others), "rec", CFG)
        self.assertEqual(sorted(out[("p1", 4.5)]["pairs"]), ["pinnacle"])

    def test_a_stale_other_book_is_dropped(self):
        others = {"pinnacle": self.mk(), "fanduel": self.mk(lu="2026-10-14T18:00:00Z")}   # 3h old at 17:10 ET = 21:10Z
        out = gg.other_lines(self.read(others), "rec", CFG)
        self.assertEqual(sorted(out[("p1", 4.5)]["pairs"]), ["pinnacle"])
        self.assertEqual(out[("p1", 4.5)]["stale"], 1)
        self.assertTrue(odds.is_stale(None, common.parse_iso("2026-10-14T17:10:00-04:00"), CFG["values"], "stale_min_others"))

    def test_hold_limits(self):
        wide = {"player_receptions": {"last_update": "2026-10-14T21:00:00Z", "outcomes": [
            {"pid": "p1", "side": "over", "line": 4.5, "price": -150}, {"pid": "p1", "side": "under", "line": 4.5, "price": -150}]}}
        out = gg.other_lines(self.read({"pinnacle": self.mk(), "bovada": wide}), "rec", CFG)
        self.assertEqual(sorted(out[("p1", 4.5)]["pairs"]), ["pinnacle"])             # ~20% hold: not a fair market
        one = {"player_receptions": {"last_update": "2026-10-14T21:00:00Z", "outcomes": [
            {"pid": "p1", "side": "over", "line": 4.5, "price": -110}]}}
        out = gg.other_lines(self.read({"fanduel": one}), "rec", CFG)
        self.assertEqual(out[("p1", 4.5)]["pairs"], {})                               # one side only: not a pair

    def test_a_hard_rock_only_read_is_not_a_market(self):
        self.assertIsNone(gg.other_lines({"pull": "R", "at": "2026-10-14T17:10:00-04:00"}, "rec", CFG))


class WhenAReadCounts(unittest.TestCase):
    def read(self, at, pull="R", others=True):
        f = {"pull": pull, "at": at, "book": "hardrockbet_fl"}
        if others:
            f["others"] = {}
        return f

    def test_nothing_before_the_final_injury_report_counts_in(self):
        # Thursday game: final report Wed 16:00 + 60 min grace
        self.assertFalse(gg.can_flag(self.read("2026-10-13T10:05:00-04:00"), GAME, CFG))
        self.assertFalse(gg.can_flag(self.read("2026-10-14T16:59:00-04:00"), GAME, CFG))
        self.assertTrue(gg.can_flag(self.read("2026-10-14T17:00:00-04:00"), GAME, CFG))
        self.assertTrue(gg.can_flag(self.read("2026-10-15T19:05:00-04:00", "C"), GAME, CFG))

    def test_after_kickoff_a_hard_rock_only_read_or_another_pull_type_never_flags(self):
        self.assertFalse(gg.can_flag(self.read("2026-10-15T20:30:00-04:00"), GAME, CFG))
        self.assertFalse(gg.can_flag(self.read("2026-10-14T18:00:00-04:00", others=False), GAME, CFG))
        self.assertFalse(gg.can_flag(self.read("2026-10-14T18:00:00-04:00", pull="M"), GAME, CFG))


class PulledText(unittest.TestCase):
    def test_if_bet_text_makes_no_claim_about_how_hard_rock_settles(self):
        for code, status in (("inactive", None), ("injury", "Questionable")):
            txt = gg.if_bet_text(code, -120, None, None, status=status)
            self.assertNotIn("void", txt.lower())
            self.assertIn("check", txt)
            self.assertTrue(txt.startswith("If you already bet it at -120:"))

    def test_rules_pull_says_what_to_do(self):
        txt = gg.if_bet_text("rules", 105, None, None)
        self.assertIn("open your bet slip at Hard Rock and check the line and price", txt)
        self.assertTrue(txt.startswith("If you already bet it at +105:"))


class SafetyPauseAndSweep(unittest.TestCase):
    def shown(self, i, slate, era, start="2026-10-25T13:00:00-04:00", price=-120):
        body = {"id": i, "game_id": "g" + i, "player_id": "p" + i, "player_short": "P. Name", "team": "SEA", "opp": "DEN", "home": False, "start": start, "price": price,
                "price_at": "2026-10-25T09:00:00-04:00", "mkey": "rec", "market": "rec", "period": "FG", "line": 4.5,
                "side": "over"}
        if era:
            body["era"] = era
        return {"t": "shown", "kind": "single", "id": i, "at": "2026-10-25T09:00:00-04:00", "slate": slate, "pick": body}

    def test_pause_counts_only_the_new_era(self):
        ev = []
        for n in range(11):
            ev += [self.shown("o%d" % n, "2026-W07", None),
                   {"t": "locked", "id": "o%d" % n, "at": "2026-10-25T13:00:00-04:00", "state": "early"},
                   {"t": "graded", "id": "o%d" % n, "at": "2026-10-26T01:00:00-04:00", "result": "L", "actual": 0,
                    "actual_text": "had 0", "price": -120, "units": -1.0}]
        self.assertIsNone(engine.safety_pause(ev, CFG))          # eleven old losses: no pause
        new = []
        for n in range(10):
            new += [self.shown("n%d" % n, "2026-W08", "gg1"),
                    {"t": "locked", "id": "n%d" % n, "at": "2026-11-01T13:00:00-04:00", "state": "inactives"},
                    {"t": "graded", "id": "n%d" % n, "at": "2026-11-02T01:00:00-04:00", "result": "L", "actual": 0,
                     "actual_text": "had 0", "price": -120, "units": -1.0}]
        self.assertIsNone(engine.safety_pause(ev + new[:27], CFG))        # nine new losses: still fine
        self.assertEqual(engine.safety_pause(ev + new, CFG), CFG["ops"]["safety_pause"]["text"])   # ten: paused

    def test_old_open_w07_items_are_locked_once_by_the_first_run_after_kickoff(self):
        w = World()
        try:
            record.append([self.shown("old1", "2026-W07", None, start="2026-10-19T20:15:00-04:00"),
                           self.shown("old2", "2026-W07", None, start="2026-10-25T13:00:00-04:00")])
            w.at("2026-10-26T09:00")
            locked = [e for e in w.events() if e["t"] == "locked"]
            self.assertEqual(sorted(e["id"] for e in locked), ["old1", "old2"])
            self.assertEqual(sorted(e["id"] for e in w.events() if e["t"] == "shown"), ["old1", "old2"])
            n = len(w.events())
            w.at("2026-10-26T09:30")
            self.assertEqual(len(w.events()), n)                 # not locked a second time
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
