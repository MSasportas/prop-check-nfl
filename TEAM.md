# TEAM.md: Prop Check NFL operating doc

Read this first in every session. Update it last.

**Live page: https://msasportas.github.io/prop-check-nfl/** · Repo: MSasportas/prop-check-nfl (public)

## 1. The product
Every NFL game day: every player-prop leg that qualifies at a real Hard Rock (`hardrockbet_fl`) price, ranked best first,
each with the price, how it beat the market and a one-line reason. Grouped by game day. Each pick says how settled it is
(Injury report in / Inactives confirmed). Pulled picks stay visible with the reason and what a bet at the posted price
means now. Record: every pick as shown, graded. No parlays, no Research tab, no Early picks. Dark, phone first (~380px).

**Customer facts (fixed), Matt 2026-10-30:** Hard Rock Florida only (a pick needs a real Hard Rock number); **no single
leg worse than -150** (plus money fine, +120 ceiling is a config value); **NO PARLAYS**; **NFL player props only: full
game, main lines only** (no 1H, no alt lines, no sides, totals, game or team props); Sharp chooses and can add or drop prop
markets from evidence, Hard Rock must offer the market; **picks are for betting after the injury reports are in**;
**regular season AND playoffs**; **method = Prop Check NHL's Gap + Guard** (section 4); **Research tab dropped**; hit-based.
**Option kept behind the scenes (Matt, 2026-10-30):** the NBA project's "Pinnacle moves alone" strategy (and "Pinnacle as the price judge")
runs as a research watch track beside the live rule, in case Gap + Guard finds no 3% edge. It never makes a pick on the Picks tab, never
enters the Record or era gg1, and promoting it to a live rule needs Matt (section 4, "Behind the scenes"). The fallback switch is prebuilt but
OFF: `config/rules.json` `ops.pin_solo.live` (false); Matt says go, then it is set to true (picks tagged `pin_solo`, era `ps1`, own Record line).
**Fixed:** the Record. Every pick exactly as shown, graded after the game at the posted price, append-only, pulled picks
kept and marked. Nothing edited or dropped. It is the only reporting Matt wants. The first-rules history (Oct 18-25, blended
chance, parlays, 1st half) stays in the log as a labelled "retired" block and never counts toward the new headline.
**Come to Matt only for:** more odds keys, paying for something, a GitHub setting we can't reach. Exact taps, one short message.

## 2. How we are judged
1. Picks hit, at the price posted. Fewer picks beats picks that miss.
2. Always current; anything missing or stale is said plainly on the page.
3. A usable set of picks every game week.
4. Readable on a phone in seconds.

## 3. The team (subagents run by the CEO; any chat in the project is the CEO)
Sharp (betting standard, final call on rules) · Data Scientist (data, model, samples, judging the Record) · UX
(page) · Builder (all code) · QA (tests every change before it goes live, real browser at 380px, fresh eyes each
round). Rule changes: the DS names the sample first; the Sharp decides; log it in section 8.

