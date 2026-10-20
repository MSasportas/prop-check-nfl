# TEAM.md: Prop Check NFL operating doc

Read this first in every session. Update it last.

**Live page: https://msasportas.github.io/prop-check-nfl/** · Repo: MSasportas/prop-check-nfl (public)

## 1. The product
Every NFL game day: every leg that qualifies at a real Hard Rock (`hardrockbet_fl`) price, ranked best first, each
with the price, our chance vs the price's break-even and a one-line reason, plus 2-3 leg parlays from the top legs.
Grouped by game day. Each pick shows how settled it is: Early / Injury report in / Inactives confirmed. Pulled picks
stay visible with the reason. Research: any game (lines, weather, injuries, inactives, defense ranks, players) and
any player (Hard Rock lines, last 10 games FG and 1H, chart). Record: every pick and parlay as shown, graded.
Dark, phone first (~380px), All / 1st half / Full game toggle, bottom tabs Picks · Research · Record.

**Customer facts (fixed):** Matt; Hard Rock Florida only; no leg worse than -200 (plus money fine); parlays 2-3 legs;
player props are the core, 1H and FG (sides/totals allowed); hit-based; bets any time from early week to kickoff.
**Fixed:** the Record. Every pick and parlay exactly as shown, graded after the game at the posted price,
append-only, pulled picks kept and marked. Nothing edited or dropped. Started clean on Oct 18, 2026 (week 6). The
old Spotlight Record is retired. It is the only reporting Matt wants.
**Come to Matt only for:** more odds keys, paying for something, a GitHub setting we can't reach. Exact taps, one
short message.

## 2. How we are judged
1. Picks hit, at the price posted. Fewer picks beats picks that miss.
2. Always current; anything missing or stale is said plainly on the page.
3. A usable set of picks every game week.
4. Readable on a phone in seconds; Research fast.

## 3. The team (subagents run by the CEO; any chat in the project is the CEO)
Sharp (betting standard, final call on rules) · Data Scientist (data, model, samples, judging the Record) · UX
(page) · Builder (all code) · QA (tests every change before it goes live, real browser at 380px, fresh eyes each
round). Rule changes: the DS names the sample first; the Sharp decides; log it in section 8.

## 4. Current rules (v1.1, 2026-10-20; v1.0 2026-10-13). Source of truth: `config/rules.json`
Set by the Sharp from the DS backtest (fit 2024+2025, out of sample on 2025 and 2026 weeks 1-5) and one real Hard
Rock payload (SEA@DEN). Not tested against real prices (no free price history; `data/odds/` is building one).
The old Spotlight rules (brief 025) were dropped, not carried over.

