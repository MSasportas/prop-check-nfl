"""The timer's shell scripts (cycle.sh, loop.sh, dispatch_next.sh) against a throwaway git repo: a bare "origin"
plays GitHub, a clone plays the runner, a second clone plays someone else pushing. Everything is offline.
Ported from Prop Check NHL (proven): commit only data/ when it changed, heartbeat, Record guard before push,
a failed cycle publishes only a failure note, an edited Record on origin stops everything, the off switch."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import helpers
import test_record_grade

REPO = helpers.REPO
HAVE = shutil.which("git") and shutil.which("bash") and shutil.which("timeout")
DAY = "2026-10-13"


@unittest.skipUnless(HAVE, "needs git, bash and timeout")
class Cycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcn_cycle_")
        self.world = helpers.World()
        for k in helpers.ENV:
            os.environ.pop(k, None)
        self.origin = os.path.join(self.tmp, "origin.git")
        self.runner = os.path.join(self.tmp, "runner")
        self.other = os.path.join(self.tmp, "other")
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        self.git(self.tmp, "clone", "-q", self.origin, self.runner)
        for d in ("scripts", "config"):
            shutil.copytree(os.path.join(REPO, d), os.path.join(self.runner, d),
                            ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(os.path.join(REPO, ".gitignore"), self.runner)
        helpers.write(os.path.join(self.runner, "config", "rules.json"), helpers.legacy_role_cfg())  # see helpers
        os.makedirs(os.path.join(self.runner, "data", "record"))
        shutil.copytree(os.path.join(self.world.data, "state"), os.path.join(self.runner, "data", "state"))
        open(os.path.join(self.runner, "data", "record", "events.jsonl"), "w").close()
        with open(os.path.join(self.runner, "README.md"), "w") as f:
            f.write("test\n")
        self.git(self.runner, "checkout", "-q", "-b", "main")
        self.git(self.runner, "add", "-A")
        self.git(self.runner, "commit", "-q", "-m", "init")
        self.git(self.runner, "push", "-q", "origin", "main")
        self.git(self.tmp, "clone", "-q", "-b", "main", self.origin, self.other)
        self.env = {k: v for k, v in os.environ.items() if k not in helpers.ENV and not k.startswith("PC_")}
        self.env.update(PC_FEED_FIXTURE_DIR=self.world.feed, PC_ODDS_FIXTURE_DIR=self.world.odds,
                        PC_PYTHON=sys.executable, GITHUB_REF_NAME="main",
                        PC_RUNNER_EVENT="workflow_dispatch", PC_RUNNER_RUN_ID="4242", PC_RUNNER_LINK="7",
                        PC_RUNNER_CHAIN_STARTED="2026-10-13T08:00:00-04:00", PC_RUNNER_ITERATION="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

    def tearDown(self):
        self.world.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, cwd, *args):
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                            "-c", "init.defaultBranch=main"] + list(args), cwd=cwd, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s failed: %s" % (" ".join(args), r.stderr))
        return r.stdout

    def script(self, name, now, args=(), **extra):
        env = dict(self.env, PC_NOW="%sT%s:00-04:00" % (DAY, now), **extra)
        return subprocess.run(["bash", os.path.join(self.runner, "scripts", name)] + list(args),
                              env=env, capture_output=True, text=True, timeout=300)

    def cycle(self, now, **extra):
        return self.script("cycle.sh", now, **extra)

    def subjects(self):
        return self.git(self.origin, "log", "--format=%s", "main").splitlines()

    def origin_file(self, path):
        r = subprocess.run(["git", "show", "main:" + path], cwd=self.origin, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    def other_push(self, path, text, msg):
        self.git(self.other, "pull", "-q", "--rebase", "origin", "main")
        full = os.path.join(self.other, path)
        os.makedirs(os.path.dirname(full) or self.other, exist_ok=True)
        with open(full, "w") as f:
            f.write(text)
        self.git(self.other, "add", "-A")
        self.git(self.other, "commit", "-q", "-m", msg)
        self.git(self.other, "push", "-q", "origin", "main")

    def clean(self):
        return self.git(self.runner, "status", "--porcelain", "--untracked-files=all", "data") == ""

    def two_good_lines(self):
        ev = test_record_grade.shown("x", slate="2026-W05", at="2026-10-06T13:02:00-04:00",
                                     start="2026-10-08T20:15:00-04:00")
        gr = {"t": "graded", "id": "x", "at": "2026-10-09T01:00:00-04:00", "result": "W", "actual": 5,
              "actual_text": "had 5", "price": -120, "units": 0.8333}
        lines = os.path.join(self.tmp, "lines.jsonl")
        with open(lines, "w") as f:
            for e in (ev, gr):
                f.write(json.dumps(e, separators=(",", ":"), sort_keys=True) + "\n")
        r = self.cycle("09:00", CYCLE_RUN_CMD="%s scripts/run.py && cat %s >> data/record/events.jsonl"
                       % (sys.executable, lines))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RECORD HISTORY: ok", r.stdout)
        return self.origin_file("data/record/events.jsonl")

    # ---- the cases
    def test_changed_commits_unchanged_does_not(self):
        r = self.cycle("10:05")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.subjects()[0].startswith("run: "))
        st = json.loads(self.origin_file("data/status.json"))
        self.assertEqual(st["runner"]["chain_link"], 7)
        sl = json.loads(self.origin_file("data/slate.json"))
        self.assertEqual(len(sl["picks"]), 1)                       # the Thursday pick, from the real payload
        self.assertIsNotNone(self.origin_file("data/research/index.json"))
        self.assertIsNotNone(self.origin_file("data/odds/2026-10-13.json"))
        n = len(self.subjects())
        r = self.cycle("10:08", PC_RUNNER_ITERATION="2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing changed, nothing pushed", r.stdout)
        self.assertEqual(len(self.subjects()), n)
        self.assertTrue(self.clean())
        r = self.cycle("10:30", PC_RUNNER_ITERATION="3")            # heartbeat due
        self.assertEqual(len(self.subjects()), n + 1)

    def test_no_secret_in_anything_pushed(self):
        key = "zzzSECRETKEYzzz1234567890"
        r = self.cycle("10:05", ODDS_API_KEYS=key)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(key, r.stdout + r.stderr)
        out = subprocess.run(["git", "grep", "-I", "-c", key[:12], "main"], cwd=self.origin, capture_output=True, text=True)
        self.assertEqual(out.stdout, "")

    def test_edited_record_is_blocked_and_the_failure_is_published(self):
        good = self.two_good_lines()
        edit = "%s scripts/run.py && sed -i 's/had 5/had 4/' data/record/events.jsonl" % sys.executable
        r = self.cycle("09:10", CYCLE_RUN_CMD=edit)
        self.assertEqual(r.returncode, 1)
        self.assertIn("RECORD GUARD: FAILED", r.stdout)
        self.assertEqual(self.subjects()[0], "run failed: record guard")
        self.assertEqual(self.origin_file("data/record/events.jsonl"), good)
        st = json.loads(self.origin_file("data/status.json"))
        self.assertEqual((st["ok"], st["failed_step"]), (False, "record guard"))
        self.assertTrue(self.clean())
        r = self.cycle("09:20")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(self.origin_file("data/status.json"))["ok"])

    def test_record_edited_on_origin_stops_the_cycle_and_nothing_is_pushed(self):
        self.two_good_lines()
        cmd = ("cd %s && git pull -q --rebase origin main && sed -i 's/had 5/had 4/' data/record/events.jsonl && "
               "git commit -qam 'edited outside the timer' && git push -q origin main" % self.other)
        subprocess.run(["bash", "-c", cmd], env=self.env, check=True, capture_output=True)
        top = self.git(self.origin, "rev-parse", "main")
        r = self.cycle("09:10")
        self.assertEqual(r.returncode, 1)
        self.assertIn("RECORD HISTORY: FAILED", r.stdout)
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), top)

    def test_run_crash_and_timeout_publish_a_failure(self):
        self.cycle("10:05")
        r = self.cycle("10:10", CYCLE_RUN_CMD="echo half > data/slate.json; exit 3")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.subjects()[0], "run failed: run")
        sl = json.loads(self.origin_file("data/slate.json"))
        self.assertTrue(sl["banners"][0]["text"].startswith("The last update failed at 10:10 AM (run)."))
        r = self.cycle("10:20", CYCLE_RUN_CMD="sleep 30", CYCLE_RUN_TIMEOUT="2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("did not finish within 2 seconds", r.stdout)

    def test_someone_else_pushed_during_the_cycle(self):
        self.cycle("10:05")
        during = ("%s scripts/run.py && cd %s && git pull -q --rebase origin main && "
                  "printf 'during\\n' > README.md && git add -A && "
                  "git commit -q -m 'human edit during' && git push -q origin main" % (sys.executable, self.other))
        r = self.cycle("10:40", CYCLE_RUN_CMD=during)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.subjects()[1], "human edit during")
        self.assertTrue(self.subjects()[0].startswith("run: "))

    def test_loop_carries_on_stops_for_new_code_and_the_off_switch(self):
        counter = os.path.join(self.tmp, "n")
        cmd = ("n=$(cat %s 2>/dev/null || echo 0); n=$((n+1)); echo $n > %s; "
               "if [ $n -eq 2 ]; then exit 1; fi; %s scripts/run.py" % (counter, counter, sys.executable))
        r = self.script("loop.sh", "10:05", LOOP_MAX_ITER="3", LOOP_SLEEP="0", CYCLE_RUN_CMD=cmd)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("=== cycle 2 failed; the loop carries on", r.stdout)
        self.assertIn("=== stop: 3 cycles done", r.stdout)
        cmd = ("%s scripts/run.py && cd %s && git pull -q --rebase origin main && echo '# x' >> scripts/teams.py && "
               "git commit -qam 'code change' && git push -q origin main" % (sys.executable, self.other))
        r = self.script("loop.sh", "11:00", LOOP_MAX_ITER="5", LOOP_SLEEP="0", CYCLE_RUN_CMD=cmd)
        self.assertIn("=== stop: new code on main", r.stdout)
        self.other_push("config/stop_timer", "off\n", "stop the timer")
        r = self.script("loop.sh", "11:30", LOOP_MAX_ITER="5", LOOP_SLEEP="0")
        self.assertIn("config/stop_timer is set: no cycles", r.stdout)

    def test_dispatch_next(self):
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        calls = os.path.join(self.tmp, "gh_calls")
        with open(os.path.join(bin_dir, "gh"), "w") as f:
            f.write("#!/bin/sh\necho \"$@\" >> %s\nexit 0\n" % calls)
        os.chmod(os.path.join(bin_dir, "gh"), 0o755)
        env = dict(PATH=bin_dir + os.pathsep + self.env["PATH"], DISPATCH_BACKOFF="0", GH_TOKEN="ghs_SECRETTOKEN123")
        r = self.script("dispatch_next.sh", "10:05", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(calls) as f:
            self.assertEqual(f.read().strip(), "workflow run run.yml --ref main -f chain_link=8 "
                                               "-f chain_started_at=2026-10-13T08:00:00-04:00")


class Cadence(unittest.TestCase):
    def test_cadence(self):
        import cadence
        from datetime import datetime
        ET = cadence.ET
        k = [datetime(2026, 10, 15, 20, 15, tzinfo=ET), datetime(2026, 10, 18, 13, 0, tzinfo=ET)]
        at = lambda *a: datetime(*a, tzinfo=ET)
        self.assertEqual(cadence.step_for(at(2026, 10, 15, 11, 0), k), 600)       # game day
        self.assertEqual(cadence.step_for(at(2026, 10, 16, 0, 30), k), 600)       # 4 h after the TNF kickoff
        self.assertEqual(cadence.step_for(at(2026, 10, 14, 11, 0), k), 1800)      # off day, awake
        self.assertEqual(cadence.step_for(at(2026, 10, 14, 3, 0), k), 3600)       # overnight
        self.assertEqual(cadence.sleep_for(at(2026, 10, 15, 19, 3), k), 420)      # to the next 10 minute mark
        self.assertEqual(cadence.sleep_for(at(2026, 10, 14, 9, 55), k), 300)      # 30 min marks hit 10:00

    def test_workflow_is_safe(self):
        with open(os.path.join(REPO, ".github", "workflows", "run.yml")) as f:
            wf = f.read()
        self.assertIn("fetch-depth: 0", wf)
        self.assertNotIn("toJSON(secrets", wf.replace(" ", ""))
        self.assertIn("ODDS_API_KEYS: ${{ secrets.ODDS_API_KEYS }}", wf)


if __name__ == "__main__":
    unittest.main()
