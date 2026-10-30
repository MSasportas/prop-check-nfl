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
3. **Injury gate.** No NEW pick unless the injury feed's last good read is at most `ops.injury_fresh_min` (120) minutes old and has not failed since; the page shows "Injury news isn't loading. No new picks until it does.". Shown picks are still looked after.
4. **Timing.** Only R and C reads taken at or after the final injury report counts in can flag (`gg.can_flag`). A new pick
   comes from the newest usable read (not older than 120 min). The price is frozen at the first passing read. Later reads can
   only PULL (gap gone, line moved, line or market gone, price out of the window, player status, wind, rules).
5. **Settled level** shown on every pick: `injury_report` ("Injury report in") or `inactives` ("Inactives confirmed").
   `flags.price_final` is true once the pre-kickoff C read has been taken with inactives in.
6. **Pre-price vetoes** (engine.pre_check): market not on the list, no model, questionable-or-worse this week, missed last
   game, starting QB out (no overs), eligibility / role unstable, room change, wind (overs on pass_yds, pass_td, rec_yds),
   key teammate out (blocks that room until inactives are confirmed). One pick per player.
7. **Safety pause.** New-era graded singles (not pulled) down 10 units or more: no NEW picks (shown ones are still looked
   after) and a banner. Retired-era results never count.
8. Sharp's markets (config `ops.gap_guard.markets`, add or drop there): `pass_yds`, `pass_td`, `rush_yds`, `rush_att`,
   `rec`, `rec_yds` = Odds API `player_pass_yds`, `player_pass_tds`, `player_rush_yds`, `player_rush_attempts`,
   `player_receptions`, `player_reception_yds`.

