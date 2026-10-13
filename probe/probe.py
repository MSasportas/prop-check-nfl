"""Probe from the GitHub runner. Never prints or writes a secret value: only secret NAMES and counts."""
import json, os, re, urllib.request, urllib.error, gzip, time
OUT = "probe/out"; os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
rep = {"when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

def get(url, keep=None, headers=False):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip": body = gzip.decompress(body)
            h = dict(r.headers)
            if keep: open(os.path.join(OUT, keep), "wb").write(body)
            return (r.status, body, h) if headers else (r.status, body)
    except urllib.error.HTTPError as e:
        return (e.code, e.read()[:300], dict(e.headers)) if headers else (e.code, e.read()[:300])
    except Exception as e:
        return (0, str(type(e).__name__).encode(), {}) if headers else (0, str(type(e).__name__).encode())

# 1. secrets: names only
sec = {k[4:]: v for k, v in os.environ.items() if k.startswith("SEC_") and v}
names = sorted(sec)
rep["secret_names"] = names
keys = []
for k in names:
    if "ODDS" in k.upper():
        vals = [v.strip() for v in re.split(r"[,\s]+", sec[k] or "") if v.strip()]
        rep.setdefault("odds_secrets", {})[k] = len(vals)
        keys += vals
keys = list(dict.fromkeys(keys))
rep["odds_key_count"] = len(keys)
API = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
creds = []
for i, k in enumerate(keys):
    st, body, h = get(f"https://api.the-odds-api.com/v4/sports?apiKey={k}", headers=True)
    creds.append({"pos": i, "status": st, "remaining": h.get("x-requests-remaining"), "used": h.get("x-requests-used")})
rep["key_credits"] = creds

# 2. free / public sources
src = {
 "espn_scoreboard": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
 "espn_injuries": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
 "espn_team_roster_DEN": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/7/roster",
 "sleeper_players": "https://api.sleeper.app/v1/players/nfl",
 "nflverse_pbp": "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_2026.csv.gz",
 "nfl_games_csv": "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv",
 "open_meteo": "https://api.open-meteo.com/v1/forecast?latitude=39.74&longitude=-104.99&hourly=precipitation,wind_speed_10m&forecast_days=3",
}
rep["sources"] = {}
for n, u in src.items():
    keep = None if n in ("sleeper_players", "nflverse_pbp", "nfl_games_csv") else n + ".json"
    st, body = get(u, keep)
    rep["sources"][n] = {"status": st, "bytes": len(body)}

# ESPN game summary for a finished game (box + play-by-play shape), and the event ids this week
try:
    sb = json.load(open(os.path.join(OUT, "espn_scoreboard.json")))
    evs = [(e["id"], e["shortName"], e["status"]["type"]["state"]) for e in sb.get("events", [])]
    rep["espn_events"] = evs
    done = [e for e in evs if e[2] == "post"]
    if done:
        st, body = get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={done[0][0]}", "espn_summary_final.json")
        rep["sources"]["espn_summary_final"] = {"status": st, "bytes": len(body)}
    pre = [e for e in evs if e[2] == "pre"]
    if pre:
        st, body = get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={pre[0][0]}", "espn_summary_pre.json")
        rep["sources"]["espn_summary_pre"] = {"status": st, "bytes": len(body)}
except Exception as e:
    rep["espn_err"] = type(e).__name__

# 3. Hard Rock: events (free), market list for 2 events (1 credit each), one full player pull for the first event
best = max((c for c in creds if c["status"] == 200 and c["remaining"]), key=lambda c: float(c["remaining"]), default=None)
if best:
    k = keys[best["pos"]]
    st, body = get(f"{API}/events?apiKey={k}")
    evs = json.loads(body) if st == 200 else []
    rep["odds_events"] = [(e["id"], e["away_team"], e["home_team"], e["commence_time"]) for e in evs]
    mk = {}
    for e in evs[:2]:
        st, body, h = get(f"{API}/events/{e['id']}/markets?apiKey={k}&regions=us&bookmakers=hardrockbet_fl,draftkings", headers=True)
        open(os.path.join(OUT, f"markets_{e['away_team'][:3]}_{e['home_team'][:3]}.json".replace(" ", "")), "wb").write(body if st == 200 else b"{}")
        if st == 200:
            for b in json.loads(body).get("bookmakers", []):
                mk.setdefault(e["id"], {})[b["key"]] = sorted(m["key"] for m in b["markets"])
        rep.setdefault("markets_cost", []).append(h.get("x-requests-last"))
    rep["markets"] = mk
    if evs and evs[0]["id"] in mk and "hardrockbet_fl" in mk[evs[0]["id"]]:
        want = [m for m in mk[evs[0]["id"]]["hardrockbet_fl"] if m.startswith("player_") and m != "player_receptions_h1"]
        st, body, h = get(f"{API}/events/{evs[0]['id']}/odds?apiKey={k}&regions=us&bookmakers=hardrockbet_fl&oddsFormat=american&markets={','.join(want)}", headers=True)
        rep["full_pull"] = {"status": st, "markets_asked": len(want), "cost": h.get("x-requests-last"), "remaining": h.get("x-requests-remaining")}
        if st == 200: open(os.path.join(OUT, "odds_event0_hardrock.json"), "wb").write(body)
        else: rep["full_pull"]["err"] = body[:200].decode("utf-8", "replace")
        st, body, h = get(f"{API}/events/{evs[0]['id']}/odds?apiKey={k}&regions=us&bookmakers=hardrockbet_fl,draftkings&oddsFormat=american&markets=h2h,spreads,totals,spreads_h1,totals_h1,team_totals", headers=True)
        rep["game_pull"] = {"status": st, "cost": h.get("x-requests-last")}
        if st == 200: open(os.path.join(OUT, "odds_event0_game.json"), "wb").write(body)
s = json.dumps(rep, indent=1)
for k in keys: s = s.replace(k, "***")
open(os.path.join(OUT, "report.json"), "w").write(s)
print(s[:4000])
# probe v2
