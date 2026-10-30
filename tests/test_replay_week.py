"""A full simulated Thursday, offline: SEA at DEN, Oct 15 2026 (8:15 PM ET), Gap + Guard on a synthetic consensus.

Hard Rock's prices are the real SEA@DEN payload. The other books (Pinnacle + 6) are built from Hard Rock's own pairs
(helpers.add_books): no gap anywhere except where a test plants one.

  Tue 10:05 AM   nothing: no read is due before the final injury report, so there is no 'Early' pick
  Wed  5:10 PM   final injury report counts in (Wed 4 PM + 60 min): read R. Planted: E. Wilson under 17.5 carries +100 (+4% gap, model
                 agrees) = the one pick; Wilson's second leg (rec yds) is capped (1 per player); S. Darnold over 218.5 pass yds
                 (+3.5% gap, model says no) = a veto in the research log; D. Dobbins has only 3 other books = nothing
  Thu  7:05 PM   inactives posted for both teams: read C (kickoff - 70 min): pick moves to 'Inactives confirmed', price final
  Thu  8:20 PM   kickoff passed: locked
  Thu 11:59 PM   ESPN says final, box score in: graded from the box (source espn_box) at the frozen price
Every run checks the Record only grew at the end (helpers.World.at)."""
import json
import os
import unittest

import helpers
from helpers import SEA_DEN, WILSON, World, add_books
import common

EDGES = {("player_reception_yds", "Emanuel Wilson", "under"): 0.04,
         ("player_rush_attempts", "Emanuel Wilson", "under"): 0.04,
         ("player_pass_yds", "Sam Darnold", "over"): 0.035}


def box(wilson_rec, wilson_yds="9", carries="15"):
    """An ESPN summary for SEA at DEN with Wilson's receiving line (other players omitted)."""
    return {"header": {"competitions": [{"status": {"type": {"completed": True, "state": "post"}}}]},
            "boxscore": {"players": [{"team": {"abbreviation": "SEA"}, "statistics": [
                {"name": "receiving", "keys": ["receptions", "receivingYards", "yardsPerReception",
                                               "receivingTouchdowns", "longReception", "receivingTargets"],
                 "athletes": [{"athlete": {"id": "4887558"}, "stats": [str(wilson_rec), wilson_yds, "4.5", "0", "6", "2"]}]},
                {"name": "rushing", "keys": ["rushingAttempts", "rushingYards", "yardsPerRushAttempt",
                                             "rushingTouchdowns", "longRushing"],
                 "athletes": [{"athlete": {"id": "4887558"}, "stats": [carries, "77", "4.3", "1", "21"]}]}]}]}}