| Area | Rule |
|---|---|
| Markets picked | **Full game:** receptions, receiving yds, rushing yds, carries, rush+rec yds, passing yds, passing TDs, longest catch, longest run (over and under); interceptions (under only). **1st half:** passing yds, receiving yds, rushing yds (over and under). Main Hard Rock line only |
| Shown, never picked | Pass attempts, completions, pass+rush yds, longest completion (model failed out of sample or no model). Anytime TD FG/1H (Yes-only market, no No price). Alt lines (all markets). Sides and totals (no model). 1H spread and 1H total come from DraftKings, labelled "DraftKings" |
| Research only | 1H receptions: Hard Rock has no market. Model number only, no price. `player_receptions_h1` is never requested |
| Price gate | -200 to +120 |
| Chance | Model chance for that side, minus the DS shrink for that market/half/side (0.03-0.07), minus 0.03 more on an over after a role jump. Then 60% that + 40% Hard Rock's de-vigged price = **our chance** (shown on the card) |
| Qualify | Model (after shrink) ≥ 0.62 **and** our chance ≥ 0.57 **and** our chance beats break-even by 0.04 (Early) / 0.03 (Injury report in) / 0.02 (Inactives confirmed). Model beats Hard Rock's fair by more than 0.15 = no pick at any settledness (v1.1; was wait 0.15 / no pick 0.20). No new pick from a Hard Rock price over 6h old. Line's vig must be 3-10% or the line is skipped as a feed glitch |
| Eligibility | Under 6 games (this season + last) = no pick. Under 8 games, missed 2+ straight games, under 3 games with his team, his team's QB changed, = no over. A jumpy role (targets/carries swing 60%+ game to game) = no pick either side (v1.1). Room change (v1.1): a WR/TE/RB with 8%+ target share or 25%+ carry share (last 4 games) joins or leaves a team (trade, cut, IR/PUP/NFI; weekly Out doesn't count) = no picks in that team's receiving/rushing room until he/they have 2 games since. Missed his team's last game = waits for the injury report and must carry no tag. Listed Questionable or worse = no pick; Questionable on the final report = no pick for that game even if active. Stats missing his team's last game = no picks for that team (named on the page) |
| News | Key teammate (20%+ of targets or 35%+ of RB carries, last 4 games) Doubtful/Out: that team's legs in the same room can't have a model-vs-book gap over 0.15. Starting QB Doubtful/Out: no overs for that team; QB Questionable: no overs for that team until inactives confirm him. Outdoor game with 15+ mph wind forecast at kickoff (within 48h): no overs in passing yds, passing TDs, receiving yds, longest catch (FG or 1H) |
| Settled | **Early** from the first price. **Injury report in** 60 min after the final injury report (4 PM ET on the last practice day: Sun game = Fri, Mon = Sat, Thu = Wed, Sat = Thu) once a fresh ESPN injury read is in. **Inactives confirmed** when ESPN lists 3+ inactives for both teams (checked from 100 min before kickoff). Not confirmed by 60 min before kickoff: stays "Injury report in", page says "Inactives not confirmed yet", locks as "Locked: inactives not confirmed" |
| Ranking and caps | By our chance, then margin, then most settled. 1 pick per player, 2 per team, 3 per game, no slate cap. First 8 open, rest under More |
| Parlays | 2-3 legs, one leg per game, same game day only. Legs: our chance ≥ 0.58, price -200 to +100. Combined chance ≥ 0.34 (2 legs) / 0.20 (3 legs) and beats the parlay break-even by 0.03. Up to 2 a game day, a leg in at most 1; caps count every parlay posted that day (open, pulled, locked) (v1.1). Price = product of leg prices, shown as "about". A one-game day (most Thursdays) has no parlays and says so |
| Pulls | Listed Questionable or worse, or inactive. Price past -200 or past +120. Hard Rock moves the line (pulled, "Hard Rock moved the line to X"; the new line must qualify on its own as a new pick). Line or market gone. Our chance drops below break-even. Price moves 4+ points of break-even against the pick before inactives are confirmed. Starting QB ruled out (his team's overs). 15+ mph wind (weather overs). Rules tightened: open picks that now fail a rule are pulled ("Rules tightened: ..."); gap over 0.15 pulls a shown pick any time. A parlay with a pulled leg is pulled. Pulled picks stay on the page and in the Record |
| Lock and grading | Locks at scheduled kickoff. Full game includes OT; 1H = quarters 1-2. Didn't play = void; whole-number tie = push. Graded from nflverse play-by-play (matches official stats 99.8-100%), ESPN box score if nflverse is 36h+ late. "Posted price" = the price on the card; if a pick showed more than one price while open, it's graded at the **worst** one shown (the Record lists every price). Parlay: any loss = loss; a void/push leg drops out and the parlay is repriced from the rest. Flat 1 unit. Pulled picks are graded too, shown apart, never in the hit rate |
| Odds pulls | Allowance = credits left (read from each response) / games left before the keys reset on the 1st. **M** = 1-credit "are props up" check, from 5 days out at 10 AM and 4 PM, earliest game of each game day first. **F** = full pull: every Hard Rock player prop for the game + game lines (~41 credits), once 15+ player markets are up; forced 24h before kickoff. **D** = 10 AM daily re-check of markets holding open picks. **R** = main player lines + game lines 60 min after the final injury report (~18). **C** = markets holding picks or near-picks + spread/total, 75 min before kickoff, retried to 20 min (~5). Tiers (credits/game): 76+ M+F+D+R+C · 66+ M+F+R+C · 48+ M+F+C · 43+ M+F (C only where a pick is open) · 24+ M+R+C (no full pull) · below: R only, earliest games first. Games after the 1st (key reset) get their full pull early only from credits spare after reserving the full tier for every game before it. Started games are never pulled |
| Page when not pulled | "Hard Rock hasn't posted player props for this game yet. Next check 4 PM." / "Main lines only for this game (odds credits are low)." / "No Hard Rock prices pulled for this game (odds credits are low)." / "Price as of 10:02 AM." on any price over 6 hours old |
| Safety | A market (each half separate) pauses itself at 30 graded picks if its hit rate is 15+ points under its average break-even or it's at -8 units. Whole board at 60 graded picks running 10+ points under break-even: every margin tightens by 0.03 automatically. Tightening any value: any time, logged. Loosening: not before 100 graded picks in that market (150 for board-wide values), one change at a time. DS may refit the model weekly; calibration and these values change only by this rule |
| Reason line | One sentence: "60% to stay under 4.5 catches, needs 54%. 3.6 catches a game his last 5 (2 of 5 over 4.5); SEA allows the 3rd fewest catches to WRs." Chance rounded down, break-even rounded. Opponent clause dropped if it runs long |

### Why (one line each)
- **Markets:** the DS "use" list plus the "caution" markets (higher shrink already priced in). Pass attempts/completions/pass+rush yds fell apart out of sample in 2026 (the .70+ legs hit 54-59%), so they stay off.
- **No alt lines:** in SEA@DEN, 30 alt lines sat inside the gate; none got a model chance near 0.62 (best 0.53). They are over-only (no under price to de-vig), carry more vig, and sit in the tails where the model was never calibrated. Revisit only with a DS backtest on tail lines.
- **No anytime TD:** Hard Rock posts only "Yes" (no "No" side), so there is no fair price, and only 2 of 28 Yes prices were inside the gate (both +100, model 37-48%). The 1H TD is worse: the model trusts only the "No" side, which isn't offered.
- **No sides/totals:** no model for them and they are the sharpest lines on the board. Hard Rock's 1H spread/total in the feed are broken (1H total 30.5 at -800/+425), so those come from DraftKings, display only.
- **+120 max:** past +120 the break-even is under 45%: a coin-flip-or-worse bet doesn't fit "picks must hit".
- **Blend 60/40, min model 0.62, min chance 0.57:** the DS pick rule at 0.62 hit 69-78% out of sample on proxy lines. Real Hard Rock lines are sharper, so the market's 40% vote and a 57% floor keep us honest.
- **Margins by settledness:** early-week prices move and injury news is unknown, so early picks need a bigger cushion (0.04) than picks after the report (0.03) or after inactives (0.02).
- **"Too good" at 0.15 (v1.1):** week 6 gap >0.15 went 4-12 (-8.6u), gap ≤0.15 went 8-4 (+2.7u); Hard Rock fair beat our chance on Brier (.258 vs .288). A big gap means we're missing something.
- **Jumpy role blocks unders too (v1.1):** role-unstable picks went 1-5 in week 6.
- **Room change (v1.1):** Jalen Brooks' snaps went 5%→53% after Harrison Jr. left ARI; both Brooks unders lost. 8% share is the level that catches Harrison (8.8% at ARI).
- **2 parlays a day, leg used once (v1.1):** parlays 0-7 in week 6, a builder bug let 7 go up with one leg in 4.
- **Questionable = no pick:** in 2026, players tagged Questionable who still played hit 16 of 31 picks the model rated 69%. That is the clearest leak we found.
- **Key teammate out:** the backtest can't test it (proxy lines don't move on news), but the real book will raise the line and our model won't know. Capping the gap stops us "finding edge" that is just the injury.
- **Wind:** 2024-26 overs in pass/receiving markets at 15+ mph hit 55% when rated 67% (n=29); unders were fine (74%). So wind blocks overs only.
- **Rank by chance:** Matt is hit-based. Margin breaks ties.
- **2 per team, 3 per game:** one game script can sink every leg on one team.
- **One leg per game in parlays:** no correlation guesswork; one bad game kills one parlay at most.
- **Line moves pull the pick:** the old number is no longer bettable, so showing it as live would be wrong. The Record keeps the original exactly as shown.
- **Worst price shown:** the Record must never flatter us; Matt may have bet at any price the card showed.
- **Odds plan:** through Oct 31 there are 29 games (SEA@DEN through CAR@GB). ~2,330 credits / 29 ≈ 80 a game: every game gets the top tier, including one full all-props pull, if the keys are ours alone. Per-game cost by tier: about 80 / 65 / 47 / 42 / 24. Tiers step down on their own if credits run low. Nov 1 games wait for the reset unless there are spare credits, so October games keep their full plan.
- **Faster pause (30 legs, not NHL's 50):** NFL volume is weekly; 50 legs in one market could take a month of losing.

## 5. How it runs
- `scripts/run.py` is the only entry point; each run decides what's due: rules, schedule (nflverse games.csv +
  ESPN scoreboard), stats (nflverse play-by-play -> `data/state/rows<season>.json.gz`), roster, injuries (ESPN,
  nflverse fallback), weather (open-meteo), odds (pulls M/F/D/R/C), engine (keep / pull / lock shown picks, add new
  ones, parlays -> Record), grade, slate, research, record, status. Each step may fail without stopping the others.
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
- **Odds keys:** GitHub secret `ODDS_API_KEYS` (comma-separated, 7 keys on Oct 18). Never in the repo, page or
  logs; passed by name only (never `toJSON(secrets)`: GitHub holds the run). The ledger trusts
  `x-requests-remaining` every call; a key spent by another tool (Prop Check NHL) is marked shared and counted at a
  discount. Page warns when the pool is low.
