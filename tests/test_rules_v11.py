"""Live rule values against the customer brief (Oct 30), the role_unstable rule and the room_change rule (a big-share
WR/TE/RB joins or leaves a team)."""
import os
import unittest
from datetime import timedelta

import helpers
from helpers import SEA_DEN, WILSON, World
import common
import engine
import model
import stats

LIVE = helpers.cfg()
V = LIVE["values"]


class Values(unittest.TestCase):
    def test_live_values_match_the_customer_rules(self):
        g = LIVE["ops"]["gap_guard"]["thresholds"]
        self.assertEqual((V["price_gate"], V["max_price"]), (-150, 120))
        self.assertEqual((g["gap_ev"], g["books_min"], g["pin_ev_min"], g["final_ev"]), (0.03, 4, 0, 0.02))
        self.assertEqual((g["market_weight"], g["model_weight"]), (0.8, 0.2))
        self.assertEqual(g["flag_pulls"], ["R", "C"])
        self.assertEqual(LIVE["ops"]["book"], "hardrockbet_fl")
        self.assertEqual(LIVE["ops"]["never_book"], ["hardrockbet"])
        self.assertNotIn("hardrockbet", LIVE["ops"]["consensus_books"])
        self.assertIn("pinnacle", LIVE["ops"]["consensus_books"])
        import odds
        self.assertEqual(len(odds.request_books(LIVE)), 10)                      # Hard Rock + 9: one billed region
        self.assertEqual(LIVE["ops"]["monthly_cap"], 1000)
        self.assertEqual(sorted(LIVE["ops"]["gap_guard"]["markets"]),
                         ["pass_td", "pass_yds", "rec", "rec_yds", "rush_att", "rush_yds"])
        engine.check_config(LIVE)

    def test_no_parlays_no_first_half_no_alt_lines_in_the_markets(self):
        for name, m in LIVE["ops"]["markets"].items():
            self.assertEqual(m["period"], "FG", name)
            self.assertNotIn("alternate", m["feed_key"])
            self.assertNotIn("h1", m["feed_key"])
        self.assertTrue(all("|FG|" in k for k in V["shrink"]))
        self.assertNotIn("parlay", " ".join(LIVE["ops"]) + " ".join(V))

    def test_role_unstable_blocks_unders_when_both(self):
        f = ["role_unstable"]
        self.assertTrue(model.pick_ok(f, "under"))                      # v1.0 default
        self.assertFalse(model.pick_ok(f, "under", "both"))
        self.assertFalse(model.pick_ok(f, "over", "both"))
        self.assertTrue(model.pick_ok(["role_jump"], "under", "both"))
        self.assertEqual(V["elig"]["role_cv_blocks"], "both")


# ---------------------------------------------------------------- room change
def store_with(players, games_by_team, rows):
    """players {pid: [name, pos]}; games_by_team {team: [(week, opp)]}; rows [(pid, team, week, tg, ra)].
    Each team game also gets a filler teammate so team totals are known."""
    team_rows, prow = [], []
    for team, gl in games_by_team.items():
        for wk, opp in gl:
            date = _date(wk)
            gid = "2026_%02d_%s" % (wk, "_".join(sorted((team, opp))))
            team_rows.append({"gid": gid, "team": team, "opp": opp, "season": 2026, "week": wk, "date": date})
            prow.append({"gid": gid, "pid": "fill-" + team, "team": team, "season": 2026, "week": wk, "date": date,
                         "tg": 20, "ra": 20})
    for pid, team, wk, tg, ra in rows:
        opp = dict(games_by_team[team])[wk]
        prow.append({"gid": "2026_%02d_%s" % (wk, "_".join(sorted((team, opp)))), "pid": pid, "team": team,
                     "season": 2026, "week": wk, "date": _date(wk), "tg": tg, "ra": ra})
    players = dict(players)
    players.update({"fill-" + t: ["Filler %s" % t, "WR"] for t in games_by_team})
    return stats.Store({2026: {"players": players, "rows": prow, "team_rows": team_rows, "games": {}}})


def _date(wk):
    from datetime import date
    return (date(2026, 9, 13) + timedelta(days=7 * (wk - 1))).isoformat()


def inputs(store, people, week):
    g = {"id": "2026_%02d_X" % week, "date": _date(week), "away": "HOU", "home": "ARI",
         "start": _date(week) + "T13:00:00-04:00"}
    people = dict(people)
    for t in ("ARI", "HOU", "SEA", "JAX"):                          # the filler teammates are on the roster
        people.setdefault("fill-" + t, {"name": "Filler", "team": t, "pos": "WR", "status": "ACT"})
    inp = engine.Inputs(LIVE, common.parse_iso(_date(week) + "T09:00:00-04:00"), "2026-W%02d" % week, 2026, [g],
                        store, {}, {}, {}, {}, people, [])
    return inp, g


