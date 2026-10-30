"""Behind-the-scenes research tracks (scripts/watch.py): Pinnacle moves alone (pin_solo) and Pinnacle as the price judge
(pin_judge), the optional watch-only W read, and the promise that none of it touches the Picks tab or the Record.

SEA at DEN (Thu Oct 15, 8:15 PM ET): read R after the final injury report (Wed 5:10 PM), read C 70 minutes before kickoff.
Hard Rock's prices are the real payload; the other books are built from them (helpers.add_books) and then moved here."""
import copy
import json
import os
import unittest
from datetime import timedelta

import helpers
from helpers import SEA_DEN, WILSON, World, add_books, _am, _be
import common
import gg
import odds
import watch

RA, WHO = "player_rush_attempts", "Emanuel Wilson"
R_AT, C_AT = "2026-10-14T17:10", "2026-10-15T19:05"
LU_R, LU_C = "2026-10-14T21:00:00Z", "2026-10-15T23:00:00Z"


def cfg_no_w():
    c = helpers.legacy_role_cfg()
    c["ops"]["watch"]["w_reads"] = 0                      # these tests run R and C only
    return c


def shifted(payload, pin=0.0, others=0.0, feed=RA, who=WHO):
    """Move every non-Hard Rock book's fair chance of the OVER for one player and market by pin (Pinnacle) or others
    (everyone else), keeping each book's own hold. Negative = toward the under."""
    js = copy.deepcopy(payload)
    for b in js["bookmakers"]:
        if b["key"] == "hardrockbet_fl":
            continue
        sh = pin if b["key"] == "pinnacle" else others
        for m in b["markets"]:
            if m["key"] != feed:
                continue
            by = {}
            for o in m["outcomes"]:
                if o["description"] == who:
                    by.setdefault(o["point"], {})[o["name"]] = o
            for pair in by.values():
                bo, bu = _be(pair["Over"]["price"]), _be(pair["Under"]["price"])
                hold = bo + bu
                f = bo / hold + sh
                pair["Over"]["price"], pair["Under"]["price"] = _am(f * hold), _am((1 - f) * hold)
    return js


def build(w, pin=0.0, others=0.0, hr_under=None, lu=LU_R):
    """The odds fixture for a read: Hard Rock (optionally Wilson's under repriced), six books built from it, then the moves."""
    base = copy.deepcopy(w.sea_den)
    if hr_under is not None:
        hr = next(b for b in base["bookmakers"] if b["key"] == "hardrockbet_fl")
        for m in hr["markets"]:
            if m["key"] == RA:
                for o in m["outcomes"]:
                    if o["description"] == WHO and o["name"] == "Under":
                        o["price"] = hr_under
    js = shifted(add_books(base, {}, last_update_z=lu), pin, others)
    w.set_odds(SEA_DEN, js, lu)