def gg_log(w):
    p = w.path("gg", "events.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(l) for l in f.read().splitlines() if l]


class Week(unittest.TestCase):
    def setUp(self):
        self.w = World(pool=(29, 500, 500, 326, 109, 500, 415))
        self.w.set_odds(SEA_DEN, add_books(self.w.sea_den, EDGES), "2026-10-14T21:00:00Z")

    def tearDown(self):
        self.w.close()

    def test_thursday(self):
        w = self.w
        # ---- Tuesday morning: no read is due before the final injury report; no pick, no credits spent
        s = w.at("2026-10-13T10:05")
        self.assertEqual(s["picks"], [])
        self.assertEqual(w.status()["due"], [])
        self.assertEqual(w.status()["pool_total"], 29 + 500 + 500 + 326 + 109 + 500 + 415)
        self.assertEqual(s["schema"], 2)
        self.assertEqual(s["empty"]["code"], "no_picks_yet")

        # ---- Wednesday 5:10 PM: the final report is in; read R; one pick
        s = w.at("2026-10-14T17:10")
        st = w.status()
        due = [d for d in st["due"] if d["game_id"] == SEA_DEN]
        self.assertEqual([d["pull"] for d in due], ["R"])
        self.assertEqual(due[0]["cost_max"], 6)
        self.assertEqual(st["tier"], "full")
        picks = [p for p in s["picks"] if p["state"] == "open"]
        self.assertEqual(len(picks), 1, [(p["player"], p["bet"]) for p in s["picks"]])
        p = picks[0]
        self.assertEqual((p["player_id"], p["market"], p["bet"], p["price"], p["book"], p["state"], p["settled"]),
                         (WILSON, "rush_att", "Under 17.5 carries", 100, "Hard Rock", "open", "injury_report"))
        self.assertEqual(p["id"], "2026-W06:2026_06_SEA_DEN:00-0038797:rush_att:17.5:under")
        self.assertEqual((p["read_pull"], p["settled_text"], p["n_books"], p["pinnacle"], p["rank"]),
                         ("R", "Injury report in", 7, True, 1))
        self.assertTrue(3.5 <= p["gap_pct"] <= 4.5, p["gap_pct"])
        self.assertTrue(p["vs"][:2] in ("+3", "+4") and p["vs"].endswith("% vs 7 books (Pinnacle in)"), p["vs"])
        self.assertGreaterEqual(p["final_ev_pct"], 2.0)
        self.assertEqual(p["flags"]["price_final"], False)
        self.assertEqual(p["flags"]["weather"]["state"], "not_checked")
        self.assertIsNone(p["pulled"])
        self.assertEqual(p["game"], "SEA @ DEN")
        self.assertEqual(s["games"][0]["settled"] if s["games"][0]["id"] == SEA_DEN else
                         next(g for g in s["games"] if g["id"] == SEA_DEN)["settled"], "injury_report")
        # no parlays, no 1st half, no research, no Early anywhere
        self.assertNotIn("parlays", s)
        self.assertFalse(os.path.exists(w.path("research")))
        self.assertNotIn("early", json.dumps(s["picks"]).lower())
        shown = [e for e in w.events() if e["t"] == "shown"]
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0]["pick"]["era"], "gg1")
        self.assertEqual(shown[0]["pick"]["period"], "FG")
        self.assertEqual(shown[0]["pick"]["read_pull"], "R")
        # Jaxon Smith-Njigba is listed Questionable: never a pick. Wilson's rush attempts leg is capped (1 per player)
        self.assertIsNone(w.pick(helpers.JSN))
        log = gg_log(w)
        self.assertEqual(sorted((e["t"], e["player"], e["market"], e["side"]) for e in log),
                         [("capped", "Emanuel Wilson", "rec_yds", "under"),
                          ("veto", "Sam Darnold", "pass_yds", "over")])
        credits = w.status()["pool_total"]
        n_events = len(w.events())
        self.assertEqual(credits, 29 + 500 + 500 + 326 + 109 + 500 + 415 - 6)     # one R read: six markets, one region

        # ---- ten minutes later: nothing due, no credits, no new events
        w.at("2026-10-14T17:20")
        self.assertEqual(w.status()["pool_total"], credits)
        self.assertEqual(len(w.events()), n_events)

        # ---- Thursday 7:05 PM: inactives posted by both teams; read C; the pick is still good and 'price final'
        w.post_inactives(26, [("Zach Charbonnet", "RB"), ("Chazz Surratt", "LB"), ("Julian Neal", "CB")])
        w.post_inactives(7, [("Riley Moss", "CB"), ("Pat Surtain II", "CB"), ("Pat Bryant", "WR")])
        w.set_odds(SEA_DEN, add_books(w.sea_den, EDGES, last_update_z="2026-10-15T23:00:00Z"), "2026-10-15T23:00:00Z")
        s = w.at("2026-10-15T19:05")
        self.assertIn("C", [d["pull"] for d in w.status()["due"] if d["game_id"] == SEA_DEN])
        p = w.pick(WILSON)
        self.assertEqual((p["settled"], p["state"], p["price"], p["flags"]["price_final"]),
                         ("inactives", "open", 100, True))
        self.assertEqual(p["settled_text"], "Inactives confirmed")
        self.assertEqual([h["text"] for h in p["history"]], ["Posted at +100", "Inactives confirmed"])
        self.assertEqual(s["status"]["inactives"]["state"], "ok")

        # ---- kickoff: locked, nothing open, no more pulls for this game
        s = w.at("2026-10-15T20:20")
        p = w.pick(WILSON)
        self.assertEqual((p["state"], p["rank"], p["settled"]), ("locked", None, "inactives"))
        self.assertNotIn(SEA_DEN, [d["game_id"] for d in (w.status()["due"] or [])])
        rec = helpers.load(w.path("record.json"))
        self.assertEqual(len(rec["weeks"]), 1)
        self.assertEqual(rec["weeks"][0]["entries"][0]["result"], None)       # on the Record, waiting

        # ---- final: ESPN box score grades it (15 carries: under 17.5 wins at +100)
        w.set_scoreboard("20261015", "401872995", "post", away=20, home=17)
        w.set_summary("401872995", box(1))
        s = w.at("2026-10-15T23:59")
        p = w.pick(WILSON)
        self.assertEqual(p["graded"], {"result": "W", "actual": "had 15", "units": 1.0})
        ev = [e for e in w.events() if e["t"] == "graded"]
        self.assertEqual(ev[0]["source"], "espn_box")
        rec = helpers.load(w.path("record.json"))
        self.assertEqual((rec["summary"]["headline"]["w"], rec["summary"]["headline"]["l"]), (1, 0))
        self.assertEqual(rec["summary"]["rows"][0]["key"], "rush_att|FG")
        self.assertEqual(rec["summary"]["rows"][0]["label"], "Carries")
        self.assertIsNone(rec["retired"])
        g = next(g for g in s["games"] if g["id"] == SEA_DEN)
        self.assertEqual((g["final"], g["score"]), (True, {"away": 20, "home": 17}))

        # ---- running again changes nothing in the Record
        n = len(w.events())
        w.at("2026-10-16T00:10")
        self.assertEqual(len(w.events()), n)
        pid = "2026-W06:2026_06_SEA_DEN:00-0038797:rush_att:17.5:under"
        self.assertEqual([e["t"] for e in w.events() if e["id"] == pid], ["shown", "state", "locked", "graded"])
        # the closing consensus on the C read was logged for the pick and the veto (research only)
        closes = [e for e in gg_log(w) if e["t"] == "close"]
        self.assertEqual(sorted(e["id"].split(":")[3] for e in closes), ["pass_yds", "rush_att"])
        self.assertTrue(all(e["close_books"] == 7 for e in closes))

    def test_questionable_pulls_a_shown_pick(self):
        w = self.w
        w.at("2026-10-14T17:10")
        self.assertIsNotNone(w.pick(WILSON))
        w.set_injury(26, "Emanuel Wilson", "Questionable", pos="RB", espn_id="4887558")
        w.at("2026-10-14T17:40")
        p = w.pick(WILSON)
        self.assertEqual(p["pulled"]["reason"], "Listed questionable.")
        self.assertIn("If you already bet it at +100", p["pulled"]["if_bet"])
        # cleared later: the pick stays pulled and is never re-shown (sticky designation)
        w.set_injury(26, "Emanuel Wilson", "Active", pos="RB", espn_id="4887558")
        w.at("2026-10-15T09:00")
        picks = [x for x in w.slate()["picks"] if x["player_id"] == WILSON]
        self.assertEqual(len(picks), 1)
        self.assertIsNotNone(picks[0]["pulled"])

    def test_c_read_takes_the_pick_back_when_the_gap_is_gone(self):
        w = self.w
        w.at("2026-10-14T17:10")
        self.assertIsNotNone(w.pick(WILSON))
        no_edge = {k: v for k, v in EDGES.items() if k[1] != "Emanuel Wilson"}
        w.post_inactives(26, [("Zach Charbonnet", "RB"), ("Chazz Surratt", "LB"), ("Julian Neal", "CB")])
        w.post_inactives(7, [("Riley Moss", "CB"), ("Pat Surtain II", "CB"), ("Pat Bryant", "WR")])
        # Hard Rock moves to -120 while the other books stay at about even money: the gap is gone
        w.set_price(SEA_DEN, "player_rush_attempts", "Emanuel Wilson", "under", -120, last_update_z="2026-10-15T23:00:00Z")
        w.set_price(SEA_DEN, "player_rush_attempts", "Emanuel Wilson", "over", -110, last_update_z="2026-10-15T23:00:00Z")
        w.set_odds(SEA_DEN, add_books(helpers.load(os.path.join(w.odds, "odds_%s.json" % SEA_DEN)), no_edge,
                                      last_update_z="2026-10-15T23:00:00Z"), "2026-10-15T23:00:00Z")
        w.at("2026-10-15T19:05")
        p = w.pick(WILSON)
        self.assertEqual(p["state"], "pulled")
        self.assertEqual(p["pulled"]["code"], "gap_gone")
        self.assertEqual(p["pulled"]["now_price"], -120)
        self.assertEqual(p["price"], 100, "the frozen price is what the Record keeps")
        self.assertIn("If you already bet it at +100: Hard Rock is now -120", p["pulled"]["if_bet"])

    def test_line_moved_pulls(self):
        w = self.w
        w.at("2026-10-14T17:10")
        self.assertIsNotNone(w.pick(WILSON))
        w.post_inactives(26, [("Zach Charbonnet", "RB"), ("Chazz Surratt", "LB"), ("Julian Neal", "CB")])
        w.post_inactives(7, [("Riley Moss", "CB"), ("Pat Surtain II", "CB"), ("Pat Bryant", "WR")])
        for side in ("under", "over"):
            w.set_price(SEA_DEN, "player_rush_attempts", "Emanuel Wilson", side, -115, line=18.5,
                        last_update_z="2026-10-15T23:00:00Z")
        w.set_odds(SEA_DEN, add_books(helpers.load(os.path.join(w.odds, "odds_%s.json" % SEA_DEN)), {},
                                      last_update_z="2026-10-15T23:00:00Z"), "2026-10-15T23:00:00Z")
        w.at("2026-10-15T19:05")
        p = w.pick(WILSON, market="rush_att")
        self.assertEqual(p["pulled"]["reason"], "Hard Rock moved the line to 18.5.")