- **Files:** `index.html` (page), `data/slate.json`, `data/record.json`, `data/rules.json`, `data/status.json`,
  `data/research/` (index + one file per game), `data/odds/<date>.json` (every price pulled), `data/state/`.
  `fixtures/` = page test data (`?fixture=normal|fresh|nopicks|allstarted|recordempty|stale`).
  Tests: `python -m unittest discover -s tests` (87), incl. a full replayed Thursday. Tests use frozen stat rows
  in `tests/fixtures/rows/`, never the live data.

## 6. Reviews (scheduled tasks; each reads this file first and updates it last)
- **Game-day checks:** Sun 11:47 AM and Mon/Thu 6:47 PM ET (scheduled tasks). Chain alive, pulls ran, failed steps, unmatched
  names, inactives confirming, last games graded. Fix what's broken; no rule changes.
- **Weekly team review:** Tuesdays 10:47 AM ET (scheduled task) (after MNF is graded). Full team on the Record and the odds log:
  volume, hit rate vs break-even by market, closing-price check (did Hard Rock move toward us?), calibration; ship
  agreed fixes through QA; log changes in section 8.

## 7. Known gaps and risks
1. **Thursday Oct 15 (SEA@DEN) was missed.** Go-live slipped to 12:31 AM Sun Oct 18. The Record starts with week 6
   Sunday. SEA@DEN shows as started with no picks.
