"""A full simulated Thursday, offline: SEA at DEN, Oct 15 2026 (8:15 PM ET), on the real Hard Rock payload.

  Tue 10:05 AM   markets check (M) then full pull (F); one Early pick (E. Wilson under 1.5 catches -110)
  Tue 10:15 AM   nothing due: no new credits, no new Record events
  Wed  5:10 PM   final injury report counts in (Wed 4 PM + 60 min): pick moves to "injury_report"; R refresh
  Thu  7:05 PM   inactives posted for both teams (3+ each): pick moves to "inactives"; C check (kickoff - 75 min)
  Thu  8:20 PM   kickoff passed: pick and anything open lock
  Thu 11:59 PM   ESPN says final, box score in: graded from the box (source espn_box), Record rebuilt
Every run checks the Record only grew at the end (helpers.World.at)."""
import json
import os
import unittest

import helpers
from helpers import SEA_DEN, WILSON, World
import common


def box(wilson_rec):
    """An ESPN summary for SEA at DEN with Wilson's receiving line (other players omitted)."""
    return {"header": {"competitions": [{"status": {"type": {"completed": True, "state": "post"}}}]},
            "boxscore": {"players": [{"team": {"abbreviation": "SEA"}, "statistics": [
                {"name": "receiving", "keys": ["receptions", "receivingYards", "yardsPerReception",
                                               "receivingTouchdowns", "longReception", "receivingTargets"],
                 "athletes": [{"athlete": {"id": "4887558"}, "stats": [str(wilson_rec), "9", "4.5", "0", "6", "2"]}]},
                {"name": "rushing", "keys": ["rushingAttempts", "rushingYards", "yardsPerRushAttempt",
                                             "rushingTouchdowns", "longRushing"],
                 "athletes": [{"athlete": {"id": "4887558"}, "stats": ["18", "77", "4.3", "1", "21"]}]}]}]}}


