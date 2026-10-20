"""Rules v1.1 (Sharp, Oct 20): too_good 0.15, role_unstable blocks unders, no new pick from a price 6h+ old,
parlays capped at 2 a game day with each leg in 1 (counting every parlay posted, locked and pulled too), and the
room_change rule (a big-share WR/TE/RB joins or leaves a team)."""
import os
import types
import unittest
from datetime import timedelta

import helpers
from helpers import SEA_DEN, WILSON, World
import common
import engine
import model
import record
import run
import stats

LIVE = helpers.cfg()
V = LIVE["values"]


class Values(unittest.TestCase):
    def test_values(self):
        self.assertEqual((V["too_good"], V["news_gap"]), (0.15, 0.15))
        self.assertEqual(V["elig"]["role_cv_blocks"], "both")
        self.assertEqual(V["new_pick_max_price_age_hours"], 6)
        self.assertEqual((V["parlay_cap"], V["parlay_leg_max_use"]), (2, 1))
        engine.check_config(LIVE)

    def test_news_gap_above_too_good_is_refused(self):
        bad = {"values": dict(V, news_gap=0.2, too_good=0.15)}
        with self.assertRaises(engine.ConfigError):
            engine.check_config(bad)

    def test_role_unstable_blocks_unders_when_both(self):
        f = ["role_unstable"]
        self.assertTrue(model.pick_ok(f, "under"))                      # v1.0 default
        self.assertFalse(model.pick_ok(f, "under", "both"))
        self.assertFalse(model.pick_ok(f, "over", "both"))
        self.assertTrue(model.pick_ok(["role_jump"], "under", "both"))

    def test_stale_price(self):
        now = common.parse_iso("2026-10-20T16:00:00-04:00")
        self.assertFalse(engine.stale_price(now, now - timedelta(hours=6), V))
        self.assertTrue(engine.stale_price(now, now - timedelta(hours=6, minutes=1), V))
        self.assertTrue(engine.stale_price(now, "2026-10-20T09:00:00-04:00", V))
        self.assertFalse(engine.stale_price(now, None, V))


class OnTheReplay(unittest.TestCase):
    """SEA at DEN: the Tuesday pick (Wilson under 1.5 catches) under the v1.0 role rule, then v1.1."""

    def setUp(self):
        self.w = World()

    def tearDown(self):
        self.w.close()

    def test_old_price_makes_no_new_pick(self):
        self.w.at("2026-10-13T10:05")
        cfg = helpers.legacy_role_cfg()

        def verdict(now):
            r = run.Run(cfg, common.parse_iso(now), readonly=True)
            r.load_schedule()
            rows = engine.explain(r.build_inputs())
            return next(x["verdict"] for x in rows if x["player"] == "Emanuel Wilson" and x["market"] == "rec"
                        and x["side"] == "under")
        self.assertEqual(verdict("2026-10-13T15:30:00-04:00"), "QUALIFIES")
        self.assertEqual(verdict("2026-10-13T16:30:00-04:00"), engine.STALE)

    def test_shown_pick_failing_a_tightened_rule_is_pulled(self):
        w = self.w
        w.at("2026-10-13T10:05")
        self.assertEqual(w.pick(WILSON)["state"], "open")
        n = len(w.events())
        helpers.write(os.environ["PC_CONFIG"], LIVE)              # the v1.1 rules arrive
        w.at("2026-10-13T10:15")
        p = w.pick(WILSON)
        self.assertEqual(p["pulled"]["reason"], "Rules tightened: His role swings too much game to game.")
        ev = w.events()
        self.assertEqual([e["t"] for e in ev[n:] if e["id"] == p["id"]], ["pulled"])


# ---------------------------------------------------------------- parlays: every posted parlay counts
def leg(i, day, game, p=0.66, price=-120):
    return i, {"p": p, "b": 0.5455, "price": price, "settled": "inactives",
               "price_at": common.parse_iso(day + "T10:00:00-04:00"), "pid": "p" + i, "team": "T" + game,
               "game_id": game, "start": day + "T13:00:00-04:00"}


def shown_single(i, day, game):
    return {"t": "shown", "kind": "single", "id": i, "at": day + "T09:00:00-04:00", "slate": "2026-W07",
            "pick": {"id": i, "game_id": game, "player_id": "p" + i, "team": "T" + game,
                     "start": day + "T13:00:00-04:00", "price": -120, "price_at": day + "T09:00:00-04:00"}}


def shown_parlay(pid, legs, day):
    return {"t": "shown", "kind": "parlay", "id": pid, "at": day + "T09:00:00-04:00", "slate": "2026-W07",
            "parlay": {"id": pid, "legs": legs, "n": len(legs), "price": 264, "price_at": day + "T09:00:00-04:00",
                       "start": day + "T13:00:00-04:00"}}


class ParlayCaps(unittest.TestCase):
    def run_parlays(self, events, legs, v=V):
        inp = types.SimpleNamespace(v=v, slate="2026-W07")
        return [e for e in engine._parlays(inp, record.fold(events), dict(legs), "2026-10-25T10:00:00-04:00")
                if e["t"] == "shown"]

    def test_locked_parlays_count_toward_the_day_cap(self):
        day = "2026-10-25"
        early = [shown_single("a", day, "g1"), shown_single("b", day, "g2"),
                 {"t": "locked", "id": "a", "at": day + "T13:00:00-04:00"},
                 {"t": "locked", "id": "b", "at": day + "T13:00:00-04:00"},
                 shown_parlay("2026-W07:P:old", ["a", "b"], day),
                 {"t": "locked", "id": "2026-W07:P:old", "at": day + "T13:00:00-04:00"}]
        late = [leg(c, day, "g" + c) for c in "cdefgh"]
        shown = self.run_parlays(early, late)
        self.assertEqual(len(shown), V["parlay_cap"] - 1)               # 2 a day, 1 already posted (locked)
        two_locked = early + [shown_parlay("2026-W07:P:old2", ["a", "b"], day),
                              {"t": "locked", "id": "2026-W07:P:old2", "at": day + "T13:00:00-04:00"}]
        self.assertEqual(self.run_parlays(two_locked, late), [])

    def test_a_leg_in_a_pulled_parlay_is_used(self):
        day = "2026-10-25"
        ev = [shown_single("c", day, "gc"), shown_single("x", day, "gx"),
              shown_parlay("2026-W07:P:px", ["c", "x"], day),
              {"t": "pulled", "id": "x", "at": day + "T09:30:00-04:00", "reason": "Listed out."},
              {"t": "pulled", "id": "2026-W07:P:px", "at": day + "T09:30:00-04:00", "reason": "x leg pulled."}]
        live = [leg(c, day, "g" + c) for c in "cde"]
        shown = self.run_parlays(ev, live)
        self.assertTrue(shown)
        for e in shown:
            self.assertNotIn("c", e["parlay"]["legs"])                  # c already in a parlay today (max use 1)

    def test_each_leg_in_one_parlay(self):
        day = "2026-10-25"
        shown = self.run_parlays([], [leg(c, day, "g" + c) for c in "abcdefgh"])
        self.assertEqual(len(shown), 2)
        used = [l for e in shown for l in e["parlay"]["legs"]]
        self.assertEqual(len(used), len(set(used)))


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
        self.assertEqual(inp.room_change(g, "ARI", "long_rec"),
                         "Marvin Harrison Jr. left ARI (now with HOU): receiving picks wait 2 games")
        inp, g = inputs(st, people, 7)                                        # ARI played weeks 5 and 6
        self.assertIsNone(inp.room_change(g, "ARI", "long_rec"))

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