def events(w):
    p = w.path("watch", "events.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(l) for l in f.read().splitlines() if l]


def summary(w):
    return common.read_json(w.path("watch", "summary.json"))


def track(w, name):
    return next(t for t in summary(w)["tracks"] if t["track"] == name)


def inactives(w):
    w.post_inactives(26, [("Zach Charbonnet", "RB"), ("Chazz Surratt", "LB"), ("Julian Neal", "CB")])
    w.post_inactives(7, [("Riley Moss", "CB"), ("Pat Surtain II", "CB"), ("Pat Bryant", "WR")])


class Base(unittest.TestCase):
    def setUp(self):
        self.w = World(pool=(29, 500, 500, 326, 109, 500, 415), config=cfg_no_w())

    def tearDown(self):
        self.w.close()

    def two_reads(self, pin, others, hr_under=105):
        w = self.w
        build(w, hr_under=hr_under)
        w.at(R_AT)
        inactives(w)
        build(w, pin=pin, others=others, hr_under=hr_under, lu=LU_C)
        return w.at(C_AT)

    def solo_cands(self):
        return [e for e in events(self.w) if e["t"] == "cand" and e["track"] == "pin_solo" and e["pid"] == WILSON
                and e["market"] == "rush_att"]


class PinSolo(Base):
    def test_pinnacle_moves_3_pts_others_1_pt_hard_rock_stays_is_flagged(self):
        s = self.two_reads(pin=-0.03, others=-0.01)
        pr = [e for e in events(self.w) if e["t"] == "pair" and e["track"] == "pin_solo"][0]
        self.assertEqual(pr["gap_min"], round((common.parse_iso(pr["b_at"]) - common.parse_iso(pr["a_at"])).total_seconds() / 60.0, 1))
        fl = [e for e in events(self.w) if e["t"] == "flag" and e["track"] == "pin_solo"]
        self.assertEqual([(e["player"], e["market"], e["side"], e["line"], e["price"], e["read"], e["window"])
                          for e in fl], [(WHO, "rush_att", "under", 17.5, 105, "C", "R→C")])
        f = fl[0]
        self.assertAlmostEqual(f["pin_after"] - f["pin_before"], 0.03, delta=0.004)
        self.assertAlmostEqual(f["others_move"], 0.01, delta=0.004)
        self.assertGreaterEqual(f["n_others"], 4)
        self.assertGreaterEqual(f["ev_vs_pin"], 0.0)
        self.assertEqual(f["bet"], "Under 17.5 carries")
        # research only: nothing on the Picks tab, nothing in the Record
        self.assertEqual([p for p in s["picks"] if p["state"] == "open"], [])
        self.assertEqual([e for e in self.w.events() if e["t"] == "shown"], [])
        self.assertTrue(os.path.exists(self.w.path("watch", "events.jsonl")))
        t = track(self.w, "pin_solo")
        self.assertEqual((t["total"]["flags"], t["total"]["pending"]), (1, 1))
        self.assertEqual(t["seen"]["would_fire"], 1)
        self.assertEqual(summary(self.w)["reads"]["by_window"][0]["window"], "R→C")

    def test_the_other_books_moved_too_is_not_flagged_and_says_why(self):
        self.two_reads(pin=-0.03, others=-0.025)
        self.assertEqual([e for e in events(self.w) if e["t"] == "flag" and e["track"] == "pin_solo"], [])
        c = self.solo_cands()
        self.assertEqual([x["gate"] for x in c], ["others_moved"])
        self.assertGreaterEqual(c[0]["others_move"], 0.02)
        gates = {g["gate"]: g["n"] for g in track(self.w, "pin_solo")["seen"]["by_gate"]}
        self.assertEqual(gates.get("others_moved"), 1)

    def test_hard_rock_price_below_pinnacles_new_fair_is_not_flagged(self):
        # Hard Rock under -105 against a Pinnacle fair that moved 3 pts to about 50%: -105 is worse than fair (+105 is not)
        self.two_reads(pin=-0.03, others=-0.01, hr_under=-105)
        self.assertEqual([e for e in events(self.w) if e["t"] == "flag" and e["track"] == "pin_solo"], [])
        c = self.solo_cands()
        self.assertEqual([x["gate"] for x in c], ["worse_than_fair"])
        self.assertLess(c[0]["ev_vs_pin"], 0.0)

    def test_a_small_pinnacle_move_is_not_even_a_candidate(self):
        self.two_reads(pin=-0.01, others=0.0)
        self.assertEqual(self.solo_cands(), [])

    def test_too_few_other_books(self):
        w = self.w
        build(w, hr_under=105)
        w.at(R_AT)
        inactives(w)
        js = shifted(add_books(copy.deepcopy(w.sea_den), {}, books=("pinnacle", "draftkings", "fanduel", "betmgm"),
                               last_update_z=LU_C), pin=-0.03)
        w.set_odds(SEA_DEN, js, LU_C)
        w.at(C_AT)
        self.assertEqual([x["gate"] for x in self.solo_cands()], ["books"])      # only 3 other books on both reads
        self.assertEqual([e for e in events(w) if e["t"] == "flag" and e["track"] == "pin_solo"], [])

    def test_a_player_listed_questionable_is_blocked_by_the_live_vetoes(self):
        w = self.w
        build(w, hr_under=105)
        w.at(R_AT)
        inactives(w)
        w.set_injury(26, "Emanuel Wilson", "Questionable", pos="RB", espn_id="4887558")
        build(w, pin=-0.03, others=-0.01, hr_under=105, lu=LU_C)
        w.at(C_AT)
        c = self.solo_cands()
        self.assertEqual([x["gate"] for x in c], ["ineligible"])
        self.assertIn("questionable", c[0]["detail"])
        self.assertEqual([e for e in events(w) if e["t"] == "flag" and e["track"] == "pin_solo"], [])

    def test_hard_rock_changing_the_line_between_reads_is_not_flagged(self):
        w = self.w
        build(w, hr_under=105)
        w.at(R_AT)
        inactives(w)
        build(w, pin=-0.03, others=-0.01, hr_under=105, lu=LU_C)
        for side in ("over", "under"):
            w.set_price(SEA_DEN, RA, WHO, side, -110 if side == "over" else 105, line=18.5, last_update_z=LU_C)
        w.at(C_AT)
        self.assertEqual([e for e in events(w) if e["t"] == "flag" and e["track"] == "pin_solo"], [])

    def test_no_read_before_the_final_injury_report_is_used(self):
        w = self.w
        build(w, hr_under=105)
        w.at("2026-10-13T10:05")                           # Tuesday: before Wednesday's final report
        build(w, pin=-0.03, others=-0.01, hr_under=105)
        w.at("2026-10-14T16:30")                           # still before it counts in (4 PM + 60 min)
        self.assertEqual(events(w), [])
        w.at("2026-10-14T17:10")
        self.assertEqual([e["t"] for e in events(w)].count("pair"), 0)    # one usable read is not a pair

    def test_running_again_adds_nothing_and_the_log_only_grows(self):
        self.two_reads(pin=-0.03, others=-0.01)
        before = open(self.w.path("watch", "events.jsonl"), "rb").read()
        self.w.at("2026-10-15T19:15")
        after = open(self.w.path("watch", "events.jsonl"), "rb").read()
        self.assertEqual(before, after)

    def test_a_log_that_lost_its_last_newline_is_refused(self):
        self.two_reads(pin=-0.03, others=-0.01)
        with self.assertRaises(RuntimeError):
            self._tamper()

    def _tamper(self):
        p = self.w.path("watch", "events.jsonl")
        raw = open(p, "rb").read()
        old = watch.read_raw
        try:
            watch.read_raw = lambda: raw[:-5]                # pretend the committed bytes are not a prefix
            watch.append([{"t": "x", "id": "y"}])
        finally:
            watch.read_raw = old


class PinJudge(Base):
    def test_hard_rock_two_percent_better_than_pinnacle_is_flagged_once(self):
        w = self.w
        build(w, pin=-0.05, others=0.0, hr_under=100)       # Pinnacle 5 pts higher on the under: Hard Rock +100 is a bargain to it
        s = w.at(R_AT)
        fl = [e for e in events(w) if e["t"] == "flag" and e["track"] == "pin_judge" and e["pid"] == WILSON]
        self.assertEqual([(e["market"], e["side"], e["price"], e["read"]) for e in fl], [("rush_att", "under", 100, "R")])
        self.assertGreaterEqual(fl[0]["pin_ev"], 0.02)
        self.assertEqual([p for p in s["picks"] if p["state"] == "open"], [])      # and the live rule saw no gap
        self.assertEqual([e for e in w.events() if e["t"] == "shown"], [])
        # a later read flags nothing new for the same player / market / game
        inactives(w)
        build(w, pin=-0.05, others=0.0, hr_under=100, lu=LU_C)
        w.at(C_AT)
        fl = [e for e in events(w) if e["t"] == "flag" and e["track"] == "pin_judge" and e["pid"] == WILSON]
        self.assertEqual(len(fl), 1)
        again = [e for e in events(w) if e["t"] == "cand" and e["track"] == "pin_judge" and e["pid"] == WILSON]
        self.assertEqual(sorted(x["gate"] or "flagged" for x in again), ["already_flagged", "flagged"])

    def test_below_two_percent_is_not_a_candidate(self):
        build(self.w, pin=-0.005, hr_under=100)
        self.w.at(R_AT)
        self.assertEqual([e for e in events(self.w) if e["t"] == "cand" and e["pid"] == WILSON], [])


class Grading(Base):
    def test_flags_are_graded_by_the_records_code_at_the_frozen_price(self):
        from test_replay_week import box
        self.two_reads(pin=-0.03, others=-0.01)
        w = self.w
        # the game ends: ESPN says final with 14 carries for Wilson: the under 17.5 wins at +105
        self.mark_final(box(3, "9", "14"))
        gr = [e for e in events(w) if e["t"] == "graded" and e["track"] == "pin_solo"]
        self.assertEqual([(e["result"], e["price"], e["units"]) for e in gr], [("W", 105, 1.05)])
        t = track(w, "pin_solo")
        self.assertEqual((t["total"]["w"], t["total"]["l"], t["total"]["units"], t["total"]["pending"]), (1, 0, 1.05, 0))
        self.assertAlmostEqual(t["total"]["avg_break_even"], 0.4878, places=4)
        self.assertEqual(t["by_market"][0]["market"], "rush_att")
        self.assertEqual([e for e in w.events() if e["t"] in ("shown", "graded")], [])    # the Record has nothing of it

    def mark_final(self, summary_doc):
        w = self.w
        w.set_scoreboard("20261015", "401872995", "post", away=20, home=17)
        w.set_summary("401872995", summary_doc)
        w.at("2026-10-16T00:10")


class SoftAndSeparate(Base):
    def test_a_watch_failure_is_a_note_not_a_failed_step(self):
        w = self.w
        build(w, hr_under=105)
        saved = os.environ.pop("PC_RAISE")
        orig = watch.scan
        try:
            watch.scan = lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
            s = w.at(R_AT)
        finally:
            watch.scan = orig
            os.environ["PC_RAISE"] = saved
        step = next(x for x in w.last.steps if x["step"] == "watch")
        self.assertEqual(step["status"], "skipped")
        self.assertIn("watch failed (soft)", step["note"])
        self.assertIsNone(w.last.failed_step())
        self.assertTrue(s["status"]["last_run"]["ok"])

    def test_watch_off_writes_nothing(self):
        c = cfg_no_w()
        c["ops"]["watch"]["active"] = False
        w = World(config=c)
        try:
            build(w, hr_under=105)
            w.at(R_AT)
            self.assertFalse(os.path.exists(w.path("watch")))
        finally:
            w.close()

    def test_w_can_never_flag_a_live_pick(self):
        self.assertEqual(gg.T(helpers.cfg())["flag_pulls"], ["R", "C"])
        self.assertTrue(gg.can_flag({"pull": "C", "at": "2026-10-15T19:05:00-04:00", "others": {}, "book": "x"},
                                    {"start": "2026-10-15T20:15:00-04:00", "away": "SEA", "home": "DEN",
                                     "date": "2026-10-15"}, helpers.cfg()))
        self.assertFalse(gg.can_flag({"pull": "W", "at": "2026-10-15T17:20:00-04:00", "others": {}, "book": "x"},
                                     {"start": "2026-10-15T20:15:00-04:00", "away": "SEA", "home": "DEN",
                                      "date": "2026-10-15"}, helpers.cfg()))


def cfg_live(live=True, **ps):
    c = cfg_no_w()
    c["ops"]["pin_solo"]["live"] = live
    c["ops"]["pin_solo"].update(ps)
    return c


class LiveSwitch(unittest.TestCase):
    """ops.pin_solo.live: OFF by default. On, the pin_solo candidates become real picks (method pin_solo, era ps1)."""

    def run_pair(self, w, pin=-0.03, others=-0.01, hr_under=105, r_edges=None):
        if r_edges:
            w.set_odds(SEA_DEN, add_books(copy.deepcopy(w.sea_den), r_edges, last_update_z=LU_R), LU_R)
        else:
            build(w, hr_under=hr_under)
        w.at(R_AT)
        inactives(w)
        build(w, pin=pin, others=others, hr_under=hr_under, lu=LU_C)
        return w.at(C_AT)

    def test_the_shipped_switch_is_off(self):
        c = helpers.cfg()
        self.assertIs(c["ops"]["pin_solo"]["live"], False)
        self.assertFalse(watch.pin_live(c, "2026-W08"))

    def test_switch_off_means_no_pin_solo_pick_ever(self):
        w = World(pool=(500, 500, 500, 500), config=cfg_live(False))
        try:
            s = self.run_pair(w)
            self.assertEqual([e for e in w.events() if e["t"] == "shown"], [])
            self.assertEqual(s["picks"], [])
            self.assertEqual(s["methods"]["pin_solo"]["live"], False)
            self.assertIsNone(common.read_json(w.path("record.json"))["pin_solo"])
            self.assertEqual(len([e for e in events(w) if e["t"] == "flag" and e["track"] == "pin_solo"]), 1)   # research still sees it
        finally:
            w.close()

    def test_only_a_real_true_turns_it_on(self):
        for v in ("true", 1, "yes", None):
            self.assertFalse(watch.pin_live(cfg_live(v), "2026-W08"), v)
        self.assertTrue(watch.pin_live(cfg_live(True), "2026-W08"))

    def test_whole_number_lines_are_skipped_by_both_tracks(self):
        t = helpers.cfg()["ops"]["watch"]["tracks"]
        self.assertIs(t["pin_solo"]["skip_whole_lines"], True)
        self.assertIs(t["pin_judge"]["skip_whole_lines"], True)

    def test_a_ps1_pick_counts_as_holding_its_market_for_the_c_read(self):
        import engine
        w = World(pool=(500, 500, 500, 500), config=cfg_live(True))
        seen = []
        real = engine.focus_markets
        try:
            self.run_pair(w)
            engine.focus_markets = lambda inp, by_game, open_items, near=True: (seen.append([i["shown"].get("era") for i in open_items]),
                                                                              real(inp, by_game, open_items, near))[1]
            w.at("2026-10-15T19:15")
            self.assertIn(["ps1"], seen)
        finally:
            engine.focus_markets = real
            w.close()

    def test_switch_on_but_the_watch_track_off_stays_off(self):
        c = cfg_live(True)
        c["ops"]["watch"]["tracks"]["pin_solo"]["active"] = False
        w = World(pool=(500, 500, 500, 500), config=c)
        try:
            s = self.run_pair(w)
            self.assertEqual([e for e in w.events() if e["t"] == "shown"], [])
            self.assertFalse(s["methods"]["pin_solo"]["live"])
        finally:
            w.close()

    def test_switch_on_pinnacle_moves_hard_rock_stays_is_a_real_pick(self):
        w = World(pool=(500, 500, 500, 500), config=cfg_live(True))
        try:
            s = self.run_pair(w)
            sh = [e for e in w.events() if e["t"] == "shown"]
            self.assertEqual(len(sh), 1)
            b = sh[0]["pick"]
            self.assertEqual((b["method"], b["era"], b["player_id"], b["mkey"], b["side"], b["line"], b["price"], b["read_pull"]),
                             ("pin_solo", "ps1", WILSON, "rush_att", "under", 17.5, 105, "C"))
            self.assertTrue(sh[0]["id"].endswith(":ps"))
            self.assertRegex(b["reason"], r"^Pinnacle moved 3 pts toward Under 17\.5 carries; the other books moved 0\.9; "
                                          r"Hard Rock is still at \+105, better than Pinnacle's fair [+-]\d+\.$")
            p = [x for x in s["picks"] if x["state"] == "open"][0]
            self.assertEqual((p["method"], p["era"], p["settled_text"], p["price"], p["rank"]),
                             ("pin_solo", "ps1", "Inactives confirmed", 105, 1))
            self.assertEqual(p["pin_solo"]["pin_move_pts"], 3.0)
            self.assertEqual(p["pin_solo"]["others_move_pts"], 0.9)
            self.assertIsNone(p["gap_pct"])
            self.assertIsNone(p["final_ev_pct"])
            self.assertEqual(p["vs"], "Pinnacle +3 pts, other books +0.9")
            self.assertTrue(s["methods"]["pin_solo"]["live"])
            self.assertTrue(p["flags"]["price_final"])
            rec = common.read_json(w.path("record.json"))
            self.assertEqual(rec["weeks"], [])                    # nothing in the gg1 record
            self.assertEqual(rec["summary"]["headline"]["n"], 0)
        finally:
            w.close()

    def test_a_gap_guard_pick_for_the_same_player_blocks_the_second(self):
        w = World(pool=(500, 500, 500, 500), config=cfg_live(True))
        try:
            edges = {(RA, WHO, "under"): 0.04}                    # a real gap on the R read: Gap + Guard picks Wilson
            s = self.run_pair(w, r_edges=edges)
            sh = [e for e in w.events() if e["t"] == "shown"]
            self.assertEqual([(e["pick"]["method"] if "method" in e["pick"] else "gap_guard", e["pick"]["era"]) for e in sh],
                             [("gap_guard", "gg1")])
            self.assertEqual([p["method"] for p in s["picks"]], ["gap_guard"])         # the C read took it back; no second pick
        finally:
            w.close()

    def test_one_pick_per_player_also_holds_for_a_pin_solo_pick_first(self):
        w = World(pool=(500, 500, 500, 500), config=cfg_live(True))
        try:
            self.run_pair(w)
            self.assertEqual(len([e for e in w.events() if e["t"] == "shown"]), 1)
            w.at("2026-10-15T19:15")                              # later runs add nothing
            self.assertEqual(len([e for e in w.events() if e["t"] == "shown"]), 1)
        finally:
            w.close()

    def test_price_out_of_the_window_pulls_the_pick(self):
        c = cfg_live(True)
        c["ops"]["watch"]["w_reads"] = 1
        w = World(pool=(500, 500, 500, 500), config=c)
        try:
            build(w)
            w.at(R_AT)
            build(w, pin=-0.03, others=-0.01, lu="2026-10-15T20:50:00Z")
            w.at("2026-10-15T17:20")                              # the W read: the pair R to W makes the pick
            sh = [e for e in w.events() if e["t"] == "shown"]
            self.assertEqual([(e["pick"]["method"], e["pick"]["read_pull"]) for e in sh], [("pin_solo", "W")])
            inactives(w)
            build(w, pin=-0.03, others=-0.01, hr_under=-170, lu=LU_C)
            w.set_price(SEA_DEN, RA, WHO, "over", 140, last_update_z=LU_C)       # a sane two-sided price: the under is now -170
            w.at(C_AT)
            pulled = [e for e in w.events() if e["t"] == "pulled"]
            self.assertEqual([e["code"] for e in pulled], ["gate"])
            self.assertIn("-150", pulled[0]["reason"])
            self.assertIn("If you already bet it at +100", pulled[0]["if_bet"])
            p = w.slate()["picks"][0]
            self.assertEqual((p["state"], p["price"]), ("pulled", 100))
        finally:
            w.close()

    def test_pinnacle_drifting_back_alone_does_not_pull_it(self):
        w = World(pool=(500, 500, 500, 500), config=cfg_live(True))
        try:
            self.run_pair(w)
            build(w, pin=0.0, others=0.0, hr_under=105, lu="2026-10-15T23:20:00Z")
            w.at("2026-10-15T19:20")
            self.assertEqual([e for e in w.events() if e["t"] == "pulled"], [])
            self.assertEqual([p["state"] for p in w.slate()["picks"]], ["open"])
        finally:
            w.close()

    def test_a_questionable_listing_pulls_it_like_any_pick(self):
        w = World(pool=(500, 500, 500, 500), config=cfg_live(True))
        try:
            self.run_pair(w)
            w.set_injury(26, "Emanuel Wilson", "Questionable", pos="RB", espn_id="4887558")
            w.at("2026-10-15T19:20")
            self.assertEqual([e["code"] for e in w.events() if e["t"] == "pulled"], ["injury"])
        finally:
            w.close()

    def test_locks_grades_and_counts_on_its_own_headline(self):
        from test_replay_week import box
        w = World(pool=(500, 500, 500, 500), config=cfg_live(True))
        try:
            self.run_pair(w)
            w.set_scoreboard("20261015", "401872995", "post", away=20, home=17)
            w.set_summary("401872995", box(3, "9", "14"))
            w.at("2026-10-16T00:10")
            ev = [e for e in w.events() if e["t"] in ("locked", "graded")]
            self.assertEqual([(e["t"], e.get("result"), e.get("price"), e.get("units")) for e in ev],
                             [("locked", None, None, None), ("graded", "W", 105, 1.05)])
            rec = common.read_json(w.path("record.json"))
            self.assertEqual(rec["summary"]["headline"]["n"], 0)          # never mixed into gg1
            self.assertEqual(rec["weeks"], [])
            ps = rec["pin_solo"]
            self.assertEqual((ps["era"], ps["label"]), ("ps1", "Pinnacle moves alone"))
            h = ps["summary"]["headline"]
            self.assertEqual((h["w"], h["l"], h["n"], h["units"]), (1, 0, 1, 1.05))
            e = ps["weeks"][0]["entries"][0]
            self.assertEqual((e["method"], e["era"], e["state"], e["result"]), ("pin_solo", "ps1", "graded", "W"))
            self.assertEqual(w.slate()["picks"][0]["graded"]["result"], "W")
        finally:
            w.close()

    def test_its_own_safety_pause_counts_only_ps1(self):
        w = World(pool=(500, 500, 500, 500), config=cfg_live(True))
        try:
            ev = []
            for n in range(10):
                i = "2026-W05:g:p%d:rec:4.5:over:ps" % n
                body = {"id": i, "era": "ps1", "method": "pin_solo", "game_id": "g", "player_id": "p%d" % n,
                        "player_short": "P. X", "team": "SEA", "opp": "DEN", "home": False, "start": "2026-10-08T20:15:00-04:00",
                        "market": "rec", "mkey": "rec", "period": "FG", "line": 4.5, "side": "over", "price": -120,
                        "price_at": "2026-10-08T10:00:00-04:00", "bet": "Over 4.5 receptions"}
                ev += [{"t": "shown", "kind": "single", "id": i, "at": "2026-10-08T10:00:00-04:00", "slate": "2026-W05", "pick": body},
                       {"t": "locked", "id": i, "at": "2026-10-08T20:15:00-04:00", "state": "inactives"},
                       {"t": "graded", "id": i, "at": "2026-10-09T01:00:00-04:00", "result": "L", "actual": 0,
                        "actual_text": "had 0", "price": -120, "units": -1.0}]
            import record
            record.append(ev)
            s = self.run_pair(w)
            self.assertEqual([e["id"] for e in w.events() if e["t"] == "shown" and e["slate"] == "2026-W06"], [])
            self.assertTrue(any("Pinnacle-moves picks are stopped" in b["text"] for b in s["banners"]), s["banners"])
            self.assertNotIn("Record is down 10 units", json.dumps(s["banners"]))      # gg1 is not paused
        finally:
            w.close()


class WRead(unittest.TestCase):
    """The optional W read: ~3 hours before kickoff, only with credits to spare after reserving R and C for every game."""

    def games(self, n, day="2026-11-01"):
        return [{"id": "g%d" % i, "start": day + "T13:00:00-05:00", "away": "A%d" % i, "home": "B%d" % i} for i in range(n)]

    def plan(self, tier="full"):
        return {"tier": tier, "pulls": odds.TIER_PULLS[tier], "pool_at_plan": 200.0}

    def test_budget_is_what_is_left_after_r_and_c_for_every_game(self):
        cfg = helpers.cfg()
        now = common.parse_iso("2026-11-01T09:00:00-05:00")
        gs = self.games(13)
        per = 6
        self.assertEqual(odds.watch_budget(self.plan(), 500, gs, {}, cfg, None, now), 500 - 13 * 2 * per - 12)
        r_done = {g["id"]: [{"pull": "R", "at": common.parse_iso("2026-10-30T17:30:00-04:00")}] for g in gs}
        self.assertEqual(odds.watch_budget(self.plan(), 211, gs, r_done, cfg, None, now), 211 - 13 * per - 12)
        self.assertEqual(odds.watch_budget(self.plan(), 100, gs, r_done, cfg, None, now), 10.0)              # 100 - 78 - 12
        self.assertEqual(odds.watch_budget(self.plan(), 50, gs, r_done, cfg, None, now), 0.0)             # never negative
        self.assertIsNone(odds.watch_budget(self.plan("tight"), 500, gs, {}, cfg, None, now))
        c = copy.deepcopy(cfg)
        c["ops"]["watch"]["active"] = False
        self.assertIsNone(odds.watch_budget(self.plan(), 500, gs, {}, c, None, now))

    def test_the_spare_is_spread_over_the_weeks_still_to_come(self):
        cfg = helpers.cfg()
        now = common.parse_iso("2026-11-01T09:00:00-05:00")
        gs = []
        for wk, day in ((8, "2026-11-01"), (9, "2026-11-08"), (10, "2026-11-15"), (11, "2026-11-22"), (12, "2026-11-29")):
            gs += [dict(g, week=wk, id="%s_%d" % (g["id"], wk), start=day + "T13:00:00-05:00") for g in self.games(2)]
        r_done = {g["id"]: [{"pull": "R", "at": common.parse_iso("2026-10-30T17:30:00-04:00")}]
                  for g in gs if g["week"] == 8}
        owed = (2 * 12 * 4) + 2 * 6                               # four weeks owe R+C, week 8 owes C only
        base = 400 - owed - 12
        self.assertEqual(odds.watch_budget(self.plan(), 400, gs, r_done, cfg, None, now), base / 5.0)
        # one W already made this week (6 credits): the week's share counts it
        one = {k: list(v) for k, v in r_done.items()}
        one[gs[0]["id"]].append({"pull": "W", "at": common.parse_iso("2026-11-01T10:05:00-05:00")})
        got = odds.watch_budget(self.plan(), 394, gs, one, cfg, None, now)       # the 6 credits are out of the pool now
        self.assertAlmostEqual(got, max(0.0, (394 - owed - 12 + 6) / 5.0 - 6))
        c = copy.deepcopy(cfg)
        c["ops"]["watch"]["w_pace"] = False
        self.assertEqual(odds.watch_budget(self.plan(), 400, gs, r_done, c, None, now), base)
        last = [g for g in gs if g["week"] == 12]
        self.assertEqual(odds.watch_budget(self.plan(), 400, last, {}, cfg, None, now), 400 - 2 * 12 - 12)   # last week: all of it

    def test_w_is_due_in_its_window_only_after_r_and_only_while_the_spare_lasts(self):
        cfg = helpers.cfg()
        gs = self.games(3)
        k = common.parse_iso(gs[0]["start"])
        rt = {g["id"]: common.parse_iso("2026-10-30T17:00:00-04:00") for g in gs}
        done = {g["id"]: [{"pull": "R", "at": common.parse_iso("2026-10-30T17:30:00-04:00"),
                                  "ina": False, "hr": ["player_receptions"]}] for g in gs}
        ok = {g["id"] for g in gs}

        def due(now, spare, done=done):
            return [(d["pull"], d["game"]["id"], odds.est_cost(d)) for d in odds.due_pulls(
                now, gs, self.plan(), cfg, done, {}, {}, rt, ok, set(), {"spare": spare})]
        self.assertEqual(due(k - timedelta(minutes=181), 100), [])
        self.assertEqual(due(k - timedelta(minutes=179), 100), [("W", "g0", 6), ("W", "g1", 6), ("W", "g2", 6)])
        self.assertEqual(due(k - timedelta(minutes=179), 13), [("W", "g0", 6), ("W", "g1", 6)])      # spare runs out
        self.assertEqual(due(k - timedelta(minutes=179), 5), [])
        self.assertEqual(due(k - timedelta(minutes=99), 100), [])                                  # too close to C
        self.assertNotIn("W", [x[0] for x in due(k - timedelta(minutes=179), 100, done={})])     # no R yet: not W
        two = {g: [dict(f) for f in fs] + [{"pull": "W", "at": common.parse_iso("2026-11-01T10:05:00-05:00"),
                                             "ina": False}] for g, fs in done.items()}
        self.assertEqual(due(k - timedelta(minutes=170), 100, done=two), [])                       # w_reads = 1
        self.assertEqual(odds.due_pulls(k - timedelta(minutes=179), gs, self.plan(), cfg, done, {}, {}, rt, ok, set()), [])  # no watch arg

    def test_c_and_r_always_win_over_w(self):
        cfg = helpers.cfg()
        gs = self.games(1)
        k = common.parse_iso(gs[0]["start"])
        rt = {"g0": common.parse_iso("2026-10-30T17:00:00-04:00")}
        d = odds.due_pulls(k - timedelta(minutes=170), gs, self.plan(), cfg, {}, {}, {}, rt, {"g0"}, set(), {"spare": 99})
        self.assertEqual([x["pull"] for x in d], ["R"])


class WReadInTheWorld(unittest.TestCase):
    def test_w_read_is_made_logged_as_w_and_used_by_the_watch_tracks_only(self):
        c = helpers.legacy_role_cfg()
        w = World(pool=(500, 500, 500, 500), config=c)
        try:
            build(w, hr_under=105)
            w.at(R_AT)
            spent_r = w.status()["pool_total"]
            build(w, pin=-0.03, others=-0.01, hr_under=105, lu="2026-10-15T21:10:00Z")
            w.at("2026-10-15T17:20")                         # 8:15 PM kickoff - 175 min: inside the W window
            self.assertEqual([d["pull"] for d in w.status()["due"] if d["game_id"] == SEA_DEN], ["W"])
            self.assertEqual(w.status()["pool_total"], spent_r - 6)
            ledger = common.read_json(w.path("state", "credits.json"))
            self.assertEqual([e["pull"] for e in ledger["spent"] if e["game_id"] == SEA_DEN], ["R", "W"])
            w.at("2026-10-15T17:30")                         # w_reads = 1: not again
            self.assertEqual(w.status()["pool_total"], spent_r - 6)
            f = [e for e in events(w) if e["t"] == "flag" and e["track"] == "pin_solo"]
            self.assertEqual([(e["read"], e["window"]) for e in f], [("W", "R→W")])
            self.assertEqual(summary(w)["credits"]["w_reads"], 1)
            self.assertEqual(summary(w)["credits"]["w_credits"], 6)
            self.assertEqual([e for e in w.events() if e["t"] == "shown"], [])
        finally:
            w.close()

    def test_clv_against_pinnacles_closing_fair_comes_from_the_c_read(self):
        c = helpers.legacy_role_cfg()
        w = World(pool=(500, 500, 500, 500), config=c)
        try:
            build(w, hr_under=105)
            w.at(R_AT)
            build(w, pin=-0.03, others=-0.01, hr_under=105, lu="2026-10-15T21:10:00Z")
            w.at("2026-10-15T17:20")                          # W read: the flag
            inactives(w)
            build(w, pin=-0.04, others=-0.01, hr_under=105, lu=LU_C)
            w.at(C_AT)                                        # C read: Pinnacle's closing fair is 1 pt further our way
            cl = [e for e in events(w) if e["t"] == "close" and e["track"] == "pin_solo"]
            self.assertEqual(len(cl), 1)
            self.assertEqual(cl[0]["read_at"][:16], "2026-10-15T19:05")
            self.assertGreater(cl[0]["clv"], 0.0)
            t = track(w, "pin_solo")
            self.assertEqual((t["total"]["clv"]["n"], t["total"]["clv"]["positive"]), (1, 1))
            self.assertEqual(t["weeks"][0]["bets"][0]["clv"], cl[0]["clv"])
        finally:
            w.close()

    def test_no_w_when_credits_do_not_cover_r_and_c_for_every_game(self):
        w = World(pool=(40,), config=helpers.legacy_role_cfg())
        try:
            build(w, hr_under=105)
            w.at(R_AT)
            w.at("2026-10-15T17:20")
            self.assertEqual([d["pull"] for d in w.status()["due"] if d["game_id"] == SEA_DEN], [])
            ledger = common.read_json(w.path("state", "credits.json"))
            self.assertNotIn("W", [e["pull"] for e in ledger["spent"]])
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