## 3. Odds pulls and credits
One the-odds-api call per game per pull, six markets, Hard Rock + 9 other books (`ops.consensus_books`, 10 books max =
one billed region), about 6 credits a game. **R**: first read once the final injury report counts in (retry every 240 min
while Hard Rock has not posted, 3 reads max). **C**: kickoff - 70 min once both teams' inactives are confirmed, else
kickoff - 45 (never later than 20 min before kickoff). Never a game that has kicked off. Tiers by allowance (credits per
unstarted game in the next 7 days, `ops.plan_window_days`): `full` >= 12 (R + C everywhere), `tight` >= 7 (R everywhere, C only where a pick or
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
schema 2, era "gg1", methods {gap_guard {era "gg1", live true}, pin_solo {era "ps1", live false}},   // see section 11
season 2026, week 8, label "Week 8",
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
{id, era "gg1"|"ps1", method "gap_guard"|"pin_solo", rank int|null (open picks only, best first within the game day), state "open"|"locked"|"pulled",
 game_id, day, kickoff, game "SEA @ DEN", player, player_short, player_id, pos, team, opp, home,
 market (our key), market_label ("Carries"), side "over"|"under", line (always x.5), bet "Under 17.5 carries",
 book "Hard Rock", price (American int, FROZEN at first passing read), read_at, read_pull "R"|"C",
 vs "+3.9% vs 7 books (Pinnacle in)", gap_pct, n_books, pinnacle true, final_ev_pct,   // pin_solo: gap_pct and final_ev_pct are null
 pin_solo null | {window "R→C", pin_move_pts, others_move_pts, n_others, fair_price, ev_vs_fair_pct},   // only on method pin_solo
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
 summary {headline {w,l,push,void,n,units,hit_rate,needed}, rows [ {key,label,w,l,push,void,n,units,hit_rate,needed} ]},
 weeks [ {week, slate, label, graded, w, l, units, entries [ENTRY]} ]   // newest first, last 4 weeks
 older ["record/2026-W04.json"], 
 retired null | {label, note, summary, weeks}}
ENTRY {id, kind "single", era, state "locked"|"graded"|"pulled", date, game, player, team, market, period "FG", bet, price, result, actual, units, pulled null|{at,reason,...}, legs []}
```
`record.json` also has `pin_solo` (null | {era "ps1", label "Pinnacle moves alone", summary, weeks}) once the fallback switch has ever
been on (section 11); each ENTRY carries `method`. Headline, rows and weeks are the current era (gg1) only; ps1 is never mixed in. Everything shown under the first rules (Oct 18-25: blended chance,
parlays, 1st half) is a labelled `retired` block with its own summary; it never counts toward the headline or the pause.
Rows are per market (`pulled` row separate). There is no `parlays` in the headline. Each ENTRY has `state`: `locked` (game started, not graded yet, not counted), `graded`, or `pulled` (never counted).

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

## 10. Behind the scenes: data/watch/summary.json (research only, for a Backstage tab)
Two research tracks, ported from Prop Check NBA, run beside the live rule and **never** make a pick on the Picks tab,
never enter the Record or era gg1, never move a live value. They write only under `data/watch/`:
`events.jsonl` (append-only, guarded like `data/gg/events.jsonl`) and `summary.json` (rebuilt from the log each run).
Config: `ops.watch` (`tracks.pin_solo`, `tracks.pin_judge`, W-read settings). Code: `scripts/watch.py`.

- **pin_solo, "Pinnacle moves alone".** Between two consecutive reads of a game (R, W or C, taken after the final injury report,
  before kickoff): Hard Rock quotes the same main line on both reads; Pinnacle quotes it on both and its de-vigged chance moves
  >= 2 pts toward a side; the median move toward that side of the other books (same line, both reads, >= 4 books, hold 0-15%,
  never the national `hardrockbet`) is under 2 pts; and Hard Rock's price on that side is at or better than Pinnacle's NEW fair
  price (Pinnacle's chance x Hard Rock payout - 1 >= 0). Candidate = that side at Hard Rock's price on the later read, inside
  the live window (-150..+120), the six live markets, the live eligibility vetoes (`engine.pre_check`). Flagged only from the
  newest read of an unstarted game, read at most 60 min old, first flag per player / market / game.
- **pin_judge, "Pinnacle as the price judge".** The newest read of an unstarted game: Hard Rock's price has EV >= +2% against
  Pinnacle's de-vigged fair on the same line, price in the window, eligible. First flag per player / market / game.
- Both are graded by the Record's own grading code (`grade.grade`, nflverse then ESPN box score) at the price frozen when
  flagged, flat 1 unit, push and void = 0 units. Closing value: the flag's price against Pinnacle's fair on the same line on
  the first C read after the flag's own read (a flag made on the C read has none).
- **W read** (`pull: "W"`, ops.watch): an extra price read, same cost as R or C (6 credits: six markets, Hard Rock + 9 books),
  ~180 min before kickoff (never later than 100), one per game (`w_reads`), only after R, only in the full tier, and only
  with credits to spare after reserving R and C for every unstarted game in the plan window and before the keys reset
  (`w_margin` 12 kept back; `w_pace` shares the spare over the weeks left in the month). W never makes a live pick
  (`gg.T.flag_pulls` stays R and C). A W that cannot be paid or fails is simply not made and never marks the odds step failed.

summary.json (schema 1; `updated_at` and the numbers change, the shape does not):
```
{schema 1, updated_at, note,
 reads   {games_with_pairs, pairs, by_window [ {window "R→C"|"R→W"|"W→C", pairs, avg_gap_hours, avg_pin_lines,
                                                avg_moves} ]},      // how far apart the reads are, how many Pinnacle lines, how many moved
 credits {w_reads, w_credits, month},                              // W reads this month, from the odds ledger
 tracks  [ { track "pin_solo"|"pin_judge", label, about, from_slate, active,
             total   {flags, w, l, push, void, pending, units, hit_rate, avg_break_even, edge_pts,
                      clv {n, avg, positive}},                     // hit_rate and avg_break_even are fractions; edge_pts = hit_rate - avg_break_even
             by_market [ {market, label, flags, w, l, push, void, units, hit_rate} ],
             seen    {reads_scanned, candidates, distinct_sides, would_fire,
                      by_gate [ {gate, label, n} ]},               // every Pinnacle move (solo) / side at +2% (judge) and the gate that stopped it
             weeks   [ {slate, label, flags, w, l, push, void, pending, units, bets [BET]} ] } ] }   // newest first; bets only for the newest ops.watch.summary_weeks
BET {id, slate, game_id, game "SEA @ DEN", start, player, team, opp, home, market, market_label, side, line, bet, price (frozen),
     read "R"|"W"|"C", read_at, prev_read, prev_at, window, flagged_at,
     solo only: pin_before, pin_after, others_move, n_others, ev_vs_pin;  judge only: pin_fair, pin_ev,
     result "W"|"L"|"PUSH"|"VOID"|null, actual "had 14"|null, units|null, close_fair|null, clv|null}
```
Gates (`by_gate[].gate`): `hr_line` Hard Rock did not quote the line on both reads; `hr_feed` Hard Rock price failed the feed
check; `books` fewer than 4 other books; `others_moved` the other books moved too; `worse_than_fair` Hard Rock worse than
Pinnacle's fair; `price_window`; `whole_line` (off by default); `ineligible` a live veto; `late` read too old to flag;
`superseded` a newer read existed; `already_flagged`. A row with no gate is a flag (`would_fire`).
Event types in events.jsonl: `pair`/`scan` (a pair of reads / one read was scanned), `cand`, `flag`, `close`, `graded`.

## 11. The fallback switch: "Pinnacle moves alone" as a live method (OFF by default)
`config/rules.json` `ops.pin_solo.live` (false). Flipped to true only when Matt says so; nothing else turns it on. It also needs
the watch track `pin_solo` active (`ops.watch.tracks.pin_solo`). While false: no pin_solo pick is ever made, `record.json.pin_solo`
is null and `slate.methods.pin_solo.live` is false. The page can ignore all of it.

When true, the candidates the watch track flags (section 10: same gates, same -150..+120 window, same eligibility vetoes, reads
after the final injury report, newest pair of usable reads with the newer one under `ops.watch.tracks.pin_solo.max_age_min`
old) become real picks beside Gap + Guard:
- Same path as any pick: shown with the price frozen at the first passing read, same lock at kickoff, same grade at the posted
  price. Pulled for injury status, inactive, wind, line moved or gone, market gone, or Hard Rock's price leaving the window (`code`
  `gate` / `max_price`). Pinnacle drifting back is NOT a pull (the move is not re-judged).
- One pick per player per week across BOTH methods (a Gap + Guard pick for him, even a pulled one, blocks a pin_solo pick, and the
  other way round); the same per-game and per-team caps apply.
- Pick id = the Gap + Guard id plus `:ps`. Pick `method` is `"pin_solo"`, `era` `"ps1"`, `gap_pct` and `final_ev_pct` are null,
  `vs` = "Pinnacle +3 pts, other books +0.9", `reason` = "Pinnacle moved 3 pts toward Under 17.5 carries; the other books moved
  0.9; Hard Rock is still at +105, better than Pinnacle's fair +102.", and the `pin_solo` object (above) holds the numbers.
  `rank` is shared with Gap + Guard picks in the same list (final EV for Gap + Guard, EV against Pinnacle's fair for pin_solo).
- Record: events carry `era "ps1"`, `method "pin_solo"`. `record.json.pin_solo` = {era, label, summary, weeks} with its own
  headline (same shape as section 6); the gg1 headline, rows and weeks never include ps1.
- Own safety pause: ps1 down `ops.pin_solo.safety_pause_units` (-10) stops new pin_solo picks only (banner
  `safety_pause_text`); Gap + Guard's pause counts gg1 only.
- Page: unknown fields are ignored; a pin_solo pick renders from `vs` and `reason` (fixtures/pinsolo checked in a real browser at
  390px, no change needed). To label the method later, read `pick.method` and `slate.methods`.
