"""stats.py: the per-player-game rows the model reads and the grader trusts (built by nflrows.py).

  data/state/rows<season>.json.gz   one file per season. Finished seasons (2024, 2025) are committed seeds and
                                    never change. This season's file is rebuilt from nflverse play-by-play
                                    whenever the release asset changes (size or updated time), never otherwise.
  data/state/stats_meta.json        what we last saw of the release assets and when we built.

A team is BEHIND when a game of theirs is final (schedule) but not in the rows yet. Its players get no new picks
(picks already shown stay) and the page names the team. nflverse usually posts a game's plays the next morning.
"""
import gzip
import json
import os
import tempfile
from datetime import timedelta

import common
import nflrows

REL = "https://github.com/nflverse/nflverse-data/releases/download/%s/%s"
API = "https://api.github.com/repos/nflverse/nflverse-data/releases/tags/%s"
ASSETS = {  # what a rebuild of this season needs: (release tag, file name pattern)
    "pbp": ("pbp", "play_by_play_%d.csv.gz"),
    "roster": ("rosters", "roster_%d.csv"),
    "snaps": ("snap_counts", "snap_counts_%d.csv"),
}


def rows_path(season):
    return common.dpath("state", "rows%d.json.gz" % season)


def find_rows(season):
    for base in (common.data_dir(), common.REPO_DATA):
        p = os.path.join(base, "state", "rows%d.json.gz" % season)
        if os.path.exists(p):
            return p
    return None


def meta_path():
    return common.dpath("state", "stats_meta.json")


# ---------------------------------------------------------------- the store
class Store:
    """Rows for the seasons the model uses (this one and last), indexed for the engine and the grader."""

    def __init__(self, data_by_season=None):
        self.players, self.rows, self.team_rows, self.games = {}, [], [], {}
        for season, d in sorted((data_by_season or {}).items()):
            self.players.update(d["players"])
            self.rows += d["rows"]
            self.team_rows += d["team_rows"]
            self.games.update(d["games"])
        self.by_pid = {}
        for r in self.rows:
            self.by_pid.setdefault(r["pid"], []).append(r)
        for v in self.by_pid.values():
            v.sort(key=lambda r: (r["date"], r["gid"]))
        self.by_game = {}
        for r in self.rows:
            self.by_game.setdefault(r["gid"], {})[r["pid"]] = r
        self.team_games = {}
        for t in self.team_rows:
            self.team_games.setdefault(t["team"], []).append(t)
        for v in self.team_games.values():
            v.sort(key=lambda t: (t["date"], t["gid"]))
        self._opp = {}

    def has_game(self, gid):
        return gid in self.games

    def player_rows(self, pid, before=None):
        rs = self.by_pid.get(pid, [])
        return [r for r in rs if before is None or r["date"] < before]

    def opp_table(self, date, season, period):
        import model
        k = (date, season, period)
        if k not in self._opp:
            self._opp[k] = model.opp_table(self.team_rows, date, season, period)
        return self._opp[k]


def load(season):
    """Store for `season` and `season - 1`. Returns (Store, problems list)."""
    data, problems = {}, []
    for s in (season - 1, season):
        p = find_rows(s)
        if p is None:
            problems.append("no rows for %d" % s)
            continue
        try:
            data[s] = nflrows.load(p)
        except (OSError, ValueError, EOFError, KeyError) as e:
            problems.append("rows for %d unreadable (%s)" % (s, type(e).__name__))
    return Store(data), problems


# ---------------------------------------------------------------- keeping this season current
def _asset_info(tag, name):
    """dict(size, updated_at) for one release asset, or None."""
    headers = dict(common.UA, Accept="application/vnd.github+json")
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        common.register_secret(tok)
        headers["Authorization"] = "Bearer " + tok
    js = common.feed_json("github_api", API % tag, "release_%s.json" % tag, timeout=30, retries=1, headers=headers)
    for a in (js or {}).get("assets", []) or []:
        if a.get("name") == name:
            return {"size": a.get("size"), "updated_at": a.get("updated_at")}
    return None