## 4. Current rules (v2.0 Gap + Guard, 2026-10-30). Source of truth: `config/rules.json`; shape of the files in docs/SPEC.md
| Area | Rule |
|---|---|
| Markets | Full game, main line: passing yds, passing TDs, rushing yds, carries, receptions, receiving yds (over and under). Odds API keys `player_pass_yds`, `player_pass_tds`, `player_rush_yds`, `player_rush_attempts`, `player_receptions`, `player_reception_yds`. Sharp adds or drops markets in `ops.gap_guard.markets` |
| Price window | -150 to +120. Whole-number lines skipped |
| Step 1 gap | Hard Rock EV vs the median proportional no-vig fair of the other books >= +3.0%; at least 4 other books with both sides on the same player, line and side; Pinnacle one of them and its own fair EV on Hard Rock's price >= 0. Other books: Pinnacle, DraftKings, FanDuel, BetMGM, BetOnline, BetRivers, ESPN Bet, Bovada, Fliff (+ BetParx reserve, cut at 10 books). The national `hardrockbet` key is never an other book. A book's market older than 120 min at read time is dropped; hold must be 0..15% |
| Step 2 guard | final = 0.8 x market fair + 0.2 x model chance (model p minus shrink 0.03-0.07, minus 0.03 on role-jump overs); pass iff final EV >= +2.0%. The model can only veto. No model = no pick |
| Timing | Only R and C reads taken at or after the final injury report counts in (final report + 60 min grace) create a pick; from the newest usable read (<= 120 min old). Price frozen at the first passing read. Later reads can only pull. No Early level |
| Vetoes | Questionable or worse this week; missed his last game; eligibility (games, new team, QB change); role swings (both sides); room change; starting QB out (no overs); key teammate out (room blocked until inactives); wind 15+ mph (overs on pass yds, pass TDs, rec yds); stats missing his team's last game |
| Caps | 1 pick per player. No team or game cap (config values, off) |
| Pulls | Player ruled out or inactive; listed questionable or worse; Hard Rock moved or removed the line or market; price outside the window; gap gone on a newer R or C read (pull EV 1.5%); rules tightened. Pulled text carries "if you already bet it at the posted price" and what to check (open the slip; we make no claim about how Hard Rock settles a player who does not play) |
| Settled | `injury_report` or `inactives`. Inactives confirmed = ESPN lists 3+ inactives for both teams. Not confirmed by 60 min before kickoff: locks as "Locked: inactives not confirmed" |
| Lock and grading | Locks at kickoff. Full game incl. OT. Did not play = void; push on a whole number (never picked). Graded from nflverse play-by-play, ESPN box score if nflverse is 36h+ late. Posted price = the frozen Hard Rock price. 1 unit flat. Pulled picks are graded, shown apart, never in the hit rate |
| Odds pulls | R after the final injury report (~6 credits a game: six markets, 10 books, one region); C at kickoff - 70 min once inactives are confirmed, else kickoff - 45. Tiers by credits per game in the NEXT 7 DAYS (unstarted games, `ops.plan_window_days`; not the whole month): full >= 12, tight >= 7 (C only for games holding a pick or near-pick), below = R only. Monthly cap 1,000 credits; shared keys at 0.5. Keys: `ODDS_API_KEYS` + `ODDS_API_KEYS_2` |
| Safety pause | New-era graded singles (not pulled) at -10 units: no NEW picks until a person reviews, banner on the page. Retired-era results never count |
| Research log | `data/gg/events.jsonl` (vetoes, capped, closing prices for CLV). Not on the page, not the Record, append-only and guarded |
| Behind the scenes | **Research watch (never picks, never the Record).** `scripts/watch.py`, `ops.watch`, output `data/watch/` (events.jsonl append-only, summary.json; shape in docs/SPEC.md section 10). Track `pin_solo` ("Pinnacle moves alone", NBA's fallback): between two reads of a game, Pinnacle's de-vigged chance moves 2+ pts toward a side while the other books' median (4+ books, same line, hold 0-15%) moves under 2 pts, and Hard Rock's price on that side is at or better than Pinnacle's new fair price; window -150..+120, same six markets, same eligibility vetoes as the live engine, reads after the final injury report only. Track `pin_judge`: Hard Rock EV >= +2% vs Pinnacle's fair on the same line (newest read, first flag per player/market/game). Both graded by the Record's code at the frozen price, 1 unit; summary has W-L-P, units, hit rate vs average break-even, CLV vs Pinnacle's closing fair, by market, and how many candidate moves were seen with the gate that stopped each. Optional **W read** (watch-only, ~6 credits/game, ~180 min before kickoff, one per game, full tier only, only from credits left after reserving R and C for every unstarted game in the window and before the reset, paced over the weeks left in the month). Promotion to live needs Matt. **Fallback switch (Matt, 2026-10-30), OFF by default:** `ops.pin_solo.live`. When true, the pin_solo candidates (same gates, window, vetoes, timing) become real picks via the normal path (price frozen at first passing read, same pulls, lock, grade), tagged `method: pin_solo`, era `ps1`, id suffix `:ps`; one pick per player across both methods; its own Record headline (`record.json.pin_solo`) and its own -10 unit pause; never mixed into gg1. Shape in docs/SPEC.md section 11 |

Retired v1.1 (Oct 13-25: blended chance, parlays, 1st half, M/F/D pulls) is kept in section 8 and in the Record's retired block.

## 5. How it runs
- `scripts/run.py` is the only entry point; each run decides what's due: rules, schedule (nflverse games.csv +
  ESPN scoreboard), stats (nflverse play-by-play -> `data/state/rows<season>.json.gz`), roster, injuries (ESPN,
  nflverse fallback), weather (open-meteo), odds (pulls R/C), engine (lock / pull shown picks, add new
  ones from gg.py -> Record), grade, slate, record, status. Each step may fail without stopping the others.
  Stdlib Python 3.12.
