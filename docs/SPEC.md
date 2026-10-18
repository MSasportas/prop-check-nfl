# Prop Check NFL: build spec (CEO, 2026-10-13)

Working contract for the team. TEAM.md (written last) is the operating doc; this file is how the pieces fit.
Deadline: live and correct before SEA @ DEN, Thu Oct 15, 8:15 PM ET.

Reference build: `/home/claude/ref-nhl` (MSasportas/prop-check-nhl, READ ONLY, never push to it). Copy its
structure, the timer chain, the Record logic and the guard. Old NFL build (ideas only): `/home/claude/oldbuild/`
(`build_data.py`, `fetch_odds.py`, `meta/*.json`). Reference files: `scripts/ref/team_names.json` (odds feed name -> abbr),
`scripts/ref/stadiums.json` (nflverse stadium_id -> [lat, lon], `_roof` overrides), `scripts/ref/team_colors.json`.
Probe payloads from the real GitHub runner: `tests/fixtures/probe/` (Hard Rock props + game lines for SEA@DEN,
ESPN scoreboard / final summary / injuries / game roster, Open-Meteo).

## 1. Customer facts (fixed, from Matt)
Hard Rock Florida only (`hardrockbet_fl`); a pick needs a real Hard Rock number. No leg worse than -200; plus money
fine. Parlays 2-3 legs. Player props are the core, 1H and full game; sides and totals allowed. Hit-based: legs must
clear. Bets any time from early week to kickoff. Dark mode, phone width (~380 px), 1H / FG toggle.
The Record (fixed): every pick and parlay exactly as shown, graded after the game at the posted price, append-only,
pulled picks kept and marked pulled, nothing edited or dropped. Starts clean.

## 2. What the runner can reach (probe, 2026-10-13)
| Source | Use | Notes |
|---|---|---|
| the-odds-api v4, `americanfootball_nfl` | prices | secret `ODDS_API_KEYS`, 7 keys comma-separated, ~2,379 credits. 4 keys are ALSO used by Prop Check NHL (balances match), so ~1,030 credits are NFL-only. Cost = 1 credit per market returned per call (bookmakers list within one region costs once: HR + DK in one call = same cost). `/sports` and `/events` are free |
| ESPN site API `site.api.espn.com/apis/site/v2/sports/football/nfl` | scoreboard (schedule, live state, final, linescores by quarter), `summary?event=` (box score), `injuries` (all teams, status per player) | NOT reachable from a chat session, only from the runner |
| ESPN core API `sports.core.api.espn.com/v2/sports/football/leagues/nfl` | `events/{id}/competitions/{id}/competitors/{teamId}/roster`: game roster; `didNotPlay: true` marks inactives (8 of 57 for DAL in a final). Pregame population timing UNVERIFIED: treat "roster present with didNotPlay flags within 85 min of kickoff" as inactives confirmed; otherwise not confirmed | runner only |
| nflverse-data releases (GitHub) | `stats_player/stats_player_week_2026.csv`, `..._2025.csv`, `pbp/play_by_play_2026.csv.gz` (+2025), `injuries/injuries_2026.csv`, `snap_counts`, `weekly_rosters` | reachable from runner AND chats. pbp for Sunday games was present by Monday night. Source of 1H player stats (grading and model) |
| `raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv` | full schedule with kickoff ET, stadium_id, roof, espn id | runner + chats |
| Open-Meteo | kickoff wind / precipitation for outdoor games | runner |
| Sleeper `api.sleeper.app/v1/players/nfl` | backup injury status, 14 MB | runner |

ESPN play-by-play has no player ids, so 1H player results come from nflverse pbp (usually by the next morning). FG
player results come from ESPN's box score right after the final.

