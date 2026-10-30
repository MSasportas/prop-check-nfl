# Prop Check NFL: spec (Gap + Guard, Matt's brief 2026-10-30)

How the pieces fit, and the exact shape of the files the page reads. TEAM.md is the operating doc; config/rules.json is
the only place a betting value lives. Python 3.12 standard library only. Eastern time everywhere.

## 1. Customer rules (fixed)
Hard Rock Florida (`hardrockbet_fl`) only; the national `hardrockbet` key is never an "other" book. No leg worse than -150,
plus money fine (ceiling +120, a config value). NO parlays. NFL player props only: full game, MAIN lines only (no 1st half,
no alternate lines, no sides, totals, game or team props). Picks are for betting AFTER the injury reports: no read taken
before the final injury report counts in may create a pick, and there is no "Early" level. Regular season and playoffs.
The Record is fixed: append-only and byte-exact; every pick exactly as shown, graded at the posted price, pulled picks kept
and marked pulled. The page has no Research tab.

## 2. The method: Gap + Guard
All numbers from `config/rules.json` (`ops.gap_guard.thresholds`). EV = fair probability x Hard Rock decimal payout - 1.
1. **Gap.** Hard Rock's EV against the other books' median proportional no-vig fair must be >= +3.0%. At least 4 other books
   must quote both sides of the same player, line and side (hold 0..15%, not stale). Pinnacle must be one of them and its own
   fair EV on Hard Rock's price must be >= 0. Whole-number lines are skipped. Price must be inside -150..+120.
2. **Guard.** final = 0.8 x market fair + 0.2 x model chance (model side chance = model p minus the shrink for that
   market and side, minus the jump penalty on role-jump overs). Pass iff final EV >= +2.0%. The model can only veto.
   No model means no pick.
3. **Timing.** Only R and C reads taken at or after the final injury report counts in can flag (`gg.can_flag`). A new pick
   comes from the newest usable read (not older than 120 min). The price is frozen at the first passing read. Later reads can
   only PULL (gap gone, line moved, line or market gone, price out of the window, player status, wind, rules).
4. **Settled level** shown on every pick: `injury_report` ("Injury report in") or `inactives` ("Inactives confirmed").
   `flags.price_final` is true once the pre-kickoff C read has been taken with inactives in.
5. **Pre-price vetoes** (engine.pre_check): market not on the list, no model, questionable-or-worse this week, missed last
   game, starting QB out (no overs), eligibility / role unstable, room change, wind (overs on pass_yds, pass_td, rec_yds),
   key teammate out (blocks that room until inactives are confirmed). One pick per player.
6. **Safety pause.** New-era graded singles (not pulled) down 10 units or more: no NEW picks (shown ones are still looked
   after) and a banner. Retired-era results never count.
7. Sharp's markets (config `ops.gap_guard.markets`, add or drop there): `pass_yds`, `pass_td`, `rush_yds`, `rush_att`,
   `rec`, `rec_yds` = Odds API `player_pass_yds`, `player_pass_tds`, `player_rush_yds`, `player_rush_attempts`,
   `player_receptions`, `player_reception_yds`.

## 3. Odds pulls and credits
One the-odds-api call per game per pull, six markets, Hard Rock + 9 other books (`ops.consensus_books`, 10 books max =
one billed region), about 6 credits a game. **R**: first read once the final injury report counts in (retry every 240 min
while Hard Rock has not posted, 3 reads max). **C**: kickoff - 70 min once both teams' inactives are confirmed, else
kickoff - 45 (never later than 20 min before kickoff). Never a game that has kicked off. Tiers by allowance (credits per
game left before the monthly reset): `full` >= 12 (R + C everywhere), `tight` >= 7 (R everywhere, C only where a pick or
near-pick sits), below = R only. The plan is capped by `ops.monthly_cap` (1,000 credits a month) and shared keys count at
0.5. Keys come from env `ODDS_API_KEYS` and `ODDS_API_KEYS_2` (comma-separated), are never printed or written, and the
ledger (`data/state/credits.json`) is by position. Every read is saved in `data/odds/<date>.json` (our price history).
The credit count comes from the `x-requests-remaining` / `x-requests-last` headers.

## 4. Files
```
config/rules.json              the standard (page reads the generated data/rules.json copy)
scripts/  common teams names sched stats model injuries weather odds gg engine record guard_record grade emit run
data/slate.json  rules.json  status.json  record.json  record/events.jsonl  record/<slate>.json
data/odds/<date>.json          every price read, others included
data/gg/events.jsonl           research log (vetoes, capped, closing prices); append-only, guarded; not the Record
data/state/*                   schedule, stats, injuries, rosters, credits ledger, book probe
```