2. **Edge vs Hard Rock is unproven.** Model leans under on most lines; first card (Oct 18) was 25 picks, nearly all
   unders, our chance 57-67%. Judge at the first weekly review with the closing-price check before touching values.
3. The first 28 picks (12:31 AM Oct 18) were posted with reason lines whose defense rank came from the model's
   weighted table, which didn't always match Research. Fixed 12:50 AM (reasons now use Research's plain ranks and
   drop clauses that argue against the side); the Record keeps what was first shown, plus "update" events.
4. Hard Rock house rules not verified: longest catch/run with no catch/carry (graded 0), parlay void-leg repricing.
5. Parlay price is the product of leg prices ("about"); Hard Rock's real parlay price may differ a little.
6. 1H receptions: no Hard Rock market in the feed; Research shows the model number only. `player_receptions_h1`
   is never requested (it fails the whole call).
7. Hard Rock's 1H total in the feed is an alt line (e.g. 30.5 at +425/-800); Research shows only a main pair
   (both sides -160..+140, closest to even), so 1H total and 1H spread come from DraftKings, labelled.
8. Key pool may be shared with Prop Check NHL; both step down together if it runs low. Keys reset on the 1st.
9. Timer chain holds one runner most of the day (free on public repos; GitHub discourages always-on use).
10. Inactives depend on ESPN's pre-game feed; if not confirmed by 60 min before kickoff the pick locks as
    "inactives not confirmed".