## 3. Hard Rock markets (SEA@DEN, probe)
Hard Rock FL carries: `h2h, spreads, totals, alternate_spreads, alternate_totals, alternate_team_totals`, period
markets, and player props `player_pass_yds, _alternate, player_pass_attempts, player_pass_completions, player_pass_tds,
_alternate, player_pass_interceptions, player_pass_longest_completion, player_pass_rush_yds, player_rush_yds, _alternate,
player_rush_attempts, _alternate, player_receptions, _alternate, player_reception_yds, _alternate, player_reception_longest,
player_rush_reception_yds, player_anytime_td (+_h1/_h2/_q1), player_1st_td, player_last_td, player_tds_over`,
1H: `player_pass_yds_h1, player_rush_yds_h1, player_reception_yds_h1` (and _q1 variants). Not every game carries every
market (HOU@JAX lacked completions, interceptions, rush_reception_yds? check per game).
- **Never request `player_receptions_h1`**: no book in the feed has it and an invalid market fails the whole call.
- **Hard Rock `spreads_h1` / `totals_h1` are junk** (one line, e.g. DEN +7.5 -400, O30.5 +425). 1H spread uses
  DraftKings `spreads_h1`, labeled "DraftKings price" everywhere it shows. 1H total: same feed problem; Sharp decides
  (DK labeled, or HR `team_totals_h1` added, or out).
- Full pull of 35 HR player markets for one game cost 35 credits. Game lines (5 markets HR+DK) cost 5.

## 4. Layout
```
index.html  manifest.webmanifest  .nojekyll  icons/
config/rules.json              Sharp's standard: every betting value lives here (page reads data/rules.json copy)
scripts/
  common.py      paths, clock (PC_NOW), atomic writes, polite HTTP, secret scrubbing      (port NHL)
  teams.py       32 teams: abbr, odds-feed name, ESPN id, nflverse abbr, stadium coords/roof, colors
  names.py       name matching odds/ESPN -> nflverse gsis id (port NHL rules: exact, then last+first initial unique)
  sched.py       season schedule cache: games.csv + ESPN scoreboard (state, scores, linescores)
  stats.py       DATA SCIENTIST: per-player per-game rows (FG and 1H) + team rows; 2025 seed + 2026 weekly
  model.py       DATA SCIENTIST: probabilities (API in section 7)
  injuries.py    ESPN injuries feed + final-report timing + inactives from ESPN game roster
  weather.py     Open-Meteo for outdoor games (wind, precip at kickoff)
  odds.py        the-odds-api client, key pool, ledger, pull plan
  engine.py      Sharp standard -> picks, parlays, pulls, settledness, lock
  record.py  guard_record.py                                                           (port NHL)
  grade.py       FG from ESPN summary box score; 1H from nflverse pbp; sides/totals from ESPN linescores
  research.py    data/research/*
  run.py         the one entry point (port NHL structure); --explain, --dry-run, --rebuild-record
  cycle.sh loop.sh dispatch_next.sh fail_status.py                                     (port NHL)
.github/workflows/run.yml      timer chain (port NHL; secret ODDS_API_KEYS)
data/
  slate.json  rules.json  status.json  record.json  record/events.jsonl  record/<YYYY-MM>.json
  research/index.json  research/<game_id>.json
  odds/<YYYY-MM-DD>.json       every price pulled (our own price history)
  state/...                    schedule, stats store, injuries, rosters, credits ledger
tests/  (python -m unittest discover -s tests)    fixtures/<name>/ (page QA: ?fixture=NAME, ?now=ISO)
```
Python 3.12 standard library only (no pandas/numpy on the runner; the timer must stay fast). Eastern time
everywhere (`America/New_York`). Every module degrades: a source that fails makes its step say so; nothing stops a run.

## 5. The football week (slate)
- A slate is one NFL week: Thursday through Monday. `slate_id` = `2026-W06`. The page shows the current slate until
  Tuesday 5:00 AM ET after MNF, then the next week.
- Game ids: nflverse `game_id` (e.g. `2026_06_SEA_DEN`). Keep ESPN event id and odds event id on the game.
- **Settledness** per game, shown on every pick (Sharp may rename):
  `early` (before the final injury report) -> `injury_report` (final report in for both teams: Wed ~4 PM ET for a
  Thu game, Fri ~4 PM for Sun, Sat ~4 PM for Mon; the "day before" for Thu, "two days before" otherwise; treat as
  in at 5:00 PM ET that day) -> `inactives` (ESPN game roster shows inactives, from ~90 min before kickoff) ->
  `locked` at kickoff.

