"""Data Scientist self-tests for nflrows.py and model.py (unittest, stdlib, no network, ~1s).

Fixture: fixtures/pbp_sample.csv.gz = every play of two real 2026 games (nflverse pbp, trimmed columns):
  2026_03_ARI_SF  lateral TD (Evans catch 2 yds, lateral to Samuel 80 yds TD), kneel, spike, sacks
  2026_01_NO_DET  overtime game with a failed 2-point try in OT
fixtures/stats_sample.csv = nflverse stats_player_week rows for those two games (the official book).
"""
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import model as M  # noqa: E402
import nflrows  # noqa: E402

FX = os.path.join(HERE, "fixtures")


def mk(i, season=2026, pos_stats=None, **kw):
    """Synthetic player-game row, one per week, newest has the largest i."""
    r = dict(gid="g%d" % i, pid="p", team="AAA", opp="BBB", home=1, season=season, week=1 + i % 18,
             date="%d-%02d-%02d" % (season, 9 + i // 28, 1 + i % 28), snap=0.8)
    for s in nflrows.STATS:
        r[s] = 0
        r["h_" + s] = 0
    r.update(kw)
    return r


def wr(i, tg, rc, rcy, season=2026, **kw):
    return mk(i, season, tg=tg, rc=rc, rcy=rcy, lr=min(rcy, 25), h_tg=tg // 2, h_rc=rc // 2, h_rcy=rcy // 2,
              h_lr=min(rcy // 2, 20), **kw)


class RowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = nflrows.build([os.path.join(FX, "pbp_sample.csv.gz")])
        cls.idx = {(r["gid"], r["pid"]): r for r in cls.d["rows"]}

    def test_matches_official_book(self):
        res, miss = nflrows.check(self.d, [os.path.join(FX, "stats_sample.csv")])
        for k, (a, n) in res.items():
            self.assertGreater(n, 30)
            self.assertEqual(a, n, (k, miss.get(k)))

    def test_lateral(self):
        evans = self.idx[("2026_03_ARI_SF", "00-0031408")]
        samuel = self.idx[("2026_03_ARI_SF", "00-0035719")]
        self.assertEqual((evans["rc"], evans["rcy"], evans["rct"]), (3, 34, 1))
        # Samuel: 80 lateral yards and the TD, but no reception and the lateral is not his 'longest catch'
        self.assertEqual((samuel["rc"], samuel["rcy"], samuel["rct"], samuel["lr"]), (0, 80, 1, 0))
        self.assertEqual(nflrows.actual(samuel, "anytime_td", "1H"), 1)
        self.assertEqual(nflrows.grade(samuel, "rec_yds", "1H", 49.5), "win")

    def test_halves_and_overtime(self):
        purdy = self.idx[("2026_03_ARI_SF", "00-0037834")]
        self.assertEqual((purdy["h_pa"], purdy["h_py"], purdy["h_ptd"]), (17, 204, 2))
        self.assertEqual((purdy["pa"], purdy["py"], purdy["ptd"]), (27, 297, 4))
        g = self.d["games"]["2026_01_NO_DET"]
        self.assertEqual((g["hs1"], g["as1"], g["hs"], g["as"]), (7, 0, 31, 30))
        # OT plays count for FG but never for 1H; every 1H stat <= FG stat
        for r in self.d["rows"]:
            for s in nflrows.STATS:
                self.assertLessEqual(r["h_" + s], r[s], (r["pid"], s))

    def test_two_point_not_counted(self):
        # B.Lance was targeted on the failed OT 2-pt try; that is not a target (official book agrees)
        import csv
        import gzip
        plays = [p for p in csv.DictReader(gzip.open(os.path.join(FX, "pbp_sample.csv.gz"), "rt"))
                 if p["game_id"] == "2026_01_NO_DET" and p["receiver_player_name"] == "B.Lance" and p["play_type"] == "pass"]
        two = [p for p in plays if p["two_point_attempt"] == "1"]
        self.assertEqual(len(two), 1)
        lance = [r for r in self.d["rows"] if r["gid"] == "2026_01_NO_DET" and self.d["players"][r["pid"]][0] == "B.Lance"]
        self.assertEqual(lance[0]["tg"], len(plays) - 1)

    def test_team_rows(self):
        t = {(x["gid"], x["team"]): x for x in self.d["team_rows"]}
        sf = t[("2026_03_ARI_SF", "SF")]
        self.assertEqual((sf["pts"], sf["opp_pts"], sf["h_pts"], sf["h_opp_pts"]), (36, 30, 17, 6))
        self.assertEqual(sf["plays"], sf["pa"] + sf["sk"] + sf["ra"])

    def test_dump_load_roundtrip(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "x.json.gz")
        nflrows.dump(self.d, p)
        d2 = nflrows.load(p)
        self.assertEqual(len(d2["rows"]), len(self.d["rows"]))
        self.assertEqual(d2["rows"][0], {k: self.d["rows"][0][k] for k in nflrows.COLS})

    def test_grade(self):
        r = mk(0, rc=5)
        self.assertEqual(nflrows.grade(r, "rec", "FG", 4.5), "win")
        self.assertEqual(nflrows.grade(r, "rec", "FG", 4.5, "under"), "loss")
        self.assertEqual(nflrows.grade(r, "rec", "FG", 5), "push")
        self.assertEqual(nflrows.grade(None, "rec", "FG", 4.5), "void")


class ModelTest(unittest.TestCase):
    def test_distributions(self):
        self.assertAlmostEqual(M.break_even(-200), 2 / 3)
        self.assertAlmostEqual(M.break_even(120), 100 / 220)
        self.assertAlmostEqual(M.nb_sf(1, 1.0), 1 - math.exp(-1), places=12)
        self.assertAlmostEqual(sum(M.nb_sf(k, 3.0, 9.0) for k in range(1, 400)), 3.0, places=6)
        self.assertAlmostEqual(sum(M.binom_sf(k, 30.0, 60) for k in range(1, 61)), 30.0, places=6)
        # quantile-table survival is monotone and hits the ends
        q = [0.0] * 5 + [i / 36 * 3 for i in range(1, 37)]
        self.assertEqual(M._ratio_sf(q, -0.1), 1.0)
        self.assertEqual(M._ratio_sf(q, 3.5), 0.0)
        self.assertAlmostEqual(M._ratio_sf(q, 0.0), 1 - 4 / 40, places=9)   # point mass at zero (4 of 40 steps)
        xs = [M._ratio_sf(q, x / 10) for x in range(0, 31)]
        self.assertTrue(all(a >= b for a, b in zip(xs, xs[1:])))

    def test_monotone_in_line_and_markets_run(self):
        rows = [wr(i, 8, 6, 70) for i in range(10)]
        ctx = dict(pos="WR", date="2026-12-31")
        for per, mk_list in (("FG", ["rec", "rec_yds", "long_rec", "anytime_td"]), ("1H", ["rec", "rec_yds", "anytime_td"])):
            for m in mk_list:
                ps = [M.prob_over(rows, m, per, ln + 0.5, ctx)["p"] for ln in range(0, 120, 5)]
                self.assertTrue(all(a >= b - 1e-12 for a, b in zip(ps, ps[1:])), (m, per))
                self.assertTrue(all(0 <= p <= 1 for p in ps))
        qb = [mk(i, pa=34, pc=22, py=250, ptd=2, pi=1, ra=4, ry=20, lu=9, h_pa=17, h_py=125, h_ra=2, h_ry=10)
              for i in range(10)]
        for m in ("pass_yds", "pass_att", "pass_cmp", "pass_td", "pass_int", "rush_yds", "rush_att", "pass_rush_yds"):
            o = M.prob_over(qb, m, "FG", 1.5, dict(pos="QB"))
            self.assertGreater(o["mu"], 0, m)
        rb = [mk(i, ra=18, ry=80, rt=1, lu=20, tg=3, rc=2, rcy=15) for i in range(10)]
        for m in ("rush_yds", "rush_att", "rush_rec_yds", "long_rush", "anytime_td"):
            self.assertGreater(M.prob_over(rb, m, "FG", 0.5, dict(pos="RB"))["mu"], 0, m)

    def test_role_and_shrink(self):
        ctx = dict(pos="WR", date="2026-12-31")
        hot = [wr(i, 10, 8, 100) for i in range(10)]
        cold = [wr(i, 3, 2, 20) for i in range(10)]
        self.assertGreater(M.prob_over(hot, "rec", "FG", 4.5, ctx)["p"], 0.75)
        self.assertLess(M.prob_over(cold, "rec", "FG", 4.5, ctx)["p"], 0.25)
        # rising role: last 3 games 12 targets -> higher mean than steady 6
        steady = [wr(i, 6, 4, 50) for i in range(10)]
        up = steady[:7] + [wr(7 + i, 12, 8, 100) for i in range(3)]
        self.assertGreater(M.expect(up, "rec", "FG", ctx)["mu"], M.expect(steady, "rec", "FG", ctx)["mu"])
        # catch rate is shrunk to the position average: 2 perfect games don't project 100%
        two = [wr(i, 10, 10, 120) for i in range(2)]
        e = M.expect(two, "rec", "FG", ctx)
        self.assertLess(e["eff"], 0.9)

    def test_prior_games_only_and_order(self):
        rows = [wr(i, 6, 4, 50) for i in range(10)]
        ctx = dict(pos="WR", date=rows[6]["date"])
        a = M.prob_over(rows, "rec_yds", "FG", 45.5, ctx)
        b = M.prob_over(rows[:6], "rec_yds", "FG", 45.5, ctx)
        c = M.prob_over(list(reversed(rows[:6])), "rec_yds", "FG", 45.5, ctx)
        self.assertEqual(a["p"], b["p"])
        self.assertEqual(b["p"], c["p"])
        self.assertEqual(a["n"], 6)
        # seasons older than last season are ignored
        old = [wr(i, 20, 15, 200, season=2024) for i in range(10)]
        self.assertEqual(M.prob_over(rows + old, "rec", "FG", 3.5, dict(pos="WR"))["p"],
                         M.prob_over(rows, "rec", "FG", 3.5, dict(pos="WR"))["p"])

    def test_context(self):
        rows = [wr(i, 7, 5, 60) for i in range(10)]
        base = M.expect(rows, "rec_yds", "FG", dict(pos="WR"))["mu"]
        soft = M.expect(rows, "rec_yds", "FG", dict(pos="WR", opp={"rcy_WR": (1.2, 2)}))["mu"]
        high = M.expect(rows, "rec_yds", "FG", dict(pos="WR", team_total=29.0))["mu"]
        self.assertGreater(soft, base)
        self.assertGreater(high, base)
        self.assertAlmostEqual(M.implied_total(44.5, 3.5), 24.0)
        self.assertAlmostEqual(M.implied_total(44.5, -3.5), 20.5)
        self.assertIsNone(M.implied_total(None, 3))

    def test_flags_and_reason(self):
        rows = [wr(i, 7, 5, 60) for i in range(10)]
        ok = M.prob_over(rows, "rec", "FG", 4.5, dict(pos="WR", opp={"rc_WR": (1.15, 4)}, opp_name="DEN"))
        self.assertFalse(ok["thin"])
        self.assertEqual(ok["flags"], [])
        self.assertIn("5.0 catches a game his last 5", ok["why_parts"][0])
        self.assertIn("DEN allows the 4th most catches to WRs", ok["why_parts"])
        few = M.prob_over(rows[:3], "rec", "FG", 4.5, dict(pos="WR"))
        self.assertTrue(few["thin"])
        self.assertIn("few_games", few["flags"])
        self.assertFalse(M.pick_ok(few["flags"], "under"))
        seven = M.prob_over(rows[:7], "rec", "FG", 4.5, dict(pos="WR"))["flags"]
        self.assertEqual(seven, ["short_history"])
        self.assertTrue(M.pick_ok(seven, "under"))
        self.assertFalse(M.pick_ok(seven, "over"))
        jump = rows[:9] + [wr(9, 14, 9, 120)]
        jf = M.prob_over(jump, "rec", "FG", 4.5, dict(pos="WR"))["flags"]
        self.assertIn("role_jump", jf)
        self.assertTrue(M.pick_ok(jf, "over"))
        back = M.prob_over(rows, "rec", "FG", 4.5, dict(pos="WR", missed_last=2, qb_changed=True, games_with_team=2))
        for f in ("injury_return", "qb_change", "new_team"):
            self.assertIn(f, back["flags"])
        jumpy = [wr(i, (2, 12)[i % 2], 4, 50) for i in range(10)]
        self.assertIn("role_unstable", M.prob_over(jumpy, "rec", "FG", 4.5, dict(pos="WR"))["flags"])
        td = M.prob_over(rows, "anytime_td", "1H", 0.5, dict(pos="WR"))
        self.assertIn("in the 1st half", td["why_parts"][0])

    def test_opp_table(self):
        tr = []
        for i in range(8):
            for off, de, y in (("A", "B", 300), ("B", "A", 150), ("C", "D", 220), ("D", "C", 220)):
                t = dict(gid="g%d%s" % (i, off), team=off, opp=de, season=2026, stype="REG", date="2026-09-%02d" % (i + 1))
                for pre in ("", "h_"):
                    for k in nflrows.TSTATS:
                        t[pre + k] = 10
                    t[pre + "rcy_WR"] = y
                tr.append(t)
        o = M.opp_table(tr, "2026-12-01", 2026)
        self.assertGreater(o["B"]["rcy_WR"][0], 1.0)       # B allowed 300 a game
        self.assertLess(o["A"]["rcy_WR"][0], 1.0)
        self.assertEqual(o["B"]["rcy_WR"][1], 1)            # rank 1 = allows the most
        self.assertEqual(M.opp_table(tr, "2026-01-01", 2026), {})

    def test_calibration_shape(self):
        P = dict(M.PARAMS, calib={"x|FG": [0.0, 0.5, 1.0], "y|FG": [0.1, 1.0]})
        self.assertAlmostEqual(M.calibrate(0.5, "x", "FG", P), 0.5)
        hi = M.calibrate(0.8, "x", "FG", P)
        lo = M.calibrate(0.2, "x", "FG", P)
        self.assertLess(hi, 0.8)                            # overs shrunk
        self.assertAlmostEqual(lo, 0.2, places=9)           # unders untouched
        self.assertAlmostEqual(M.calibrate(0.5, "y", "FG", P), 1 / (1 + math.exp(-0.1)))
        self.assertAlmostEqual(M.calibrate(0.7, "nope", "FG"), 0.7, places=9)

    def test_params_loaded(self):
        for k in ("rec|FG", "rec_yds|FG", "pass_yds|1H", "anytime_td|1H"):
            self.assertIn(k, M.PARAMS["calib"])
        for k in ("rec_yds|FG", "pass_yds|1H", "rush_rec_yds|FG"):
            self.assertEqual(len(M.PARAMS["ratioq"][k]), 5)


if __name__ == "__main__":
    unittest.main()