class RoomChange(unittest.TestCase):
    TEAMS = {"ARI": [(w, "SEA") for w in range(1, 8)], "HOU": [(w, "JAX") for w in range(1, 8)],
             "SEA": [(w, "ARI") for w in range(1, 8)], "JAX": [(w, "HOU") for w in range(1, 8)]}

    def person(self, team, status="ACT", **kw):
        return dict({"name": "x", "team": team, "pos": "WR", "status": status}, **kw)

    def test_join_blocks_new_team_room_until_he_has_played_two(self):
        rows = [("mhj", "ARI", w, 8, 0) for w in (1, 2, 3, 4)]               # 8 of 28 targets = 29%
        people = {"mhj": self.person("HOU", name="Marvin Harrison Jr."), "fill-HOU": self.person("HOU")}
        st = store_with({"mhj": ["Marvin Harrison Jr.", "WR"]}, self.TEAMS, rows)
        inp, g = inputs(st, people, 6)
        self.assertEqual(inp.room_change(g, "HOU", "rec_yds"),
                         "Marvin Harrison Jr. joined HOU (0 games with them): receiving picks wait 2 games")
        self.assertIsNone(inp.room_change(g, "HOU", "rush_yds"))
        self.assertIsNone(inp.room_change(g, "HOU", "pass_yds"))
        # one game for HOU: still waiting; two: clear
        st = store_with({"mhj": ["Marvin Harrison Jr.", "WR"]}, self.TEAMS, rows + [("mhj", "HOU", 6, 6, 0)])
        inp, g = inputs(st, people, 7)
        self.assertIn("1 game with them", inp.room_change(g, "HOU", "rec"))
        st = store_with({"mhj": ["Marvin Harrison Jr.", "WR"]}, self.TEAMS,
                        rows + [("mhj", "HOU", 6, 6, 0), ("mhj", "HOU", 7, 6, 0)])
        inp, g = inputs(st, people, 8)
        self.assertIsNone(inp.room_change(g, "HOU", "rec"))

    def test_leave_blocks_old_team_room_for_two_team_games(self):
        rows = [("mhj", "ARI", w, 8, 0) for w in (1, 2, 3, 4)]
        people = {"mhj": self.person("HOU", name="Marvin Harrison Jr.")}
        st = store_with({"mhj": ["Marvin Harrison Jr.", "WR"]}, self.TEAMS, rows)
        inp, g = inputs(st, people, 6)                                        # ARI played week 5 only since
        self.assertEqual(inp.room_change(g, "ARI", "rec_yds"),
                         "Marvin Harrison Jr. left ARI (now with HOU): receiving picks wait 2 games")
        inp, g = inputs(st, people, 7)                                        # ARI played weeks 5 and 6
        self.assertIsNone(inp.room_change(g, "ARI", "rec_yds"))

    def test_reserve_and_release_are_leaves_weekly_out_is_not(self):
        rows = [("rb", "ARI", w, 1, 15) for w in (1, 2, 3, 4, 5)]           # 15 of 35 carries = 43%
        st = store_with({"rb": ["Run Back", "RB"]}, self.TEAMS, rows)
        for p, want in ((self.person("ARI", "RES", pos="RB"), "on reserve (IR/PUP/NFI)"),
                        ({"name": "Run Back", "team": "ARI", "pos": "RB", "from_rows": True}, "off the roster"),
                        (None, "off the roster")):
            people = {"fill-ARI": self.person("ARI")}
            if p:
                people["rb"] = p
            inp, g = inputs(st, people, 6)
            self.assertIn(want, inp.room_change(g, "ARI", "rush_att") or "", p)
        people = {"rb": self.person("ARI", "ACT", pos="RB"), "fill-ARI": self.person("ARI")}   # weekly Out: ACT
        inp, g = inputs(st, people, 6)
        self.assertIsNone(inp.room_change(g, "ARI", "rush_att"))

    def test_small_share_is_ignored(self):
        rows = [("wr3", "ARI", w, 1, 0) for w in (1, 2, 3, 4)]               # 1 of 21 = 5%
        st = store_with({"wr3": ["Wide Three", "WR"]}, self.TEAMS, rows)
        inp, g = inputs(st, {"wr3": self.person("HOU"), "fill-HOU": self.person("HOU")}, 6)
        self.assertIsNone(inp.room_change(g, "HOU", "rec"))
        self.assertIsNone(inp.room_change(g, "ARI", "rec"))


if __name__ == "__main__":
    unittest.main()