class Week(unittest.TestCase):
    def setUp(self):
        self.w = World(pool=(29, 500, 500, 326, 109, 500, 415))

    def tearDown(self):
        self.w.close()

    def test_thursday(self):
        w = self.w
        # ---- Tuesday morning: M then F, one early pick
        s = w.at("2026-10-13T10:05")
        st = w.status()
        pulls = [d["pull"] for d in st["due"] if d["game_id"] == SEA_DEN]
        self.assertEqual(pulls[:2], ["M", "F"])
        self.assertEqual(st["tier"], "full_plus")
        p = w.pick(WILSON)
        self.assertIsNotNone(p, s["picks"])
        self.assertEqual((p["bet"], p["price"], p["settled"], p["state"], p["book"]),
                         ("Under 1.5 receptions", -110, "early", "open", "Hard Rock"))
        self.assertEqual(p["id"], "2026-W06:2026_06_SEA_DEN:00-0038797:rec:FG:1.5:under")
        self.assertEqual((p["p_pct"], p["breakeven_pct"]), (57, 52))
        self.assertEqual(s["label"], "Week 6")
        self.assertIsNone(s["empty"])
        g = next(g for g in s["games"] if g["id"] == SEA_DEN)
        self.assertEqual((g["props_state"], g["settled"], g["started"]), ("ok", "early", False))
        # Jaxon Smith-Njigba is listed Questionable on the probe: never a pick
        self.assertIsNone(w.pick(helpers.JSN))
        credits = w.status()["pool_total"]
        n_events = len(w.events())

        # ---- ten minutes later: nothing due, no credits, no events
        w.at("2026-10-13T10:15")
        self.assertEqual(w.status()["pool_total"], credits)
        self.assertEqual(len(w.events()), n_events)

        # ---- Wednesday after the final report: injury_report + R refresh (same prices)
        w.set_odds(SEA_DEN, w.sea_den, "2026-10-14T21:00:00Z")
        s = w.at("2026-10-14T17:10")
        self.assertEqual(w.pick(WILSON)["settled"], "injury_report")
        self.assertIn("R", [d["pull"] for d in w.status()["due"] if d["game_id"] == SEA_DEN])
        hist = [h["text"] for h in w.pick(WILSON)["history"]]
        self.assertEqual(hist, ["Posted at -110", "Injury report in"])

        # ---- Thursday: inactives posted by both teams 90 minutes out; price moves -110 -> -115
        w.post_inactives(26, [("Zach Charbonnet", "RB"), ("Chazz Surratt", "LB"), ("Julian Neal", "CB")])
        w.post_inactives(7, [("Riley Moss", "CB"), ("Pat Surtain II", "CB"), ("Pat Bryant", "WR")])
        w.set_price(SEA_DEN, "player_receptions", "Emanuel Wilson", "under", -115, last_update_z="2026-10-15T22:40:00Z")
        w.set_price(SEA_DEN, "player_receptions", "Emanuel Wilson", "over", -115, last_update_z="2026-10-15T22:40:00Z")
        s = w.at("2026-10-15T19:05")
        p = w.pick(WILSON)
        self.assertEqual(p["settled"], "inactives")
        self.assertEqual((p["price"], p["first_price"], p["grade_price"]), (-115, -110, -115))
        self.assertIn("C", [d["pull"] for d in w.status()["due"] if d["game_id"] == SEA_DEN])
        ina = s["status"]["inactives"]
        self.assertEqual(ina["state"], "ok")
        res = helpers.load(w.path("research", "%s.json" % SEA_DEN))
        self.assertTrue(res["inactives"]["posted"])
        self.assertIn("Zach Charbonnet", [x["player"] for x in res["inactives"]["list"]])

        # ---- kickoff: locked, nothing open, no more pulls for this game
        s = w.at("2026-10-15T20:20")
        p = w.pick(WILSON)
        self.assertEqual((p["state"], p["rank"], p["settled"]), ("locked", None, "inactives"))
        self.assertNotIn(SEA_DEN, [d["game_id"] for d in (w.status()["due"] or [])])
        rec = helpers.load(w.path("record.json"))
        self.assertEqual(len(rec["weeks"]), 1)
        self.assertEqual(rec["weeks"][0]["entries"][0]["result"], None)       # on the Record, waiting

        # ---- final: ESPN box score grades it (1 catch: under 1.5 wins at -115)
        w.set_scoreboard("20261015", "401872995", "post", away=20, home=17)
        w.set_summary("401872995", box(1))
        s = w.at("2026-10-15T23:59")
        p = w.pick(WILSON)
        self.assertEqual(p["graded"], {"result": "W", "actual": "had 1", "units": round(100 / 115.0, 4)})
        ev = [e for e in w.events() if e["t"] == "graded"]
        self.assertEqual(ev[0]["source"], "espn_box")
        rec = helpers.load(w.path("record.json"))
        self.assertEqual((rec["summary"]["headline"]["w"], rec["summary"]["headline"]["l"]), (1, 0))
        self.assertEqual(rec["summary"]["rows"][0]["key"], "rec|FG")
        self.assertEqual(rec["summary"]["rows"][0]["label"], "Receptions")
        g = next(g for g in s["games"] if g["id"] == SEA_DEN)
        self.assertEqual((g["final"], g["score"]), (True, {"away": 20, "home": 17}))

        # ---- running again changes nothing in the Record
        n = len(w.events())
        w.at("2026-10-16T00:10")
        self.assertEqual(len(w.events()), n)
        # Wilson's events in the Record, in order
        pid = "2026-W06:2026_06_SEA_DEN:00-0038797:rec:FG:1.5:under"
        self.assertEqual([e["t"] for e in w.events() if e["id"] == pid],
                         ["shown", "state", "price", "state", "locked", "graded"])
        # every pick shown this week is on the Record (locked at kickoff), and one per player at most
        rec = helpers.load(w.path("record.json"))
        shown = [e["id"] for e in w.events() if e["t"] == "shown" and e["kind"] == "single"]
        self.assertEqual(sorted(x["id"] for x in rec["weeks"][0]["entries"] if x["kind"] == "single"), sorted(shown))
        players = [e["pick"]["player_id"] for e in w.events() if e["t"] == "shown" and e["kind"] == "single"]
        self.assertEqual(len(players), len(set(players)))

    def test_questionable_pulls_a_shown_pick(self):
        w = self.w
        w.at("2026-10-13T10:05")
        self.assertIsNotNone(w.pick(WILSON))
        w.set_injury(26, "Emanuel Wilson", "Questionable", pos="RB", espn_id="4887558")
        w.at("2026-10-13T11:00")
        p = w.pick(WILSON)
        self.assertEqual(p["pulled"]["reason"], "Listed questionable.")
        # cleared later in the week: the pick stays pulled and is never re-shown (sticky designation)
        w.set_injury(26, "Emanuel Wilson", "Active", pos="RB", espn_id="4887558")
        w.at("2026-10-14T17:10")
        picks = [x for x in w.slate()["picks"] if x["player_id"] == WILSON]
        self.assertEqual(len(picks), 1)
        self.assertIsNotNone(picks[0]["pulled"])

    def test_price_past_the_gate_pulls(self):
        w = self.w
        w.at("2026-10-13T10:05")
        w.set_price(SEA_DEN, "player_receptions", "Emanuel Wilson", "under", -215, last_update_z="2026-10-14T21:00:00Z")
        w.set_price(SEA_DEN, "player_receptions", "Emanuel Wilson", "over", 165, last_update_z="2026-10-14T21:00:00Z")
        w.at("2026-10-14T17:10")                                       # the R refresh reads the new price
        p = w.pick(WILSON)
        self.assertEqual(p["pulled"]["reason"], "Hard Rock moved it to -215, past our -200 limit.")
        self.assertEqual(p["price"], -110, "a price that caused a pull was never shown as live")
        rec = helpers.load(w.path("record.json"))
        self.assertEqual(rec["weeks"][0]["entries"][0]["pulled"]["reason"], p["pulled"]["reason"])

    def test_line_moved_pulls(self):
        w = self.w
        w.at("2026-10-13T10:05")
        for side in ("under", "over"):
            w.set_price(SEA_DEN, "player_receptions", "Emanuel Wilson", side, -110, line=2.5,
                        last_update_z="2026-10-14T21:00:00Z")
        w.at("2026-10-14T17:10")
        self.assertEqual(w.pick(WILSON)["pulled"]["reason"], "Hard Rock moved the line to 2.5.")


if __name__ == "__main__":
    unittest.main()
