"""Probe 2: inactives source, ESPN pregame shape, Pages config. No odds credits."""
import json, os, subprocess, urllib.request, urllib.error, gzip
OUT = "probe/out2"; os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/126 Safari/537.36"}
rep = {}
def get(name, url, keep=True):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            b = r.read()
            if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
            if keep: open(f"{OUT}/{name}.json", "wb").write(b)
            rep[name] = [r.status, len(b)]; return b
    except urllib.error.HTTPError as e:
        rep[name] = [e.code, e.read()[:200].decode("utf-8", "replace")]
    except Exception as e:
        rep[name] = [0, type(e).__name__]
C = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
S = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
get("core_roster_final_DAL", f"{C}/events/401872980/competitions/401872980/competitors/6/roster")
get("core_status_final", f"{C}/events/401872980/competitions/401872980/status")
sb = get("sb_week6", f"{S}/scoreboard?dates=20261015-20261020")
try:
    ev = json.loads(sb)["events"]; rep["week6"] = [(e["id"], e["shortName"], e["date"]) for e in ev]
    e0 = ev[0]["id"]; comp = ev[0]["competitions"][0]
    get("summary_pre", f"{S}/summary?event={e0}")
    for c in comp["competitors"]:
        get(f"core_roster_pre_{c['team']['abbreviation']}", f"{C}/events/{e0}/competitions/{e0}/competitors/{c['id']}/roster")
        get(f"team_injuries_{c['team']['abbreviation']}", f"{C}/teams/{c['id']}/injuries?limit=100")
        get(f"site_team_{c['team']['abbreviation']}", f"{S}/teams/{c['id']}?enable=roster,injuries")
except Exception as ex:
    rep["week6_err"] = repr(ex)
get("depthchart_DEN", f"{C}/seasons/2026/teams/7/depthcharts")
get("nflverse_depth", "https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_2026.csv", keep=False)
get("nflverse_stats", "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.csv", keep=False)
get("pages_site", "https://msasportas.github.io/prop-check-nfl/", keep=False)
r = subprocess.run(["gh", "api", f"repos/{os.environ.get('GITHUB_REPOSITORY')}/pages"], capture_output=True, text=True)
rep["pages_api"] = (r.stdout or r.stderr)[:800]
json.dump(rep, open(f"{OUT}/report.json", "w"), indent=1)
print(json.dumps(rep, indent=1)[:3000])
