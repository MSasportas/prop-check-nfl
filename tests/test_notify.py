"""notify.py: Discord messages. Local fake HTTP server only; never a real Discord URL, never the real data/."""
import contextlib
import http.server
import io
import json
import os
import shutil
import tempfile
import threading
import unittest

import helpers  # noqa: F401  (puts scripts/ on sys.path)
import common
import notify

SECRET = "https://discord.com/api/webhooks/123456789/SECRETTOKENabcdefghijklmnop"
DAY = "2026-11-01"


class Server:
    def __init__(self):
        self.posts, self.status = [], 200
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                outer.posts.append(json.loads(self.rfile.read(n)))
                self.send_response(outer.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/hook" % self.srv.server_port
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def pick(n, rank=None, kickoff="13:00", state="open", pulled=None, price=-115, ev=3.6, price_final=False,
         market="rec_yds", method="gap_guard"):
    return {"id": "2026-W08:g1:p%d:%s:44.5:under" % (n, market), "state": state, "rank": rank if rank is not None else n,
            "day": DAY, "kickoff": "%sT%s:00-05:00" % (DAY, kickoff), "game": "SEA @ DEN", "player": "P. Player%d" % n,
            "market": market, "side": "under", "line": 44.5, "bet": "Under 44.5 receiving yards", "price": price,
            "vs": "+4.9% vs 8 books (Pinnacle in)", "gap_pct": 4.9, "final_ev_pct": ev, "method": method, "pin_solo": None,
            "settled": "injury_report", "settled_text": "Injury report in", "pulled": pulled, "graded": None,
            "flags": {"price_final": price_final}}


def slate(picks=(), banners=(), odds_state="ok", valid_until="2026-11-03T06:00:00-05:00"):
    return {"schema": 2, "valid_until": valid_until, "status": {"odds": {"state": odds_state}}, "banners": list(banners),
            "picks": list(picks)}


class T(unittest.TestCase):
    def setUp(self):
        self.srv = Server()
        self.dir = tempfile.mkdtemp(prefix="pcn_notify_")
        self.saved = {k: os.environ.get(k) for k in ("PC_NOW", "PC_DATA_DIR", "DISCORD_WEBHOOK_URL", "PC_DISCORD_FIXTURE_URL")}
        os.environ.update(PC_DATA_DIR=self.dir, DISCORD_WEBHOOK_URL=SECRET, PC_DISCORD_FIXTURE_URL=self.srv.url)
        self.at("10:00")
        notify.notify(slate())                       # the one-time hello
        self.srv.posts.clear()

    def tearDown(self):
        self.srv.close()
        shutil.rmtree(self.dir, ignore_errors=True)
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def at(self, hhmm, day=DAY):
        os.environ["PC_NOW"] = "%sT%s:00-05:00" % (day, hhmm)

    def texts(self):
        return [p["content"] for p in self.srv.posts]

    def state(self):
        with open(os.path.join(self.dir, "state", "notified.json")) as f:
            return json.load(f)

    # ---- hello, silence, switches
    def test_hello_once(self):
        d = tempfile.mkdtemp(prefix="pcn_hello_")
        try:
            os.environ["PC_DATA_DIR"] = d
            self.assertEqual(notify.notify(slate()), 1)
            self.assertIn("Prop Check NFL notifications are on", self.texts()[0])
            self.assertEqual(notify.notify(slate()), 0)
        finally:
            os.environ["PC_DATA_DIR"] = self.dir
            shutil.rmtree(d, ignore_errors=True)

    def test_no_webhook_does_nothing(self):
        os.environ["DISCORD_WEBHOOK_URL"] = ""
        self.assertEqual(notify.notify(slate([pick(1)])), 0)
        self.assertEqual(self.srv.posts, [])

    def test_test_mode_never_reaches_the_real_webhook(self):
        os.environ.pop("PC_DISCORD_FIXTURE_URL")
        self.assertIsNone(notify.endpoint())

    def test_quiet_hours_hold_picks_until_8am(self):
        self.at("03:00")
        self.assertEqual(notify.notify(slate([pick(1)])), 0)
        self.at("08:00")
        self.assertEqual(notify.notify(slate([pick(1)])), 1)

    def test_old_slate_is_silent(self):
        self.assertEqual(notify.notify(slate([pick(1)], valid_until="2026-10-31T06:00:00-04:00")), 0)

    # ---- new picks
    def test_new_pick_message_and_numbers(self):
        self.assertEqual(notify.notify(slate([pick(1)])), 1)
        t = self.texts()[0]
        self.assertIn("🏈 **NEW PICK · Sun Nov 1**", t)
        self.assertIn("**P. Player1 UNDER 44.5 rec yds  -115**", t)
        self.assertIn("SEA @ DEN · 1:00 PM ET · 📋 Injury report in", t)
        self.assertIn("+4.9% vs 8 books (Pinnacle in), we say 55.4%, price needs 53.5%", t)
        self.assertIn(notify.CHECK, t)
        self.assertTrue(t.rstrip().endswith("https://msasportas.github.io/prop-check-nfl/"))

    def test_we_say_is_the_final_ev_at_the_frozen_price(self):
        p = pick(1, price=+110, ev=5.0)
        self.assertEqual(notify.breakeven_pct(110), 47.6)
        self.assertEqual(notify.we_say_pct(p), 50.0)             # 1.05 / 2.10
        p["final_ev_pct"] = None
        self.assertIsNone(notify.we_say_pct(p))

    def test_each_pick_once_even_if_the_price_moves(self):
        notify.notify(slate([pick(1)]))
        self.srv.posts.clear()
        self.assertEqual(notify.notify(slate([pick(1, price=-130)])), 0)
        self.assertEqual(notify.notify(slate([pick(1), pick(2)])), 1)
        self.assertIn("P. Player2", self.texts()[0])
        self.assertNotIn("P. Player1", self.texts()[0])

    def test_told_picks_survive_to_monday_night(self):
        self.at("20:00", day="2026-10-30")                        # told Friday night
        notify.notify(slate([pick(1)]))
        self.srv.posts.clear()
        self.at("18:00", day="2026-11-02")                        # still open Monday: not told again
        self.assertEqual(notify.notify(slate([pick(1, kickoff="20:15")])), 0)

    def test_eight_blocks_then_more(self):
        notify.notify(slate([pick(i) for i in range(1, 12)]))
        t = self.texts()[0]
        self.assertIn("NEW PICKS (11)", t)
        self.assertLessEqual(len(t), 1990)
        self.assertIn("more on the page", t)
        self.assertTrue(t.rstrip().endswith("prop-check-nfl/"))

    def test_started_locked_pulled_graded_are_not_new(self):
        self.at("13:30")
        self.assertEqual(notify.notify(slate([pick(1)])), 0)                 # kicked off
        self.at("10:00")
        self.assertEqual(notify.notify(slate([pick(2, state="locked"), pick(3, state="pulled", pulled={"reason": "x"})])), 0)

    def test_pin_solo_pick_says_what_moved(self):
        p = pick(1, method="pin_solo", ev=None)
        p["pin_solo"] = {"pin_move_pts": 3.1, "others_move_pts": 0.4, "ev_vs_fair_pct": 2.2}
        notify.notify(slate([p]))
        self.assertIn("Pinnacle moved +3.1 pts, other books +0.4", self.texts()[0])

    # ---- pulled and final
    def test_pulled_only_for_told_picks_before_kickoff(self):
        notify.notify(slate([pick(1)]))
        self.srv.posts.clear()
        pulled = {"reason": "Ruled out", "if_bet": "If you already bet it at -115, check the slip."}
        self.at("11:00")
        self.assertEqual(notify.notify(slate([pick(1, state="pulled", pulled=pulled),
                                              pick(2, state="pulled", pulled=pulled)])), 0 + 1)
        t = self.texts()[0]
        self.assertIn("❌ **PULLED. Don't bet these now:**", t)
        self.assertIn("P. Player1", t)
        self.assertNotIn("P. Player2", t)                                    # never told about, so no pull message
        self.assertIn("If you already bet it", t)
        self.srv.posts.clear()
        self.assertEqual(notify.notify(slate([pick(1, state="pulled", pulled=pulled)])), 0)

    def test_price_final_once_and_not_for_picks_that_said_it(self):
        notify.notify(slate([pick(1)]))
        self.srv.posts.clear()
        self.at("11:50")
        self.assertEqual(notify.notify(slate([pick(1, price_final=True)])), 1)
        self.assertIn("✅ **PRICE FINAL.**", self.texts()[0])
        self.assertEqual(notify.notify(slate([pick(1, price_final=True)])), 0)
        self.srv.posts.clear()
        self.assertEqual(notify.notify(slate([pick(1, price_final=True), pick(2, price_final=True)])), 1)
        self.assertIn("✅ Price final", self.texts()[0])
        self.assertNotIn("PRICE FINAL.", self.texts()[0])

    # ---- keys
    def test_keys_refused_and_pool_low_once_a_day(self):
        banner = {"level": "warn", "text": "Odds credits are low: 90 left for 13 games this week. Some games get fewer price checks until more keys are added."}
        self.assertEqual(notify.notify(slate(banners=[banner])), 1)
        self.assertIn("⚠️ **Needs you:** Odds credits are low", self.texts()[0])
        self.assertEqual(notify.notify(slate(banners=[banner])), 0)
        self.srv.posts.clear()
        self.assertEqual(notify.notify(slate(odds_state="keys_refused")), 1)
        self.assertIn("odds keys were refused", self.texts()[0])

    # ---- delivery
    def test_failed_post_is_retried_next_cycle(self):
        self.srv.status = 500
        self.assertEqual(notify.notify(slate([pick(1)])), 0)
        self.srv.status = 200
        self.srv.posts.clear()
        self.assertEqual(notify.notify(slate([pick(1)])), 1)

    def test_429_stops_the_run_sending(self):
        self.srv.status = 429
        self.assertEqual(notify.notify(slate([pick(1)], banners=[
            {"level": "stop", "text": "Out of odds keys: no new Hard Rock prices until more keys are added. No new picks."}])), 0)
        self.assertEqual(len(self.srv.posts), 1)

    def test_twelve_a_day(self):
        for i in range(1, 16):
            notify.notify(slate([pick(i)]))
        self.assertEqual(len(self.srv.posts), notify.MAX_PER_DAY - 1)        # the hello in setUp was the first of the 12
        self.assertEqual(self.state()["sent"][DAY], notify.MAX_PER_DAY)

    # ---- the secret
    def test_secret_never_in_state_or_output(self):
        buf = io.StringIO()
        common.install_scrubbers()
        with contextlib.redirect_stdout(buf):
            notify.notify(slate([pick(1)]))
            print(common.scrub("posting to " + SECRET + " token SECRETTOKENabcdefghijklmnop"))
        with open(os.path.join(self.dir, "state", "notified.json")) as f:
            raw = f.read()
        for blob in (raw, buf.getvalue()):
            self.assertNotIn("SECRETTOKEN", blob)
            self.assertNotIn("discord.com", blob)

    def test_never_raises(self):
        self.assertEqual(notify.notify({"picks": [{"id": "x", "state": "open", "rank": 1}], "valid_until": "2026-11-03T06:00:00-05:00"}), 0)


if __name__ == "__main__":
    unittest.main()