def update(season, now, cfg, force=False):
    """Rebuild rows<season> when the play-by-play asset changed. Returns a status dict."""
    ops = cfg["ops"]
    meta = common.read_json(meta_path(), {}) or {}
    last_check = common.parse_iso(meta.get("checked_at"))
    every = timedelta(minutes=ops.get("stats_check_min", 60))
    if not force and last_check is not None and now - last_check < every and now >= last_check \
            and find_rows(season) is not None:
        return {"status": "skipped", "note": "checked at %s" % common.fmt_time(last_check)}
    tag, pat = ASSETS["pbp"]
    info = _asset_info(tag, pat % season)
    meta["checked_at"] = common.iso(now)
    old = meta.get("pbp") or {}
    built = common.read_json(meta_path(), {}) or {}
    if info is None:
        # the GitHub API did not answer: rebuild anyway once the last build is older than a day
        last_built = common.parse_iso(meta.get("built_at"))
        if not force and last_built is not None and now - last_built < timedelta(hours=24) and find_rows(season):
            common.write_json(meta_path(), meta)
            return {"status": "failed", "note": "release info did not load; rows from %s kept" % meta.get("built_at")}
    elif not force and info == old and find_rows(season) is not None and built.get("built_for") == info:
        common.write_json(meta_path(), meta)
        return {"status": "skipped", "note": "play-by-play unchanged (%s)" % (info.get("updated_at") or "?")}
    res = rebuild(season, now)
    if res["status"] == "ok":
        meta["pbp"] = info or old
        meta["built_for"] = info or old
        meta["built_at"] = common.iso(now)
        meta["games"] = res.get("games")
        meta["through"] = res.get("through")
    common.write_json(meta_path(), meta)
    return res


def rebuild(season, now):
    tmp = tempfile.mkdtemp(prefix="pc_rows_")
    try:
        paths = {}
        for k, (tag, pat) in ASSETS.items():
            name = pat % season
            body = common.feed_get("nflverse_" + k, REL % (tag, name), name, timeout=120, retries=2)
            if body is None:
                if k == "pbp":
                    return {"status": "failed", "note": "play-by-play download failed"}
                continue                                        # roster / snaps help, but rows build without them
            p = os.path.join(tmp, name)
            with open(p, "wb") as f:
                f.write(body)
            paths[k] = p
        if paths["pbp"].endswith(".gz"):
            try:
                with gzip.open(paths["pbp"], "rb") as g:
                    g.read(1)
            except (OSError, EOFError):
                return {"status": "failed", "note": "play-by-play download was damaged"}
        d = nflrows.build([paths["pbp"]], [paths["roster"]] if "roster" in paths else [],
                          [paths["snaps"]] if "snaps" in paths else [])
        if not d["rows"]:
            return {"status": "failed", "note": "play-by-play had no rows"}
        out = os.path.join(tmp, "rows.json.gz")
        nflrows.dump(d, out)
        with open(out, "rb") as f:
            raw = f.read()
        # re-pack with mtime 0 so the same data gives the same bytes (no empty commits)
        data = gzip.decompress(raw)
        import io
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:
            g.write(data)
        new = buf.getvalue()
        old = None
        if os.path.exists(rows_path(season)):
            with open(rows_path(season), "rb") as f:
                old = f.read()
        if old is None or gzip.decompress(old) != data:
            common.write_bytes(rows_path(season), new)
        through = max((g["date"] for g in d["games"].values()), default=None)
        return {"status": "ok", "note": "%d games through %s" % (len(d["games"]), through), "games": len(d["games"]),
                "through": through}
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def team_status(store, cache, now):
    """{team: reason} for teams with a final game missing from the rows, and the latest game date in the rows."""
    behind = {}
    for g in (cache or {}).get("games", {}).values():
        start = common.parse_iso(g["start"])
        if start > now or start < now - timedelta(days=21):
            continue
        lv = ((cache or {}).get("live") or {}).get(g["id"]) or {}
        final = lv.get("final") or (g.get("away_score") is not None and g.get("home_score") is not None)
        if final and not lv.get("postponed") and not store.has_game(g["id"]):
            for t in (g["away"], g["home"]):
                behind[t] = "last game's stats are not in yet"
    through = max((g["date"] for g in store.games.values()), default=None)
    return behind, through