11. Model blind spots: rookies get position averages; no route data; traded players need 3 games before an over.
12. 320px phones: long names truncate. Thanksgiving week needs an injury-report override (report is Tuesday).
13. Final games on Research still say "Weather not posted yet" / "No Hard Rock game lines yet" and don't show the
    score (QA round 3 minor). Status sheet says "Stats through week N" before that week's Sunday games are played.
14. Research has no TD-pass defense row, so the Daniels-type reason ("7th fewest TD passes") can't be checked
    there; longest-catch reasons cite receiving-yards ranks (shown under that market).
15. CLOSED 2026-10-20: unders vs top-10-allowing defenses went 6-6, vs stingy (bottom-10) 1-7. No rule.
16. ADDRESSED 2026-10-20 by the room_change rule (v1.1). Not covered: exact transaction date (only latest roster week), returns from IR. Was: **A new teammate isn't seen by the model.** Marvin Harrison Jr. joined HOU after week 5 (ARI through week 4, no games for
    HOU yet), and Xavier Hutchinson's Over 10.5 longest catch (-120) was built from Hutchinson's last 5 games, all before
    that. Eligibility flags the player who moved (3-game rule), not the teammates whose targets he takes. Sharp/DS to add:
    when a WR/TE/RB joins a team, that room's overs wait or are blocked for N games. Found by Matt, Oct 18, 9:11 AM.

17. **Week 6 Record: singles 12-16 (-5.9u), parlays 0-7, pulled 1-3.** Zero closing-line value (Hard Rock rarely moves; 18 of 28 at -115/-115). Calibration inverted (our chance .63+ went 0-4). The 60/40 blend is unchanged (calibration waits for the sample rule); DS re-checks at 60 graded. Board auto-tighten (0.03) fires at 60 graded if still 10+ pts under. v1.1 cuts week 6's card to ~8-10 singles; DS flags any week under 5 legs.
18. QA follow-ups (minor): Record Parlays row shows "–" under Needed; nopicks fixture repeats the mid-week props note under every day.

## 8. Rule-change log
| Date | Change | Why | Sample |
|---|---|---|---|
| 2026-10-20 | v1.1 (all tightenings): `too_good` 0.20→0.15 (open picks over it pulled); role-unstable blocks unders too (`elig.role_cv_blocks` both); no new pick on a price >6h old; `parlay_cap` 3→2, `parlay_leg_max_use` 2→1, caps count locked/pulled parlays (bug fix); new `room_change` (8% targets / 25% carries, 2 games) | Week 6: gap>0.15 4-12, role-unstable 1-5, parlays 0-7 (7 on one day), picks posted on 11h-old prices, Brooks/Hutchinson room changes | 28 graded singles, 7 parlays. Tightening allowed any time; no loosening before 100/market, 150 board |
| 2026-10-20 | Page: Record headline shows singles + parlays; filter-aware week totals; pulled entries greyed "not counted"; calm mid-week no-props note; odds warning only on the backend low flag | UX review at 380px | Display only |
| 2026-10-18 | Reason line: context clauses (defense rank, team total) only when they support the side; defense ranks are the plain this-season numbers Research shows | QA: reasons contradicted Research and some argued against the pick | Display only, no pick rule changed |
| 2026-10-18 | Game lines on Research: main pair only (both sides -160..+140, closest to even); otherwise DraftKings, labelled | Hard Rock's 1H total in the feed is an alt line | Display only |
| 2026-10-13 | v1.0 set (section 4) | First build. Sharp standard from the DS backtest and one live Hard Rock pull. Spotlight rules dropped | Backtest only; 0 graded live legs |

## 9. Session log (newest first, one line each)
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