## 5. data/slate.json (schema 2)
Rewritten every run. Top level:
```
schema 2, era "gg1", season 2026, week 8, label "Week 8",
generated_at, status_at, valid_until (ISO, next Tuesday 6 AM ET rollover), next_check (ISO|null),
status { odds, injuries, inactives, stats, pool, last_run },
banners [ {level "warn"|"stop", text} ],
empty  null | {code, text, sub},
games  [ GAME ],      // schedule order
picks  [ PICK ]       // open (ranked), locked, pulled; no parlays key, no research key
```
- `status.odds` {state, at, next_at}: state is `ok`, `limited` (some games skipped for credits), `not_yet`, `feed_down`,
  `pool_empty`, `keys_refused`. `at` = newest price read, `next_at` = next planned read.
- `status.injuries` {state `ok`|`not_yet`|`down`, at}. `status.inactives` {state `ok`|`not_yet`|`down`, at, note}.
  `status.stats` {state `ok`|`behind`, through_week, at}.
- `status.pool` {credits_left, keys, keys_live, keys_shared, days_left, low, empty}. `status.last_run` {ok, at, failed_step}.
- `empty.code`: `off_week`, `all_started`, `no_picks_yet`. Null whenever at least one open pick exists.
- A failed run marks `last_run.ok=false` and puts a `stop` banner first; the old data stays.

GAME:
```
{id, away, home, kickoff, day "YYYY-MM-DD", roof, started, final, score {away, home}|null,
 settled "early"|"injury_report"|"inactives" (the game-level level, both teams),
 props_state "ok"|"none_yet"|"not_pulled"|"closed", props_at ISO|null, note string|null, picks N}
```
PICK (one row per bet, no legs):
```
{id, era "gg1", rank int|null (open picks only, best first within the game day), state "open"|"locked"|"pulled",
 game_id, day, kickoff, game "SEA @ DEN", player, player_short, player_id, pos, team, opp, home,
 market (our key), market_label ("Carries"), side "over"|"under", line (always x.5), bet "Under 17.5 carries",
 book "Hard Rock", price (American int, FROZEN at first passing read), read_at, read_pull "R"|"C",
 vs "+3.9% vs 7 books (Pinnacle in)", gap_pct, n_books, pinnacle true, final_ev_pct,
 reason (one plain sentence), settled "injury_report"|"inactives", settled_text,
 flags {price_final bool, weather {state "checked"|"dome"|"not_checked", text, wind_mph}},
 pulled null | {at, reason, code, if_bet, now_price},     // code: inactive, injury, rules, wind, line_moved, line_gone,
                                                           // market_gone, gap_gone, price, ...; if_bet = plain text for what
                                                           // a bet placed at the posted price would mean now
 graded null | {result "W"|"L"|"PUSH"|"VOID", actual, units},
 history [ {at, text} ]}                                   // Posted at +100 / Inactives confirmed / Pulled / Locked
```
The page splits by `state`: open picks by day then `rank`; locked and pulled below. Picks are ranked by final EV, then
settled level.

## 6. data/record.json
```
{generated_at, season, era "gg1",
 summary {headline {w,l,push,void,n,units,hit_rate,needed,parlays{w,l,units}}, rows [ {key,label,w,l,push,void,n,units,hit_rate,needed} ]},
 weeks [ {week, slate, label, graded, w, l, units, entries [ENTRY]} ]   // newest first, last 4 weeks
 older ["record/2026-W04.json"], 
 retired null | {label, note, summary, weeks}}
ENTRY {id, kind "single", era, date, game, player, team, market, period "FG", bet, price, result, actual, units, pulled null|{at,reason,...}, legs []}
```
Headline, rows and weeks are the current era (gg1) only. Everything shown under the first rules (Oct 18-25: blended chance,
parlays, 1st half) is a labelled `retired` block with its own summary; it never counts toward the headline or the pause.
Rows are per market (`pulled` row separate). `parlays` in the headline is always zero now (kept for old readers).

## 7. data/rules.json
`{version, updated, values, gap_guard (thresholds), text [plain-language rules for the Rules screen], ui}`, generated from
config/rules.json by run.py. The text lists what is in force (price window, markets, gap, Pinnacle, guard, timing, vetoes).

## 8. Research log (not shown on the page)
`data/gg/events.jsonl`: `veto` (step 1 passed, the guard said no), `capped` (passed but one-per-player), `close`
(closing Hard Rock and market numbers, for CLV). Append-only, checked by `guard_record.check`, never under `data/record/`.

## 9. Running it
`python scripts/run.py` (one cycle), `--explain` (every candidate with its verdict), `--dry-run` (copy of data, nothing
written back), `--rebuild-record`. Tests: `python -m unittest discover -s tests`. Guards: `python scripts/guard_record.py`
and `--history`. The timer is off while `config/stop_timer` exists.