## 6. Odds plan (Builder implements; Sharp owns the market list and values in rules.json)
- Pull per game, never for a game that has kicked off. Three pulls a game: **A** early (first cycle at/after
  `pull_a_time` on a day within `pull_a_days` of kickoff, retried daily if HR has not posted props), **B** after the
  final injury report (`injury_report` state), **C** after inactives or at `pull_c_min` before kickoff, whichever first.
- Allowance = NFL credits left / games left before the 1st (keys reset monthly; assume the 1st, like NHL). Credits on
  keys marked `shared` count at `shared_key_weight` (0.5). Tiers decide which pulls run with the full market set and
  which run only on markets holding a shown pick or a near-miss. Page warns when the pool is low.
- **Key pool:** keys from env `ODDS_API_KEYS` only (comma-separated). Never printed, written or logged (scrub every
  write and stdout). Ledger `data/state/credits.json` by position, never the key. **Shared-key detection:** record
  each key's remaining after our call; if at our next read it is lower than we left it, mark it `shared`. Spend NFL-only
  keys first, shared keys last. A 401/429 key is skipped and marked; refused keys = "keys_refused" on the page.
- Store every price pulled in `data/odds/<date>.json` (that is our price history for the Sharp's reviews).

## 7. Model API (Data Scientist owns `scripts/stats.py` and `scripts/model.py`)
```python
stats.load(now) -> Store                      # reads data/state/stats/*; builds/refreshes from nflverse when due
stats.refresh(now) -> dict status              # called by run.py; must be fast when nothing new (< 20 s)
model.player_prob(store, player_id, market, period, line, ctx) -> dict
    market in {"pass_yds","pass_att","pass_cmp","pass_tds","rush_yds","rush_att","rec","rec_yds","rush_rec_yds", ...}
    period in {"fg","h1"}
    ctx = {"game_id", "team", "opp", "home": bool, "spread": float|None (team's HR spread, + = underdog),
           "total": float|None (HR game total), "roof", "wind_mph", "precip_mm", "status": injury status|None,
           "teammates_out": [player_id...]}
    returns {"p_over": float, "mu": float, "n_games": int, "thin": bool,
             "basis": short str for the reason line, e.g. "7.1 targets a game, 68% catch rate",
             "last": [values of this stat in his last up-to-8 games, newest first],
             "eligible": bool, "why_not": str|None}
model.game_prob(store, game_id, market, period, line, side, ctx) -> same shape (spread/total), or None if not modeled
model.break_even(american) -> float
```
`p_over` must be calibrated (DS proves it in `docs/DS_REPORT.md`). `p_under = 1 - p_over - p_push` for whole-number
lines (DS returns `p_push` too when the line is a whole number).

## 8. Data contracts (page reads these; Builder writes them)
### data/slate.json
```json
{
 "slate_id": "2026-W06", "generated_at": "ISO", "valid_until": "ISO",
 "status": {"prices_state": "ok|not_yet|stale|failed|no_keys|keys_refused|all_used_up",
            "prices_at": "ISO|null", "next_price_read_at": "ISO|null",
            "injuries_at": "ISO|null", "stats_through": "2026-W05", "stats_state": "ok|behind|failed",
            "last_run": {"ok": true, "at": "ISO", "failed_step": null},
            "pool": {"credits_left": 1029, "credits_left_shared": 1350, "keys": 7, "keys_shared": 4,
                     "games_left": 30, "allowance": 34.3, "low": false, "text": null}},
 "banners": [{"level": "warn|info|error", "text": "plain sentence"}],
 "empty": {"code": "...", "text": "...", "sub": "..."} | null,
 "games": [{"id": "2026_06_SEA_DEN", "away": "SEA", "home": "DEN", "start": "ISO", "day": "Thu",
            "state": "early|injury_report|inactives|locked|final", "started": false,
            "lines": {"spread": {"home": -2.5, "price_home": -125, "price_away": 105, "book": "hardrock"},
                      "total": {"line": 41.5, "over": -105, "under": -115, "book": "hardrock"},
                      "ml": {"home": -160, "away": 135},
                      "h1_spread": {"home": -0.5, "price_home": -118, "price_away": -102, "book": "draftkings"},
                      "h1_total": {...} | null, "at": "ISO"},
            "weather": {"roof": "outdoors|dome|closed|retractable|open", "wind_mph": 9, "precip_mm": 0, "text": "..."} ,
            "picks": 2, "note": null}],
 "picks": [ PICK ... ],        // open + pulled-this-slate + locked, engine order (best first); page splits by state
 "parlays": [ PARLAY ... ]
}
```
PICK:
```json
{"id": "2026-W06:2026_06_SEA_DEN:00-0039337:rec_yds:fg:54.5:over", "kind": "single",
 "game_id": "...", "start": "ISO", "team": "SEA", "opp": "DEN", "home": false,
 "player_id": "00-0039337", "player": "J. Smith-Njigba", "pos": "WR",
 "market": "rec_yds", "period": "fg|h1", "line": 54.5, "side": "over|under|home|away",
 "bet": "Over 54.5 receiving yards", "price": -115, "book": "hardrock|draftkings", "price_at": "ISO",
 "p": 0.61, "p_pct": 61, "breakeven": 0.535, "breakeven_pct": 53, "fair": 0.52, "model": 0.66,
 "reason": "plain one-liner", "state": "early|injury_report|inactives", "rank": 1,
 "status": "open|pulled|locked|graded", "pulled": {"at": "ISO", "reason": "plain"} | null,
 "result": null | {"r": "W|L|PUSH|VOID", "actual": 61, "text": "had 61", "units": 0.87}}
```
PARLAY: `{"id", "kind": "parlay", "legs": [pick ids], "period", "price": +264 (product of leg prices, "about"),
"p", "breakeven", "status", "pulled", "result"}`.

### data/research/index.json + data/research/<game_id>.json
UX decides the screens; Builder + DS fill the data. Minimum per game: lines (as slate), injuries for both teams
(name, pos, status, practice note), weather, and per player who has any HR line or meaningful role: id, name, pos,
team, status, HR lines by market/period (line + both prices), model mu and p_over at the HR line, last 8 games of
each relevant stat FG and 1H (week, opp, value), season average, hit count vs the current line, opponent allowed rank
for that stat (1 = allows most). `index.json` lists games and players (id, name, team, pos, game_id) for search.
Keep each game file under ~150 KB.

### data/record.json  (port NHL record.py views; units flat 1, graded at the worst price shown; weeks not nights)
`{"generated_at", "summary": {"headline": {...}, "rows": [by market+period, by settledness, parlays]}, "weeks": [{"slate_id", "graded", "w", "l", "units", "entries": [...]}]}`.
Record event log `data/record/events.jsonl` exactly like NHL (`shown`, `price`, `update`, `state`, `pulled`,
`locked`, `graded`), guarded by `guard_record.py` in every cycle. Only the timer writes `data/record/`.

### data/status.json
Like NHL: finished_at, steps (name, ok, ms, note), sources reached, keys (position, left, status, shared), runner
chain info, unmatched names, excluded players/teams.

## 9. Timer
Port NHL's chain exactly (`run.yml`, `loop.sh`, `cycle.sh`, `dispatch_next.sh`, `fail_status.py`, record guard).
Cadence: every 10 min 8 AM-1 AM ET on game days (Thu/Sun/Mon, Sat late season) and from 90 min before any kickoff;
every 30 min otherwise. Commits only when data changed (heartbeat 20 min). Off switch `config/stop_timer`.
Pages: main branch root, already enabled, live at https://msasportas.github.io/prop-check-nfl/ (needs `.nojekyll`).

## 10. Working rules for the team
- Never commit or push. The CEO commits and pushes. Never touch `/home/claude/ref-nhl` or MSasportas/prop-check-nhl.
- Never write a key anywhere. Tests use fixtures (`PC_ODDS_FIXTURE_DIR` etc.), never live data/.
- Plain words on the page: Matt is a casual bettor on a phone.