- **Timer:** `.github/workflows/run.yml` is a self-dispatching chain (cron is unreliable): each run loops
  `scripts/loop.sh` ~50 min (`scripts/cadence.py`: every 10 min on game days 8 AM-1 AM and around kickoffs, 30 min
  other days, hourly overnight), then starts the next run by workflow_dispatch. Hourly cron (:23) and any push to
  scripts/, config/ or the workflow (re)start it. Commits only when data changed. Off switch: commit
  `config/stop_timer`.
- **Is it alive?** `git pull` and read `data/status.json`: `finished_at` < 30 min old on game days; `steps`;
  `keys` (credits per key, never the key); `live_page` (hourly check from the runner: http 200 and the page title).
  Chats can't open github.io or the Actions API through the sandbox proxy; status.json is the window.
- **Restart a dead chain:** push any commit touching `scripts/`, `config/` or `.github/workflows/run.yml`.
- **Record guard:** `scripts/guard_record.py --history` runs every cycle: each committed version of
  `data/record/events.jsonl` must be a byte-exact prefix of the next, or the cycle pushes nothing and the page says
  "Updates have stopped". Only the timer writes `data/record/`. No chat ever edits it. `python scripts/run.py
  --rebuild-record` regenerates the derived views.
- **Odds keys:** GitHub secrets `ODDS_API_KEYS` and `ODDS_API_KEYS_2` (comma-separated). Never in the repo, page or
  logs; passed by name only (never `toJSON(secrets)`: GitHub holds the run). The ledger trusts
  `x-requests-remaining` every call; a key spent by another tool (Prop Check NHL) is marked shared and counted at a
  discount. Page warns when the pool is low.