class InjuriesDown(unittest.TestCase):
    """The injury feed dies after Tuesday. A later C read (odds fixtures unchanged) must not create a pick; once injuries
    answer again, the same read does."""

    def test_no_new_pick_without_a_fresh_injury_read(self):
        w = World(pool=(29, 500, 500, 326, 109, 500, 415))
        try:
            w.set_odds(SEA_DEN, add_books(w.sea_den, {}), "2026-10-14T21:00:00Z")      # no gap anywhere on the R read
            s = w.at("2026-10-14T17:10")                                 # final report in, injuries read fine, no pick
            self.assertEqual(s["picks"], [])
            w.set_odds(SEA_DEN, add_books(w.sea_den, EDGES, last_update_z="2026-10-15T23:00:00Z"), "2026-10-15T23:00:00Z")
            os.unlink(os.path.join(w.feed, "espn_injuries.json"))        # then the injury feed stops answering
            s = w.at("2026-10-15T19:30")
            self.assertTrue(any(d["pull"] == "C" and d["game_id"] == SEA_DEN for d in w.status()["due"]), w.status()["due"])
            self.assertEqual([e for e in w.events() if e["t"] == "shown"], [])
            self.assertEqual(s["picks"], [])
            self.assertTrue(any("Injury news isn't loading" in b["text"] for b in s["banners"]), s["banners"])
            self.assertEqual(s["status"]["injuries"]["state"], "down")
            helpers.write(os.path.join(w.feed, "espn_injuries.json"), w.injuries)      # the feed is back
            s = w.at("2026-10-15T19:40")
            self.assertEqual(s["status"]["injuries"]["state"], "ok")
            self.assertEqual([p["player_id"] for p in s["picks"] if p["state"] == "open"], [WILSON])
            self.assertFalse(any("Injury news" in b["text"] for b in s["banners"]))
        finally:
            w.close()

    def test_an_old_injury_read_also_blocks(self):
        w = World(pool=(29, 500, 500, 326, 109, 500, 415))
        try:
            import run, common
            w.at("2026-10-14T17:10")
            r = run.Run(helpers.cfg(), common.parse_iso("2026-10-14T19:20:00-04:00"), readonly=True)
            self.assertEqual(r.injuries_problem(), "the last injury read is old")      # 130 min after the last good read
            r = run.Run(helpers.cfg(), common.parse_iso("2026-10-14T18:30:00-04:00"), readonly=True)
            self.assertIsNone(r.injuries_problem())
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
