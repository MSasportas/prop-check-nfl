#!/usr/bin/env python3
"""run.py: the one entry point. The timer runs `python scripts/run.py` and this file decides what is due.

    python scripts/run.py                    do whatever is due right now
    python scripts/run.py --only grade       run some steps (comma list; see STEPS)
    python scripts/run.py --rebuild-record   rebuild record.json and the older-week files from the log, nothing else
    python scripts/run.py --dry-run          run against a throwaway copy of data/, spend no credits, print the result
    python scripts/run.py --explain          every Hard Rock line the engine priced, its numbers and the first rule it
                                             failed (reads files only; writes nothing)
    python scripts/run.py --rebuild-stats    rebuild this season's rows from nflverse now

Steps, in order, each allowed to fail without stopping the others:
    rules      data/rules.json from config/rules.json
    schedule   games.csv (season) + ESPN scoreboard (live state, finals)
    stats      this season's rows from nflverse play-by-play, only when the release changed
    roster     nflverse weekly rosters (names -> gsis ids)
    injuries   ESPN injuries (nflverse fallback) + inactives for games inside their window
    weather    open-meteo at kickoff for outdoor games
    odds       Hard Rock + other books' prices (pulls R and C, and the optional watch-only W), if one is due and the budget allows
    engine     lock / pull / keep shown picks, add new Gap + Guard picks  -> appends to the Record (and data/gg/)
    grade      results for final games                                    -> appends to the Record
    watch      behind-the-scenes research tracks (Pinnacle moves alone / price judge)  -> data/watch/ only; soft
    slate      data/slate.json
    record     data/record.json and older-week files
    status     data/status.json
Safe to run twice in a row: every decision is made from files on disk, and every write is temp + rename.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common      # noqa: E402
import emit        # noqa: E402
import engine      # noqa: E402
import gg          # noqa: E402
import grade       # noqa: E402
import watch       # noqa: E402
import injuries    # noqa: E402
import names       # noqa: E402
import odds        # noqa: E402
import record      # noqa: E402
import roster      # noqa: E402
import sched       # noqa: E402
import stats       # noqa: E402
import weather     # noqa: E402

STEPS = ["rules", "schedule", "stats", "roster", "injuries", "weather", "odds", "engine", "grade", "watch", "slate",
         "record", "status"]


PAGE_URL = "https://msasportas.github.io/prop-check-nfl/"


def live_page_check(prev, now):
    """Once an hour from the runner: does the public page answer and is it this page? Never in tests."""
    if os.environ.get("PC_NOW") or os.environ.get("PC_DATA_DIR"):
        return prev
    last = common.parse_iso((prev or {}).get("checked_at"))
    if last is not None and timedelta(0) <= now - last < timedelta(hours=1):
        return prev
    r = common.http_get(PAGE_URL + "?t=%d" % int(now.timestamp()), timeout=15, retries=1)
    body = r.body or b""
    return {"checked_at": common.iso(now), "http": r.status, "is_page": b"Prop Check NFL" in body}


class Run:
    def __init__(self, cfg, now, only=None, dry_run=False, readonly=False):
        self.cfg, self.now, self.only, self.dry_run, self.readonly = cfg, now, only, dry_run, readonly
        self.steps = []
        self.cache = None
        self.games = []
        self.started = {}
        self.season = None
        self.slate = None
        self.store, self.stats_problems = None, []
        self.behind, self.stats_through = {}, None
        self.roster = None
        self.inj, self.ina = None, None
        self.odds_res = None
        self.events = None
        self.extra = None
        self.inp = None
        self.view = None
        self.new_events = 0
        self.wrote_slate = False
        self.paused = {}
        self.slate_doc = None
        self.step_res = {}

    # ---- helpers
    def wants(self, step):
        return self.only is None or step in self.only

    def step(self, name, fn):
        if not self.wants(name):
            return
        try:
            out = fn() or {}
            self.steps.append({"step": name, "status": out.get("status", "ok"), "note": out.get("note", "")})
        except Exception as e:                              # one bad step never takes the run down
            self.steps.append({"step": name, "status": "failed", "note": common.short_err(e)})
            common.log("step %s failed: %s" % (name, common.short_err(e)))
            if os.environ.get("PC_RAISE"):
                raise
        self.step_res[name] = self.steps[-1]

    def failed_step(self):
        return next((s["step"] for s in self.steps if s["status"] == "failed"), None)

    # ---- loading what is on disk
    def load_schedule(self):
        if self.cache is None:
            self.cache = common.read_json(sched.path())
        if self.cache:
            self.season = self.cache.get("season")
            self.games = sched.slate_games(self.cache, self.now, self.cfg)
            self.slate = sched.slate_id(self.cache, self.now, self.cfg)
            self.started = {g["id"]: sched.started(g, self.now, self.cache) for g in self.games}

    def load_stats(self):
        if self.store is not None or not self.season:
            return
        self.store, self.stats_problems = stats.load(self.season)
        self.behind, self.stats_through = stats.team_status(self.store, self.cache, self.now)

    def load_roster(self):
        if self.roster is None:
            self.roster = roster.load()
        return self.roster

    def people(self):
        """gsis -> person, from the weekly roster plus anyone with rows this season (signed this week)."""
        ppl = {pid: dict(p) for pid, p in self.load_roster().items()}
        self.load_stats()
        if self.store:
            for pid, rows in self.store.by_pid.items():
                if pid in ppl or not rows or rows[-1]["season"] != self.season:
                    continue
                nm, pos = (self.store.players.get(pid) or ["", ""])[:2]
                first, _, last = nm.partition(" ")
                if last and pos in ("QB", "RB", "WR", "TE", "FB"):
                    ppl[pid] = {"first": first, "last": last, "name": nm, "team": rows[-1]["team"],
                                "pos": "RB" if pos == "FB" else pos, "espn_id": None, "from_rows": True}
        return ppl

    def matcher_for(self, game):
        ppl = []
        for pid, p in self.people().items():
            if p.get("team") not in (game["away"], game["home"]):
                continue
            ppl.append({"id": pid, "first": p.get("first", ""), "last": p.get("last", ""), "team": p["team"],
                        "pos": p.get("pos")})
            if p.get("football") and p["football"] != p.get("first"):
                ppl.append({"id": pid, "first": p["football"], "last": p.get("last", ""), "team": p["team"],
                            "pos": p.get("pos")})
        return names.Matcher(ppl)

    def load_events(self):
        if self.events is None:
            self.events = record.read_events()
        return self.events

    # ---- steps
    def do_rules(self):
        out = emit.rules_doc(self.cfg)
        if common.read_json(common.dpath("rules.json")) != out:
            common.write_json(common.dpath("rules.json"), out)
            return {"note": "rules.json written"}
        return {"status": "skipped", "note": "unchanged"}

    def do_schedule(self):
        self.cache, res = sched.update(self.now, self.cfg)
        self.load_schedule()
        if self.cache and res["status"] == "ok":
            res["note"] = "%d game(s) this week (%s); %s" % (len(self.games), self.slate, res["note"])
        return res

    def do_stats(self, force=False):
        self.load_schedule()
        if not self.season:
            return {"status": "skipped", "note": "no schedule"}
        if self.dry_run:
            return {"status": "skipped", "note": "dry run"}
        return stats.update(self.season, self.now, self.cfg, force=force)

    def do_roster(self):
        self.load_schedule()
        if not self.season:
            return {"status": "skipped", "note": "no schedule"}
        res, self.roster = roster.update(self.now, self.season, self.cfg)
        return res

    def do_injuries(self):
        self.load_schedule()
        if not self.games:
            return {"status": "skipped", "note": "no games this week"}
        res = injuries.check(self.now, self.games, self.cfg, self.load_roster(), self.season, self.started,
                             week_key=self.slate)
        self.inj, self.ina = res["state"], res["inactives"]
        posted = sum(1 for g in self.games for t in (g["away"], g["home"]) if injuries.team_confirmed(self.ina, g, t))
        if posted:
            res["note"] += "; inactives posted for %d team(s)" % posted
        return res

    def do_weather(self):
        self.load_schedule()
        if not self.games:
            return {"status": "skipped", "note": "no games this week"}
        return weather.check(self.now, self.games, self.cfg)

    def report_times(self):
        """{game id: when the final injury report counts as in} (R runs from then, on a fresh injury read)."""
        return {g["id"]: injuries.report_counts_at(g, self.cfg) for g in self.games}

    def report_ok(self):
        inj = self.inj if self.inj is not None else injuries.load()
        return {g["id"] for g in self.games if injuries.report_in(g, self.cfg, inj, self.now)}

    def injuries_problem(self):
        """None when the injury read is good enough to make a NEW pick on, else why not: the feed failed after the last good
        read, there has been no read yet, or the last good read is older than ops.injury_fresh_min."""
        inj = self.inj if self.inj is not None else injuries.load()
        at = common.parse_iso(inj.get("at"))
        if at is None:
            return "no injury read yet"
        fail = common.parse_iso(inj.get("last_fail_at"))
        if fail is not None and fail > at:
            return "the injury feed is not answering"
        if (self.now - at).total_seconds() / 60.0 > self.cfg["ops"].get("injury_fresh_min", 120):
            return "the last injury read is old"
        return None

    def confirmed(self):
        ina = self.ina if self.ina is not None else injuries.load_inactives()
        return {g["id"] for g in self.games if injuries.game_confirmed(ina, g)}

    def by_game(self):
        return gg.reads_by_game(odds.load_fetches(self.games, self.now), self.now)

    def do_odds(self):
        self.load_schedule()
        if not self.cache:
            return {"status": "skipped", "note": "no schedule"}
        inp = self.build_inputs()
        items = record.fold(self.load_events())
        open_items = [it for it in items.values() if it["slate"] == self.slate and record.is_open(it)
                      and it["kind"] == "single" and it["era"] == gg.era(self.cfg)]
        focus = engine.focus_markets(inp, self.by_game(), open_items)
        window = sched.games_in_window(self.cache, self.now, self.cfg)
        left = len(window)
        reserve = {g["id"]: g for g in window}                 # R and C are owed to these before any watch-only W read
        reserve.update({g["id"]: g for g in sched.games_left_before_reset(self.cache, self.now)})
        self.odds_res = odds.run_pulls(self.now, self.slate, self.games, left, self.cfg, self.matcher_for, focus,
                                       self.report_times(), self.report_ok(), self.confirmed(), self.started,
                                       dry_run=self.dry_run, reserve_games=list(reserve.values()))
        self.inp = None                                     # prices changed: rebuild the inputs for the engine
        return {"status": self.odds_res["status"], "note": self.odds_res["note"]}

    def build_inputs(self):
        self.load_schedule()
        self.load_stats()
        if self.inj is None:
            self.inj = injuries.load()
        if self.ina is None:
            self.ina = injuries.load_inactives()
        fetches = odds.load_fetches(self.games, self.now)
        self.view = odds.build_view(fetches, self.cfg)
        self.inp = engine.Inputs(self.cfg, self.now, self.slate, self.season, self.games,
                                 self.store or stats.Store(), self.behind, self.view, self.inj, self.ina, self.people(),
                                 self.load_events(), cache=self.cache, weather=weather.load())
        return self.inp

    def do_engine(self):
        self.load_schedule()
        if not self.cache:
            return {"status": "skipped", "note": "no schedule"}
        events = self.load_events()
        pause = engine.safety_pause(events, self.cfg)
        self.paused = {"safety": pause} if pause else {}
        inp = self.build_inputs()
        inj_problem = self.injuries_problem()
        block = bool(self.stats_problems) or bool(pause) or bool(inj_problem)
        log_items = gg.fold(gg.read_events())
        new, self.extra = engine.evaluate(inp, self.by_game(), block_new=block, log_items=log_items)
        record.append(new)
        gg.append(self.extra.get("log") or [])
        self.events = inp.events + new
        self.new_events += len(new)
        shown = sum(1 for e in new if e["t"] == "shown")
        pulled = sum(1 for e in new if e["t"] == "pulled")
        locked = sum(1 for e in new if e["t"] == "locked")
        note = "%d new event(s): %d shown, %d pulled, %d locked" % (len(new), shown, pulled, locked) if new else "no change"
        if pause:
            note += "; new picks stopped (safety pause)"
        elif inj_problem:
            note += "; new picks blocked (%s)" % inj_problem
        elif self.stats_problems:
            note += "; new picks blocked (%s)" % "; ".join(self.stats_problems)
        return {"note": note}

    def do_watch(self):
        """Behind-the-scenes research tracks (scripts/watch.py). Soft by design: whatever goes wrong here is a note, never a
        failed step, so it can never mark the page or the run as failed. Writes only data/watch/."""
        if not watch.enabled(self.cfg):
            return {"status": "skipped", "note": "off"}
        try:
            self.load_schedule()
            if not self.cache or not self.games:
                return {"status": "skipped", "note": "no games this week"}
            self.load_stats()
            inp = self.inp or self.build_inputs()
            by_game = self.by_game()
            old = watch.read_events()
            new = watch.scan(inp, engine, by_game, old, self.cfg, self.now)
            new += watch.closes(old + new, by_game, inp.games, self.cfg, self.now)
            if self.ina is None:
                self.ina = injuries.load_inactives()
            graded = watch.grade_new(old + new, self.cfg, self.now, self.cache, self.store or stats.Store(), self.ina,
                                     self.load_roster())
            new += graded
            allev = old + new
            if not self.dry_run:
                watch.append(new)
                ledger = common.read_json(odds.ledger_path(), {}) or {}
                watch.write_summary(allev, self.cfg, self.now, watch.w_credits(ledger, self.now))
            n = lambda t: sum(1 for e in new if e["t"] == t)    # noqa: E731
            return {"status": "ok" if new else "skipped",
                    "note": "%d move(s)/side(s) scanned, %d flagged, %d graded" % (n("cand"), n("flag"), n("graded"))}
        except Exception as e:
            if os.environ.get("PC_RAISE"):
                raise
            common.log("watch failed (soft): %s" % common.short_err(e))
            return {"status": "skipped", "note": "watch failed (soft): %s" % common.short_err(e)}

    def do_grade(self):
        self.load_schedule()
        self.load_stats()
        if self.ina is None:
            self.ina = injuries.load_inactives()
        new, notes = grade.grade(self.load_events(), self.now, self.cfg, self.cache, self.store or stats.Store(),
                                 self.ina, self.load_roster())
        record.append(new)
        self.events = self.events + new
        self.new_events += len(new)
        n = sum(1 for e in new if e["t"] == "graded")
        note = "%d graded" % n if n else "nothing to grade"
        if notes:
            note += "; " + "; ".join(notes[:3])
        return {"status": "ok" if n else "skipped", "note": note}

    # ---- facts shared by slate.json and status.json
    def facts(self, last_run):
        cfg, v, now = self.cfg, self.cfg["values"], self.now
        st = odds.load_state(self.slate) if self.slate else {}
        led = common.read_json(odds.ledger_path(), {}) or {}
        key_state = led.get("keys", [])
        keys = odds.load_keys()
        fixture = bool(odds.fixture_dir())
        no_keys = not keys and not fixture
        left_games = len(sched.games_in_window(self.cache, now, cfg)) if self.cache else 0
        plan = st.get("plan")
        pool = odds.pool_summary(key_state, plan, left_games, cfg, led.get("spent", []), now)
        refused = not no_keys and odds.keys_refused(key_state, len(keys or key_state))
        view = self.view if self.view is not None else odds.build_view(odds.load_fetches(self.games, now), cfg)
        props_at = max([gv["props_at"] for gv in view.values() if gv.get("props_at")] or [None],
                       key=lambda x: x or now - timedelta(days=99))
        lines_at = max([gv["lines_at"] for gv in view.values() if gv.get("lines_at")] or [None],
                       key=lambda x: x or now - timedelta(days=99))
        read_at = max([x for x in (props_at, lines_at) if x] or [None], key=lambda x: x or now)
        nxt = (self.odds_res or {}).get("next_at")
        if nxt is None and plan and not refused:
            nxt = odds.next_check_at(now, self.games, plan, cfg, odds.done_map(st), self.report_times(), self.started,
                                     self.confirmed())
        # odds state
        if refused:
            ost = "keys_refused"
        elif pool["empty"]:
            ost = "pool_empty"
        elif no_keys:
            ost = "not_yet"
        elif st.get("last_fail_at") and (read_at is None or common.parse_iso(st["last_fail_at"]) > read_at):
            ost = "feed_down"
        elif st.get("skipped"):
            ost = "limited"
        elif read_at:
            ost = "ok"
        else:
            ost = "not_yet"
        # props state per game
        props = {}
        for g in self.games:
            gv = view.get(g["id"]) or {}
            if gv.get("props_at"):
                ps, note = "ok", None
            elif g["id"] in (st.get("skipped") or {}):
                ps, note = "not_pulled", "Not priced yet: saving odds credits."
            else:
                ps = "none_yet"
                note = "No Hard Rock player props yet." + (" Next check %s." % _day_time(nxt, now) if nxt else "")
            k = common.parse_iso(g["start"])
            if note is None and not self.started.get(g["id"]) and \
                    now >= k - timedelta(minutes=v.get("inactives_deadline_min", 60)) and \
                    not injuries.game_confirmed(self.ina if self.ina is not None else injuries.load_inactives(), g):
                note = "Inactives not confirmed yet."
            props[g["id"]] = (ps, note)
        # injuries / inactives
        inj = self.inj if self.inj is not None else injuries.load()
        ina = self.ina if self.ina is not None else injuries.load_inactives()
        inj_down = bool(inj.get("last_fail_at")) and (not inj.get("at") or inj["last_fail_at"] > inj["at"])
        if not inj_down and inj.get("at") and self.injuries_problem() == "the last injury read is old":
            inj_down = True
        inj_state = "down" if inj_down else ("ok" if inj.get("at") else "not_yet")
        posted = [g for g in self.games if all(injuries.team_confirmed(ina, g, t) for t in (g["away"], g["home"]))]
        ina_at = max([t.get("confirmed_at") for rec in ina.values() if isinstance(rec, dict)
                      for t in rec.values() if isinstance(t, dict) and t.get("confirmed_at")] or [None],
                     key=lambda x: x or "")
        upcoming = [g for g in self.games if not self.started.get(g["id"])]
        if posted:
            times = sorted({common.fmt_time(common.parse_iso(g["start"])) for g in posted
                            if not self.started.get(g["id"])})
            ina_note = "Posted for the %s game%s." % (", ".join(times), "" if len(times) == 1 else "s") if times else None
            ina_state = "ok"
        else:
            ina_note = "Not posted yet. They come out about 90 minutes before kickoff." if upcoming else None
            ina_state = "not_yet" if not inj_down else "down"
        stats_state = "behind" if (self.stats_problems or any(t in self.behind for g in upcoming
                                                              for t in (g["away"], g["home"]))) else "ok"
        meta = common.read_json(stats.meta_path(), {}) or {}
        through_week = None
        if self.store and self.store.games:
            through_week = max((gm["week"] for gm in self.store.games.values() if gm["season"] == self.season),
                               default=None)
        status = {
            "odds": {"state": ost, "at": common.iso(read_at) if read_at else None,
                     "next_at": common.iso(nxt) if nxt else None},
            "injuries": {"state": inj_state, "at": inj.get("at")},
            "inactives": {"state": ina_state, "at": ina_at, "note": ina_note},
            "stats": {"state": stats_state, "through_week": through_week, "at": meta.get("built_at")},
            "pool": {k: pool[k] for k in ("credits_left", "keys", "keys_live", "keys_shared", "days_left", "low",
                                          "empty")},
            "last_run": last_run,
        }
        banners = []
        if not last_run["ok"]:
            banners.append({"level": "warn", "text": "Part of the last update failed (%s). Some of what is below may "
                                                     "be older." % last_run["failed_step"]})
        if no_keys:
            banners.append({"level": "stop", "text": "Hard Rock prices are not connected: no odds keys are set."})
        elif refused:
            banners.append({"level": "stop", "text": odds.KEYS_REFUSED_TEXT})
        elif pool["empty"]:
            banners.append({"level": "stop", "text": "Out of odds keys: no new Hard Rock prices until more keys are "
                                                     "added. No new picks."})
        elif pool["low"]:
            n = left_games
            banners.append({"level": "warn", "text": "Odds credits are low: %s left for %d game%s this week. Some games get "
                                                     "fewer price checks until more keys are added." % (
                                                         "{:,}".format(pool["credits_left"]), n, "" if n == 1 else "s")})
        if ost == "feed_down":
            banners.append({"level": "warn", "text": "The odds feed did not answer at %s. Trying again on the next "
                                                     "check." % common.fmt_time(common.parse_iso(st["last_fail_at"]))})
        if inj_down:
            banners.append({"level": "warn", "text": "Injury news isn't loading. No new picks until it does."})
        behind = sorted({t for g in upcoming for t in (g["away"], g["home"]) if t in self.behind})
        if behind:
            banners.append({"level": "warn", "text": "No new picks on %s yet: last game's stats are not in." %
                                                     ", ".join(behind)})
        for text in (self.paused or {}).values():
            banners.append({"level": "warn", "text": text})
        unmatched = sorted({n for gv in view.values() for n in gv.get("unmatched", set())})
        wk = self.games[0]["week"] if self.games else None
        done = odds.done_map(st)
        price_final = {gid: any(f["pull"] == "C" and f.get("ina") for f in reads) for gid, reads in done.items()}
        anchor = sched.slate_anchor(now, cfg)
        valid_until = common.iso(anchor + timedelta(days=7, hours=1))
        return {"status": status, "banners": banners, "props": props, "season": self.season, "week": wk,
                "valid_until": valid_until, "next_check": common.iso(nxt) if nxt else None,
                "injuries_down": inj_down, "price_final": price_final,
                "_keys": [{"n": k["n"], "left": k.get("left"), "shared": bool(k.get("shared"))} for k in key_state], "_plan": plan, "_pool": pool,
                "_unmatched": unmatched, "_unknown_teams": st.get("unknown_teams", []),
                "_spent_today": sum(e["cost"] for e in led.get("spent", []) if e["date"] == now.date().isoformat())}

    def do_slate(self, facts):
        if self.inp is None:
            if self.extra is None:
                return {"status": "skipped", "note": "engine did not run"}
            self.build_inputs()
        doc = emit.build_slate(self.inp, self.events, self.extra, facts)
        self.slate_doc = doc
        path = common.dpath("slate.json")
        old = common.read_json(path)
        if old and _essence(old) == _essence(doc) and not self._heartbeat_due(old.get("generated_at")):
            self.wrote_slate = True
            return {"status": "skipped", "note": "unchanged"}
        common.write_json(path, doc)
        self.wrote_slate = True
        return {"note": "%d open pick(s), %d pulled" % (sum(1 for p in doc["picks"] if p["state"] == "open"),
                                                        sum(1 for p in doc["picks"] if p["state"] == "pulled"))}

    def _heartbeat_due(self, stamp):
        last = common.parse_iso(stamp) if stamp else None
        return last is None or self.now < last or \
            self.now - last >= timedelta(minutes=self.cfg["ops"]["heartbeat_min"])

    def do_record(self):
        changed = record.write_views(self.cfg, self.load_events())
        return {"status": "ok" if changed else "skipped", "note": ", ".join(changed) or "unchanged"}

    def write_status(self, facts, started_at):
        failed = self.failed_step()
        path = common.dpath("status.json")
        old = common.read_json(path) or {}
        src = {k: dict(v) for k, v in common.SOURCES.items()}
        src["odds"] = dict(odds.SOURCE)
        reached = dict(old.get("sources") or {})
        for k, s in src.items():
            if s["ok"] or s["failed"]:
                reached[k] = "ok" if not s["failed"] else "failed" if not s["ok"] else "partly"
        plan = facts.get("_plan") or {}
        st = {
            "ok": failed is None, "season": self.season, "week": facts.get("week"), "slate": self.slate,
            "started_at": common.iso(started_at), "status_at": common.iso(self.now),
            "finished_at": common.iso(common.now() if not os.environ.get("PC_NOW") else self.now),
            "last_ok_at": common.iso(self.now) if failed is None else old.get("last_ok_at"),
            "failed_step": failed, "steps": self.steps, "sources": reached,
            "keys": facts.get("_keys", []), "pool_total": (facts.get("_pool") or {}).get("credits_left"),
            "allowance": plan.get("allowance"), "tier": plan.get("tier"), "games_left_before_reset": plan.get("games_left"),
            "budget_used_today": facts.get("_spent_today", 0),
            "odds": (facts.get("status") or {}).get("odds"), "due": (self.odds_res or {}).get("due"),
            "skipped_games": sorted(((odds.load_state(self.slate) if self.slate else {}).get("skipped") or {}).keys()),
            "unmatched_names": facts.get("_unmatched", []), "unknown_teams": facts.get("_unknown_teams", []),
            "behind": self.behind, "stats_problems": self.stats_problems,
            "games_this_week": len(self.games), "record_events": len(self.events) if self.events is not None else None,
            "grade_checks": grade.cross_check(self.events or [], self.store) if self.store else [],
            "odds_notes": sorted(set((self.odds_res or {}).get("errors", [])))[:6],
            "runner": runner_info(),
            "live_page": live_page_check(old.get("live_page"), self.now),
        }
        if old and _essence(old) == _essence(st) and not self._heartbeat_due(old.get("finished_at")):
            return
        common.write_json(path, st)


def _day_time(dt, now):
    if dt is None:
        return None
    dt = dt.astimezone(common.ET)
    if dt.date() == now.date():
        return common.fmt_time(dt)
    return "%s %s" % (dt.strftime("%a"), common.fmt_time(dt))


def runner_info():
    env = os.environ
    if not env.get("PC_RUNNER_RUN_ID") and not env.get("PC_RUNNER_EVENT"):
        return {"event": "local", "run_id": None, "iteration": None, "chain_link": None, "chain_started_at": None}

    def num(k):
        try:
            return int(env.get(k, ""))
        except ValueError:
            return None
    return {"event": env.get("PC_RUNNER_EVENT"), "run_id": env.get("PC_RUNNER_RUN_ID"),
            "iteration": num("PC_RUNNER_ITERATION"), "chain_link": num("PC_RUNNER_LINK"),
            "chain_started_at": env.get("PC_RUNNER_CHAIN_STARTED") or None,
            "run_started_at": env.get("PC_RUNNER_RUN_STARTED") or None}


def _essence(doc):
    """A file with its clock stamps removed: tells 'something changed' from 'only time passed'."""
    d = json.loads(common.dumps(doc, False))
    # also this run's own diagnostics (what was due, the feed's error notes): they say what the run did, not what
    # the data is; a failure still shows through steps / sources below, and the heartbeat writes them anyway
    for k in ("generated_at", "status_at", "started_at", "finished_at", "last_ok_at", "runner", "updated_at", "due",
              "odds_notes"):
        d.pop(k, None)
    if isinstance(d.get("steps"), list):
        d["steps"] = [(s["step"], s["note"]) for s in d["steps"] if s["status"] == "failed"]
    if isinstance(d.get("sources"), dict):
        d["sources"] = sorted(k for k, v in d["sources"].items() if v in ("failed", "partly"))
    if isinstance(d.get("status"), dict):
        lr = d["status"].get("last_run")
        if isinstance(lr, dict):
            lr.pop("at", None)
    if isinstance(d.get("games"), list):
        for g in d["games"]:
            if isinstance(g, dict):
                g.pop("updated_at", None)
    return d


def run(only=None, dry_run=False):
    cfg = common.load_config()
    engine.check_config(cfg)
    now = common.now()
    started_at = now
    odds.load_keys()                                        # registers the keys so nothing can print them
    for k in ("GITHUB_TOKEN", "GH_TOKEN"):
        common.register_secret(os.environ.get(k))
    common.SOURCES.clear()
    odds.SOURCE.update(ok=0, failed=0)
    r = Run(cfg, now, only, dry_run)
    r.step("rules", r.do_rules)
    r.step("schedule", r.do_schedule)
    r.load_schedule()
    r.step("stats", r.do_stats)
    r.step("roster", r.do_roster)
    r.step("injuries", r.do_injuries)
    r.step("weather", r.do_weather)
    r.step("odds", r.do_odds)
    # a pick enters the Record in the same commit that first shows it: if slate.json is then NOT written, this
    # run's events are rolled back to the exact bytes the log had before and the next run makes them again
    record_before = record.read_raw() if r.wants("slate") else None
    r.step("engine", r.do_engine)
    r.step("grade", r.do_grade)
    r.step("watch", r.do_watch)
    failed = r.failed_step()
    last_run = {"ok": failed is None, "at": common.iso(now), "failed_step": failed}
    facts = None
    try:
        r.load_schedule()
        r.load_stats()
        if r.slate:
            facts = r.facts(last_run)
    except Exception as e:
        r.steps.append({"step": "facts", "status": "failed", "note": common.short_err(e)})
        if os.environ.get("PC_RAISE"):
            raise
    if facts is not None:
        r.step("slate", lambda: r.do_slate(facts))
    if record_before is not None and not r.wrote_slate and record.read_raw() != record_before:
        common.write_bytes(record.events_path(), record_before)
        r.events = record.parse(record_before)
        r.steps.append({"step": "record", "status": "failed",
                        "note": "page not updated, so this run's Record events were taken back"})
    if not r.wrote_slate and r.failed_step() and r.wants("slate"):
        _mark_slate_failed(now, r.failed_step())
    r.step("record", r.do_record)
    if facts is None:
        facts = {"status": {}}
    try:
        r.write_status(facts, started_at)
    except Exception as e:
        common.log("status write failed: %s" % common.short_err(e))
        raise
    for s in r.steps:
        common.log("%-9s %-8s %s" % (s["step"], s["status"], s["note"]))
    return r


def _mark_slate_failed(now, step):
    path = common.dpath("slate.json")
    sl = common.read_json(path)
    if not isinstance(sl, dict) or "status" not in sl:
        return
    mark = "The last update failed"
    sl["status"]["last_run"] = {"ok": False, "at": common.iso(now), "failed_step": step}
    sl["status_at"] = common.iso(now)
    shown = common.fmt_time(common.parse_iso(sl["generated_at"])) if sl.get("generated_at") else "earlier"
    sl["banners"] = [{"level": "stop", "text": "%s at %s (%s). Showing %s data." % (mark, common.fmt_time(now), step, shown)}] + \
                    [b for b in sl.get("banners", []) if not str(b.get("text", "")).startswith(mark)]
    common.write_json(path, sl)


def dry_run_main():
    src = common.data_dir()
    tmp = tempfile.mkdtemp(prefix="pc_dry_")
    try:
        dst = os.path.join(tmp, "data")
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            os.makedirs(dst)
        os.environ["PC_DATA_DIR"] = dst
        r = run(dry_run=True)
        print("DRY RUN (nothing in %s was changed, no odds credits spent)" % src)
        print("  new Record events: %d" % r.new_events)
        sl = common.read_json(os.path.join(dst, "slate.json")) or {}
        for p in sl.get("picks", []):
            print("  %s %s %s %s  %s  final EV %s%%  [%s]" % (
                p.get("rank") or "-", p["player"], p["bet"], common.fmt_price(p["price"]), p["vs"], p["final_ev_pct"],
                "pulled" if p["pulled"] else p["settled_text"]))
        if sl.get("empty"):
            print("  empty: %s %s" % (sl["empty"]["text"], sl["empty"].get("sub") or ""))
        for b in sl.get("banners", []):
            print("  banner: %s" % b["text"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


def explain_main():
    r = Run(common.load_config(), common.now(), readonly=True)
    r.load_schedule()
    rows = engine.explain(r.build_inputs(), r.by_game())
    cols = ("player", "team", "market", "line", "side", "price", "read", "n_books", "market_fair", "gap_ev", "final_ev",
            "verdict")
    print(" | ".join(cols))
    for x in sorted(rows, key=lambda x: -(x.get("gap_ev") if x.get("gap_ev") is not None else -9)):
        print(" | ".join(("%.4f" % x[c]) if isinstance(x.get(c), float) else str(x.get(c, "")) for c in cols))
    from collections import Counter
    print(Counter(x["verdict"].split(":")[0] for x in rows).most_common())
    return 0


def main(argv):
    common.install_scrubbers()
    if "--rebuild-record" in argv:
        changed = record.write_views(common.load_config())
        print("record rebuilt from the log: %s" % (", ".join(changed) or "no file changed"))
        return 0
    if "--dry-run" in argv:
        return dry_run_main()
    if "--explain" in argv:
        return explain_main()
    if "--rebuild-stats" in argv:
        r = Run(common.load_config(), common.now())
        r.load_schedule()
        print(r.do_stats(force=True))
        return 0
    only = None
    if "--only" in argv:
        only = set()
        for i, a in enumerate(argv):
            if a == "--only" and i + 1 < len(argv):
                only |= set(argv[i + 1].split(","))
        bad = only - set(STEPS)
        if bad:
            print("unknown step(s): %s. Steps: %s" % (", ".join(sorted(bad)), ", ".join(STEPS)))
            return 2
        only |= {"status"}
    run(only=only)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