- **Discord notifications (2026-11-01, copied from NHL/NBA):** `scripts/notify.py`, called at the end of `run.py` (never fails or slows a
  run: 5 s timeout, one retry, whole run capped at 14 s). Webhook = GitHub secret `DISCORD_WEBHOOK` (read from env `DISCORD_WEBHOOK_URL`,
  passed to the loop step in `run.yml`); env only, scrubbed from stdout/files, never in the repo or state; unset or empty = does nothing.
  Sends only: NEW PICKS (one batched message per cycle, 8 blocks then "+N more": bold bet line, game, kickoff, Injury report in /
  Inactives confirmed, "+X% vs N books, we say Y%, price needs Z%", link), PULLED (only picks already told, before kickoff, with the
  `if_bet` line), PRICE FINAL (once per told pick after the C read; a pick first shown at that read says "Price final" in its NEW block),
  ACTION NEEDED (keys refused / pool low or empty: the page's own sentence, once per ET day), a one-time hello. Never no-pick days,
  heartbeats, injury checks, grading, errors, vetoes. Quiet 12:30-8:00 AM ET (held items go out at 8 if still ahead). Max 12 messages per ET
  day (PRICE FINAL stops at 9). State `data/state/notified.json` (ids and timestamps only, kept 8 days so a Friday pick is not told again on
  Monday night; marked only after a 2xx; 429/5xx = retry next cycle). Tests: `tests/test_notify.py` (20, local fake server only; in
  `PC_NOW`/`PC_DATA_DIR` mode nothing reaches Discord without `PC_DISCORD_FIXTURE_URL`). Secret to add: repo Settings > Secrets and
  variables > Actions > New repository secret, name `DISCORD_WEBHOOK`, value = the Discord channel webhook URL. Secrets load only when a
  run starts, so after adding it push any commit touching `scripts/` or `config/` (or wait for the next chain link).
- **Files:** `index.html` (page), `data/slate.json`, `data/record.json`, `data/rules.json`, `data/status.json`,
  `data/odds/<date>.json` (every price pulled, others included), `data/gg/events.jsonl` (research log), `data/watch/` (behind-the-scenes tracks), `data/state/`.
  `fixtures/` = page test data, schema 2 (`?fixture=NAME`, built by `fixtures/make_fixtures.py`).
  Tests: `python -m unittest discover -s tests` (164), incl. a full replayed Thursday on a synthetic 7-book consensus. Tests use frozen stat rows
  in `tests/fixtures/rows/`, never the live data.

## 6. Reviews (scheduled tasks; each reads this file first and updates it last)
- **Game-day checks:** Sun 11:47 AM and Mon/Thu 6:47 PM ET (scheduled tasks). Chain alive, pulls ran, failed steps, unmatched
  names, inactives confirming, last games graded. Fix what's broken; no rule changes.
- **Weekly team review:** Tuesdays 10:47 AM ET (scheduled task) (after MNF is graded). Full team on the Record and the odds log:
  volume, hit rate vs break-even by market, closing-price check (did Hard Rock move toward us?), calibration; ship
  agreed fixes through QA; log changes in section 8.

## 7. Known gaps and risks
1. **Odds credits are thin.** About 500 credits across the keys, mostly shared with NHL (weighted 0.5), and the monthly cap is 1,000. The plan divides credits by the games in the next 7 days (about 13 this Sunday week = full tier, R then C). If credits run short the tier drops to tight, then R only, and the page says "Odds credits are low: N left for M games this week". More keys (or ODDS_API_KEYS_2) are Matt's call.
2. **Other books' staleness is untuned.** The book probe did not store `last_update`, so `stale_min_others` is 120 by guess. Tune from the first real reads in `data/odds/`.
3. **The Gap + Guard thresholds are NHL's, untested on NFL prices.** Expect few picks; fewer beats misses. First review after week 8 grades.
4. **No model, no pick:** rookies and thin samples never appear. By design.
5. Hard Rock house rules not verified (what happens to a prop if the player does not play): pulled text only says to check the slip.
6. Inactives depend on ESPN's pre-game feed; if not confirmed 60 min before kickoff the pick locks as "inactives not confirmed".
7. **`values.final_report` offsets are unverified for odd weeks.** Wed (Mon 16:00), Fri (Wed 16:00) and Sat (Thu 16:00) entries are copied from the old build, not checked against a real report. Thanksgiving (Thu Nov 26 games, report likely Tue/Wed), Black Friday Nov 27, Dec 19 (Sat) and Dec 25 (Fri) games are the risk: check the report day by hand the week before and fix the config; no value was changed because none can be verified here.
8. **Weeks 19-22 (playoffs) are untested:** slate numbering, week labels and Saturday/Sunday playoff kickoffs have no test and no real data yet.
9. **Open-Meteo fails open:** if the weather feed is down, the wind veto does not apply and the pick shows weather "not checked". The page shows that flag; nothing blocks the pick.
10. **Injury news gate:** no NEW pick unless the last good injury read is within `ops.injury_fresh_min` (120 min) and the feed has not failed since. Shown picks are still looked after. Page banner: "Injury news isn't loading."
11. Key pool shared with Prop Check NHL; both step down together if it runs low. Keys reset on the 1st.
12. Timer chain holds one runner most of the day (free on public repos; GitHub discourages always-on use).
13. The Record's retired block label is derived from entry dates ("Retired rules (Oct 18-22)"); the brief says Oct 18-25.
14. **Watch tracks have few reads.** Each game has R (Fri/Sat after the injury report), C (about 70 min before kickoff) and at most one W (about 3 hours before): the R to C gap is about 2 days on early games, so Pinnacle's move between reads is mostly news, not a short-term move, and only R→W→C pairs exist. Expect few candidates; summary.json reads.by_window shows the real gaps. A flag made on the C read has no closing line, so its CLV is blank.
15. **W budget is conservative and pre-reset it is zero.** W is made only from credits left after reserving R and C for every unstarted game before the reset and in the 7-day window, paced over the weeks left this month. On a stale pre-reset ledger (about 420 credits, shared keys at 0.5) the reserve for the 71 November games exceeds the pool, so no W is made until the keys are re-read after the reset.
16. The watch tracks use the live eligibility vetoes as they stand NOW when a read is first scanned (a later change of a player's status does not rewrite old rows). Candidate rows are read-level: a side that stays a candidate on R, W and C is counted three times (`distinct_sides` counts it once).
17. **The fallback switch is untested on real NFL reads.** Tested on the synthetic world only (off = nothing; on = pick, blocked by a held Gap + Guard pick, pulled on price, locked and graded on its own ps1 line). The page shows a pin_solo pick from `vs` and `reason` without change (fixture `pinsolo`). Needs two usable reads after the final injury report, so on a Sunday game it only fires when R and W/C both exist (W is zero before the Nov 1 credit reset, section 15 above).
18. **`data/watch/events.jsonl` is append-only at write time** (watch.append runs guard_record.check on the old bytes before every write, like data/gg) but `guard_record.py --history` walks only the Record, not the watch log or its git history.
19. **Discord is built but OFF until Matt adds the `DISCORD_WEBHOOK` secret** (2026-11-01: he could not add it right then). Until then `notify.py` does nothing and no message is lost: picks open at the time are told on the first cycle after the secret loads (the hello first). The webhook URL was shared in chat only; it is not in the repo. If it was ever pasted anywhere public, make a new one in Discord (channel settings > Integrations > Webhooks).

## 8. Rule-change log
| Date | Change | Why | Sample |
|---|---|---|---|
| 2026-10-30 | **Fallback switch built, OFF:** `ops.pin_solo.live` false. When Matt says go it makes "Pinnacle moves alone" picks (era ps1, method pin_solo) beside Gap + Guard. No live value changed | Matt: ready as the fallback on Sunday if Gap + Guard produces nothing; backend only, no new tab | NBA: 16 bets 12-4 +7.12u; no NFL sample |
| 2026-10-30 | **Behind the scenes only:** NBA "Pinnacle moves alone" (`pin_solo`) and "Pinnacle as the price judge" (`pin_judge`) ported as research watch tracks, plus an optional watch-only W read. No live value changed; W cannot flag a live pick | Matt: keep the NBA option as the fallback if Gap + Guard finds no 3% edge | NBA discovery Oct 25-28: pin_solo 16 bets 12-4 +7.12u (v2.3), judge 6 bets 5-1 +3.18u; no NFL sample |
| 2026-10-30 | **v2.0 Gap + Guard** (Matt's brief): price window -150..+120; NO parlays; full game main lines only (six markets); picks only from reads after the final injury report (no Early); market = other books' no-vig fair (Pinnacle required, 4+ books, gap >= 3%, guard 80/20, final EV >= 2%); R and C pulls only, monthly cap 1,000; safety pause at -10u; Research tab dropped; Record gets era gg1 plus a retired block | Customer rules and the NHL method | No NFL sample; thresholds from NHL |
| 2026-10-20 | v1.1 (all tightenings): `too_good` 0.20→0.15 (open picks over it pulled); role-unstable blocks unders too (`elig.role_cv_blocks` both); no new pick on a price >6h old; `parlay_cap` 3→2, `parlay_leg_max_use` 2→1, caps count locked/pulled parlays (bug fix); new `room_change` (8% targets / 25% carries, 2 games) | Week 6: gap>0.15 4-12, role-unstable 1-5, parlays 0-7 (7 on one day), picks posted on 11h-old prices, Brooks/Hutchinson room changes | 28 graded singles, 7 parlays. Tightening allowed any time; no loosening before 100/market, 150 board |
| 2026-10-20 | Page: Record headline shows singles + parlays; filter-aware week totals; pulled entries greyed "not counted"; calm mid-week no-props note; odds warning only on the backend low flag | UX review at 380px | Display only |
| 2026-10-18 | Reason line: context clauses (defense rank, team total) only when they support the side; defense ranks are the plain this-season numbers Research shows | QA: reasons contradicted Research and some argued against the pick | Display only, no pick rule changed |
| 2026-10-18 | Game lines on Research: main pair only (both sides -160..+140, closest to even); otherwise DraftKings, labelled | Hard Rock's 1H total in the feed is an alt line | Display only |
| 2026-10-13 | v1.0 set (section 4) | First build. Sharp standard from the DS backtest and one live Hard Rock pull. Spotlight rules dropped | Backtest only; 0 graded live legs |

## 9. Session log (newest first, one line each)
- 2026-11-01 11:55 AM (Sun game-day check): healthy. Chain alive (11:50 run, link 840+), no failed steps, no unmatched names/unknown teams/behind, page 200 (11:20 check), pool 3,424 on 7 keys (tier full, post-reset), C reads ran 11:50 (5 games), inactives posted for 13 of 16 teams of the 1 PM games, nothing to grade yet (no gg1 picks so far). 0 picks is the market: best Hard Rock gap on today's C/W reads is +0.4% with 4+ books (+4.2% only with 2 books, no Pinnacle). 187 tests pass. No code change.
- 2026-11-01 ~10 AM (CEO, Matt: do what NHL and NBA do, send Discord messages): ported `notify.py` (NEW PICK, PULLED, PRICE FINAL, ACTION, hello) to the NFL slate shape, wired it at the end of `run.py`, `DISCORD_WEBHOOK` added to `run.yml`, `common.scrub` now replaces the longest secret first, 20 tests (187 pass). No rule, pick or Record change. Off until the secret exists (section 7 item 19).
- 2026-10-30 night: Builder prebuilt the Pinnacle-moves-alone fallback switch (`ops.pin_solo.live`, OFF): ps1 picks, own Record line, own pause, one pick per player across methods; SPEC section 11; page fixture `pinsolo` renders unchanged; 164 tests. Do not turn on without Matt.
- 2026-10-30 night (QA fixes): page shows the ps1 line on the Record tab and a method tag on pin_solo cards (fixtures `pinsolo`, `pinsolo_empty`, 380px clean); `skip_whole_lines` true on both watch tracks; ps1 picks count as holding a focus market for the C read; switch needs a literal `true`; pair `gap_min` from UTC times; 167 tests. **To flip: file `config/rules.json`, key `ops.pin_solo.live`, value `true` (false = off). Only when Matt says so.**
- 2026-10-30 evening: Builder added the behind-the-scenes watch tracks (`pin_solo`, `pin_judge`, watch-only W read, `data/watch/summary.json`; SPEC section 10). The watch step is soft (never marks the run failed). Live values untouched. Page tab for it is the UX agent's job.
- 2026-10-30 5:30 PM: LIVE. Page (schema 2, Picks + Record) built and checked at 380px; QA SHIP WITH FIXES, both blockers fixed (injury feed down now blocks new picks; credit plan uses this week's games), 130 tests, guard_record ok. `config/stop_timer` removed (push restarts the chain); three NFL scheduled tasks re-enabled. Sharp's market list (pass yds, pass TDs, rush yds, rush att, receptions, rec yds) reported to Matt. Expect first R reads for Sunday games right after the push (October credits ~500, resets Nov 1). Watch: stale_min_others, final_report offsets for Nov 25/27 and Dec 19/25.
- 2026-10-30 evening: Builder shipped the Gap + Guard backend (gg.py, odds R/C, engine, record eras, slate schema 2, docs/SPEC.md, 122 tests). Timer still PAUSED (config/stop_timer) until the CEO says go. Next: UX builds the page from docs/SPEC.md, QA, then go-live for Sun Nov 1.
- 2026-10-30 4:40 PM: Matt's new brief and answers received (section 1). Rework to NHL-style Gap + Guard started, target live Sun Nov 1. Timer still PAUSED (config/stop_timer) until the rework passes QA. First job: probe which books/markets the feed has for NFL player props, then Sharp reports the market list to Matt before go-live.
- 2026-10-22 10:41 AM: PAUSED by Matt. `config/stop_timer` committed (no more odds pulls or runs); game-day and weekly review scheduled tasks disabled. Resume: delete `config/stop_timer` (push restarts the chain) and re-enable the three scheduled tasks. Page stays up on the last data (credits left 823).
- 2026-10-20 ~12:30 PM: weekly review (week 6). Health clean (10:30 run, link 737, no failed steps, page 200, pool 1,171 tier full, MNF graded). DS/Sharp/UX/Builder/QA: rules v1.1 shipped (section 8), QA SHIP, 100 tests. Expect R. Stevenson's Thursday pick to be pulled "Rules tightened" next cycle; 0 new picks for NE@CHI so far. Gap 15 closed, gap 16 addressed.
- 2026-10-19 6:55 PM: MNF check (WAS@SF). Chain alive (6:50 run, link 376), no failed steps, page 200, pool 1,326 (tier full_plus), D pull 10 AM ran, C pull due 7 PM, inactives not posted yet for WAS/SF (checked each cycle), Sunday graded (singles 10-15, -6.6u; parlays 0-4). Fixed: parlay builder ignored "same game day only" (3 week-6 parlays paired Sunday legs with MNF; all already have a lost leg, Record untouched); parlays now built and capped per game day; test added (87). Unmatched names in status are Sun 12:40 AM pulls from before the NICK fix.
- 2026-10-18 8:10 PM: RESUMED at Matt's request. `config/stop_timer` removed (push restarted the chain, link 0, run 39179604468); the three NFL scheduled tasks re-enabled. Status clean: no failed steps, page 200, pool 1,333 (tier full), 15:10 pull has no unmatched names (the Brown/Ogletree fix holds; the status list is the day's accumulated list). Open picks 5, parlays 7 (most games started).
- 2026-10-18 ~1 PM: PAUSED at Matt's request. `config/stop_timer` committed (timer chain and all odds pulls stop; page stays up with last data, will go stale). Three NFL scheduled tasks (Sun check, Mon/Thu check, Tue review) disabled. To resume: delete `config/stop_timer` (push restarts the chain) and re-enable the three tasks.
- 2026-10-18 12:05 PM: game-day check. Chain alive (12:00 run, link 313), no failed steps, page 200, pool 1,361 (tier standard, 51/game), inactives confirming team by team for 1 PM games (ESPN lag, as designed), nothing to grade yet. Fixed: feed nicknames never matched (Hollywood Brown, Drew Ogletree): Matcher now uses NICK; test added (84). James Jordan not on any roster, left unmatched.
- 2026-10-18 ~1 AM: LIVE. v1 pushed 12:30 AM, first real run 12:31 AM (25 open picks, 3 parlays, 1,926 credits,
  tier full). QA round 1 SHIP WITH FIXES -> fixed; QA round 2 on live data FAIL (reasons vs Research) -> fixed and
  re-verified with a checker (25 of 25 reasons match Research). QA round 3 SHIP WITH FIXES: lopsided 1H totals
  tightened (main pair -160..+140, else DraftKings). Reviews scheduled: Sun 11:47 AM, Mon/Thu 6:47 PM, Tue 10:47 AM.
  Live page check from the runner: 200.
- 2026-10-13: probes (secret `ODDS_API_KEYS`, Hard Rock market list, ESPN, Pages), DS model + backtest, Sharp
  standard v1.0, UX spec + page, Builder backend. Work stalled before go-live; Thursday missed.
