"""The Record and grading: units, parlays, append-only log, the guard, record.json per the contract, and grading
from nflverse rows and from the ESPN box score (real TB at DAL summary)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

import helpers
import common
import grade
import guard_record
import nflrows
import record
import run
import stats

CFG = helpers.cfg()
W06 = "2026-W06"


def shown(i, market="rec", period="FG", line=4.5, side="over", price=-120, game="2026_06_SEA_DEN", pid="00-0038543",
          slate=W06, at="2026-10-13T10:05:00-04:00", start="2026-10-15T20:15:00-04:00", team="SEA", opp="DEN",
          home=False):
    return {"t": "shown", "kind": "single", "id": i, "at": at, "slate": slate,
            "pick": {"id": i, "rank": 1, "game_id": game, "player": "Jaxon Smith-Njigba", "player_short": "J. Smith-Njigba",
                     "player_id": pid, "pos": "WR", "team": team, "opp": opp, "home": home, "start": start,
                     "mkey": market if period == "FG" else market + "_1h", "market": market, "period": period,
                     "line": line, "side": side, "bet": "Over %g receptions" % line, "book": "Hard Rock",
                     "price": price, "price_at": at, "p": 0.6, "breakeven": 0.55, "p_pct": 60, "breakeven_pct": 55,
                     "reason": "r", "state": "early", "thin": False}}


def graded(i, result, price, units, text="had 5", at="2026-10-16T00:30:00-04:00"):
    return {"t": "graded", "id": i, "at": at, "result": result, "actual": 5, "actual_text": text, "price": price,
            "units": units}


class Units(unittest.TestCase):
    def test_single(self):
        self.assertAlmostEqual(record.single_units("W", -145), 0.6897, places=4)
        self.assertAlmostEqual(record.single_units("W", 120), 1.2)
        self.assertEqual(record.single_units("L", -145), -1.0)
        self.assertEqual(record.single_units("PUSH", -145), 0.0)
        self.assertEqual(record.single_units("VOID", -145), 0.0)

    def test_parlay(self):
        self.assertEqual(record.grade_parlay([("W", -120), ("W", 100)]), ("W", 267, 2.67))
        self.assertEqual(record.grade_parlay([("W", -120), ("L", 100)]), ("L", 267, -1.0))
        self.assertEqual(record.grade_parlay([("W", -120), ("VOID", 100)]), ("W", -120, 0.8333))
        self.assertEqual(record.grade_parlay([("VOID", -120), ("PUSH", 100)]), ("VOID", None, 0.0))
        self.assertEqual(record.grade_parlay([("W", -120), (None, 100)]), (None, None, None))

    def test_result_for(self):
        self.assertEqual(grade.result_for("over", 4.5, 5), "W")
        self.assertEqual(grade.result_for("over", 4.5, 4), "L")
        self.assertEqual(grade.result_for("under", 4.5, 4), "W")
        self.assertEqual(grade.result_for("over", 5.0, 5), "PUSH")

    def test_worst_price_is_the_lowest_payout(self):
        it = record.fold([shown("a", price=-120), {"t": "price", "id": "a", "at": "t2", "price": -140},
                          {"t": "price", "id": "a", "at": "t3", "price": -110}])["a"]
        self.assertEqual(record.worst_price(it), -140)
        self.assertEqual(record.grade_price(it), -140)


class RecordLog(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("PC_DATA_DIR")
        self.dir = tempfile.mkdtemp(prefix="pcn_rec_")
        os.environ["PC_DATA_DIR"] = self.dir

    def tearDown(self):
        if self.old is None:
            os.environ.pop("PC_DATA_DIR", None)
        else:
            os.environ["PC_DATA_DIR"] = self.old

    def test_append_only_adds_at_the_end(self):
        record.append([shown("a")])
        first = record.read_raw()
        record.append([{"t": "locked", "id": "a", "at": "2026-10-15T20:15:00-04:00", "state": "early"}])
        second = record.read_raw()
        self.assertTrue(second.startswith(first))
        self.assertEqual(second.count(b"\n"), 2)
        self.assertEqual(record.append([]), 0)

    def test_damaged_log_stops_everything(self):
        record.append([shown("a")])
        raw = record.read_raw()
        common.write_bytes(record.events_path(), raw[:-1])
        with self.assertRaises(record.RecordError):
            record.append([shown("b")])
        common.write_bytes(record.events_path(), raw + b"{not json\n")
        with self.assertRaises(record.RecordError):
            record.read_events()
        common.write_bytes(record.events_path(), raw)
        with self.assertRaises(record.RecordError):
            record.append([{"t": "edit", "id": "a", "at": "x"}])
        self.assertEqual(record.read_raw(), raw)

    def test_fold_settledness_only_through_state_events(self):
        ev = [shown("a", price=-135), {"t": "state", "id": "a", "at": "t3", "state": "injury_report"},
              {"t": "locked", "id": "a", "at": "t4", "state": "injury_report"},
              {"t": "pulled", "id": "a", "at": "t6", "reason": "late"}]          # after lock: ignored
        it = record.fold(ev)["a"]
        self.assertEqual((it["state"], it["locked_at"], it["pulled"]), ("injury_report", "t4", None))

    def test_contract_views_and_rebuild(self):
        ev = [shown("a", price=-120), shown("b", price=-150, pid="p2"),
              shown("c", market="rush_yds", period="1H", line=22.5, price=110, pid="p3"),
              shown("d", price=-110, pid="p4"), shown("e", price=-110, pid="p5"),          # e: shown, never on Record
              {"t": "pulled", "id": "d", "at": "2026-10-14T11:00:00-04:00", "reason": "Listed questionable.",
               "code": "injury"},
              {"t": "locked", "id": "a", "at": "2026-10-15T20:15:00-04:00", "state": "inactives"},
              {"t": "locked", "id": "b", "at": "2026-10-15T20:15:00-04:00", "state": "inactives"},
              {"t": "locked", "id": "c", "at": "2026-10-15T20:15:00-04:00", "state": "inactives"},
              graded("a", "W", -120, 0.8333), graded("b", "L", -150, -1.0, "had 1"),
              graded("c", "VOID", 110, 0.0, "did not play"), graded("d", "L", -110, -1.0, "had 0")]
        rec, older = record.build_views(ev, CFG)
        h = rec["summary"]["headline"]
        self.assertEqual((h["w"], h["l"], h["push"], h["void"], h["n"]), (1, 1, 0, 1, 2))
        self.assertAlmostEqual(h["units"], -0.1667, places=4)
        self.assertEqual(h["parlays"], {"w": 0, "l": 0, "units": 0})
        rows = {r["key"]: r for r in rec["summary"]["rows"]}
        self.assertEqual(sorted(rows), ["pulled", "rec|FG", "rush_yds|1H"])
        self.assertEqual(rows["rec|FG"]["label"], "Receptions")
        self.assertEqual(rows["rush_yds|1H"]["label"], "Rushing yards (1st half)")
        self.assertEqual((rows["pulled"]["l"], rows["pulled"]["label"]), (1, "Pulled"))
        wk = rec["weeks"][0]
        self.assertEqual((wk["week"], wk["label"], wk["graded"], wk["w"], wk["l"]), (6, "Week 6", True, 1, 1))
        ids = [e["id"] for e in wk["entries"]]
        self.assertNotIn("e", ids)                       # joins the Record at kickoff or when pulled
        d = next(e for e in wk["entries"] if e["id"] == "d")
        self.assertEqual(d["pulled"], {"at": "2026-10-14T11:00:00-04:00", "reason": "Listed questionable."})
        a = next(e for e in wk["entries"] if e["id"] == "a")
        self.assertEqual((a["date"], a["game"], a["player"], a["market"], a["period"], a["price"]),
                         ("2026-10-15", "SEA at DEN", "J. Smith-Njigba", "rec", "FG", -120))
        self.assertEqual((rec["season"], rec["older"], older), (2026, [], {}))
        record.append(ev)
        record.write_views(CFG)
        before = open(common.dpath("record.json"), "rb").read()
        os.unlink(common.dpath("record.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            run.main(["--rebuild-record"])
        self.assertEqual(open(common.dpath("record.json"), "rb").read(), before)
        self.assertEqual(record.write_views(CFG), [])

    def test_empty_record_at_cutover(self):
        rec, older = record.build_views([], CFG)
        h = rec["summary"]["headline"]
        self.assertEqual((h["w"], h["l"], h["n"], h["hit_rate"], h["needed"]), (0, 0, 0, None, None))
        self.assertEqual((rec["summary"]["rows"], rec["weeks"], rec["older"]), ([], [], []))

    def test_older_weeks_go_to_their_own_files(self):
        ev = []
        for wk in range(1, 7):
            sl = "2026-W%02d" % wk
            ev += [shown("p%d" % wk, slate=sl), {"t": "locked", "id": "p%d" % wk, "at": "2026-10-15T20:15:00-04:00",
                                                 "state": "early"}]
        rec, older = record.build_views(ev, CFG)
        self.assertEqual([w["week"] for w in rec["weeks"]], [6, 5, 4, 3])
        self.assertEqual(rec["older"], ["record/2026-W02.json", "record/2026-W01.json"])
        self.assertEqual(older["2026-W01"]["weeks"][0]["week"], 1)


class Guard(unittest.TestCase):
    A = b'{"t":"shown","id":"a","at":"1"}\n'
    B = b'{"t":"locked","id":"a","at":"2"}\n'

    def test_check(self):
        c = guard_record.check
        self.assertIsNone(c(b"", self.A))
        self.assertIsNone(c(self.A, self.A + self.B))
        self.assertEqual(c(self.A + self.B, self.A), "the Record lost lines (2 committed, 1 now)")
        self.assertEqual(c(self.A + self.B, self.B + self.A), "a committed Record line was changed")
        self.assertEqual(c(self.A, self.A + self.B[:-1]), "the Record does not end with a newline")

    def test_against_a_real_git_repo(self):
        d = tempfile.mkdtemp(prefix="pcn_guard_git_")
        os.makedirs(os.path.join(d, "scripts"))
        os.makedirs(os.path.join(d, "data", "record"))
        with open(os.path.join(helpers.SCRIPTS, "guard_record.py")) as f:
            src = f.read()
        with open(os.path.join(d, "scripts", "guard_record.py"), "w") as f:
            f.write(src)
        log = os.path.join(d, "data", "record", "events.jsonl")
        git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c",
                                         "commit.gpgsign=false"] + list(a), cwd=d, capture_output=True, text=True)
        guard = lambda *a: subprocess.run([sys.executable, os.path.join(d, "scripts", "guard_record.py")] + list(a),
                                          capture_output=True, text=True)
        if git("init", "-q").returncode != 0:
            self.skipTest("git not available")
        self.assertEqual(guard().returncode, 0)
        common.write_bytes(log, self.A)
        git("add", "-A"); git("commit", "-q", "-m", "one")
        common.write_bytes(log, self.A + self.B)
        self.assertEqual(guard().returncode, 0)
        git("add", "-A"); git("commit", "-q", "-m", "two")
        self.assertEqual(guard("--history").returncode, 0)
        common.write_bytes(log, self.B)
        self.assertEqual(guard().returncode, 1)
        git("add", "-A"); git("commit", "-q", "-m", "rewritten")
        r = guard("--history")
        self.assertEqual(r.returncode, 1)
        self.assertIn("RECORD HISTORY: FAILED", r.stdout)


class GradeFromRows(unittest.TestCase):
    """nflverse rows: the committed 2026 rows hold week 5 (SF at SEA, Oct 11)."""

    def setUp(self):
        self.w = helpers.World()
        self.store, _ = stats.load(2026)

    def tearDown(self):
        self.w.close()

    def cache(self, gid, start):
        return {"games": {gid: {"id": gid, "start": start, "away": "SF", "home": "SEA", "espn": None, "date": start[:10],
                                "away_score": 29, "home_score": 32}}, "live": {}}

    def test_win_loss_push_void_and_1h(self):
        gid, start = "2026_05_SF_SEA", "2026-10-11T16:25:00-04:00"
        row = self.store.by_game[gid][helpers.JSN]
        rec_fg, rec_1h = nflrows.actual(row, "rec", "FG"), nflrows.actual(row, "rec", "1H")
        ev = [shown("w", line=rec_fg - 0.5, game=gid, start=start, slate="2026-W05"),
              shown("l", line=rec_fg + 0.5, game=gid, start=start, slate="2026-W05"),
              shown("p", line=float(rec_fg), game=gid, start=start, slate="2026-W05"),
              shown("h", line=rec_1h + 0.5, side="under", period="1H", game=gid, start=start, slate="2026-W05"),
              shown("v", pid="00-0000000", game=gid, start=start, slate="2026-W05")]
        now = common.parse_iso("2026-10-12T09:00:00-04:00")
        new, notes = grade.grade(ev, now, CFG, self.cache(gid, start), self.store, {}, {})
        res = {e["id"]: (e["result"], e.get("source")) for e in new if e["t"] == "graded"}
        self.assertEqual(res["w"], ("W", "nflverse"))
        self.assertEqual(res["l"][0], "L")
        self.assertEqual(res["p"][0], "PUSH")
        self.assertEqual(res["h"][0], "W")
        self.assertEqual(res["v"], ("VOID", "nflverse"))          # snap counts are in for the game: no row = no snap
        locks = [e for e in new if e["t"] == "locked"]
        self.assertEqual(len(locks), 5)                           # never locked by the engine: locked first
        new2, _ = grade.grade(ev + new, now, CFG, self.cache(gid, start), self.store, {}, {})
        self.assertEqual(new2, [])                                # graded once

    def test_postponed_is_void_and_not_final_waits(self):
        gid, start = "2026_06_SEA_DEN", "2026-10-15T20:15:00-04:00"
        cache = {"games": {gid: {"id": gid, "start": start, "away": "SEA", "home": "DEN", "espn": "401872995",
                                 "date": "2026-10-15", "away_score": None, "home_score": None}}, "live": {}}
        ev = [shown("a")]
        now = common.parse_iso("2026-10-15T23:00:00-04:00")
        self.assertEqual(grade.grade(ev, now, CFG, cache, self.store, {}, {})[0], [])
        cache["live"][gid] = {"postponed": True}
        new, _ = grade.grade(ev, now, CFG, cache, self.store, {}, {})
        self.assertEqual([(e["t"], e.get("result"), e.get("actual_text")) for e in new],
                         [("locked", None, None), ("graded", "VOID", "game postponed")])


class GradeFromEspnBox(unittest.TestCase):
    """The real ESPN summary of TB at DAL (Oct 8 2026), before nflverse posts the plays."""

    def setUp(self):
        self.w = helpers.World(with_rows=False)
        self.summary = helpers.load(os.path.join(helpers.WORLD, "espn_summary_tb_dal.json"))
        self.w.set_summary("401872980", self.summary)
        self.roster = {"00-0038120": {"espn_id": "4426354"},     # George Pickens: 9 rec, 130 yds, 1 TD, long 27
                       "00-0033077": {"espn_id": "2577417"}}     # Dak Prescott: 24/42, 316, 1 TD, 2 INT

    def tearDown(self):
        self.w.close()

    def test_full_game_from_the_box_1h_waits(self):
        gid, start = "2026_05_TB_DAL", "2026-10-08T20:15:00-04:00"
        cache = {"games": {gid: {"id": gid, "start": start, "away": "TB", "home": "DAL", "espn": "401872980",
                                 "date": "2026-10-08", "away_score": None, "home_score": None}},
                 "live": {gid: {"final": True, "state": "post"}}}
        mk = lambda i, pid, market, line, side="over", period="FG": shown(i, market=market, line=line, side=side,
                                                                          period=period, pid=pid, game=gid, start=start,
                                                                          slate="2026-W05", team="DAL", opp="TB", home=True)
        ev = [mk("rec", "00-0038120", "rec", 7.5), mk("yds", "00-0038120", "rec_yds", 130.0),
              mk("long", "00-0038120", "long_rec", 26.5), mk("td", "00-0038120", "anytime_td", 0.5),
              mk("py", "00-0033077", "pass_yds", 299.5, "under"), mk("int", "00-0033077", "pass_int", 0.5, "under"),
              mk("h1", "00-0038120", "rec_yds", 40.5, period="1H"),
              mk("dnp", "00-0099999", "rec", 2.5)]
        now = common.parse_iso("2026-10-08T23:59:00-04:00")
        new, notes = grade.grade(ev, now, CFG, cache, stats.Store(), {}, self.roster)
        res = {e["id"]: (e["result"], e["actual"], e["source"]) for e in new if e["t"] == "graded"}
        self.assertEqual(res["rec"], ("W", 9, "espn_box"))
        self.assertEqual(res["yds"], ("PUSH", 130, "espn_box"))
        self.assertEqual(res["long"], ("W", 27, "espn_box"))
        self.assertEqual(res["td"], ("W", 1, "espn_box"))
        self.assertEqual(res["py"][0:2], ("L", 316))
        self.assertEqual(res["int"][0:2], ("L", 2))
        self.assertNotIn("h1", res)                 # 1st half is not in the box: waits for nflverse
        self.assertNotIn("dnp", res)                # no box line and no inactive proof: waits

    def test_box_matches_nflverse_rows(self):
        """read_box on the real summary equals nflrows for every stat it shows (checked on Oct 13 rows)."""
        import roster as R
        ppl = R.parse(open(os.path.join(helpers.WORLD, "roster_weekly_2026.csv")).read(), 2026)
        box = grade.read_box(self.summary, {"4426354": "00-0038120"})
        self.assertEqual({k: box["00-0038120"][k] for k in ("rc", "rcy", "rct", "lr", "tg")},
                         {"rc": 9, "rcy": 130, "rct": 1, "lr": 27, "tg": 13})


if __name__ == "__main__":
    unittest.main()
