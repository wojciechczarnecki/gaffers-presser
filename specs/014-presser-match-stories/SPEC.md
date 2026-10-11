---
status: implemented
stage_history:
  - "spec-draft — 2026-10-10"
  - "spec-ready — 2026-10-10"
  - "plan-draft — 2026-10-10"
  - "plan-approved — 2026-10-10"
  - "implemented — 2026-10-11"
metrics:
  started_at: 2026-10-10T23:28
  plan_steps: 13
  plan_changes: 6
  escalations: 0
  escalations_permission: 0
  escalations_tooling: 0
  plan_review_blockers: 0
  plan_review_majors: 1
  implement_steps: 13
  implement_iterations: 12
  deviations_minor: 4
  deviations_major: 0
---

# SPEC 014 — Presser match stories, chips section and table rivalries

## Goal

The owner likes the presser's sections but finds them thin. Chips get lost among the bench
and transfers. A failed or a good transfer gets one flat line. The table section reports
movements without the stories behind them. And nothing says which real-football moment
decided the gameweek: a differential haul, a red card, or an Arsenal defence that lost its
clean sheet and with it the "Manager kolejki" title. It works when the fact sheet carries
these stories, computed from FPL data only. That means notable players and match events
with their owners in the league, key moments that explain a result between two managers,
successful transfers and paid-off hits, and swaps between table rivals and on the podium.
The presser then gets a "⚽ Z boiska" section and a separate "🃏 Chipy" section, and a
2500-character limit. This runs before the model comparison (BACKLOG #32), so #32 compares
models on the presser the owner wants.

## Context

- Specs 012 and 013 built the presser: the fact sheet (`backend/app/presser/facts/`,
  `FactSheet` version 2 in `schema.py`, six sections in `SECTIONS`), the writer
  (`backend/app/presser/writer.py`, prompt `backend/app/content/prompts/presser_writer.md`
  version 4, at most 1500 characters), the judge (`presser_judge.md` version 3), the 3 style
  examples (`presser_style_examples.md`) and evaluation set v2 (`backend/evals/presser/v2/`).
- The `bench_transfers_chips` section today holds bench points, hits, transfer misses
  (player in scored less than player out), chip plays with their `effect`, Free Hit and
  Wildcard squad changes, and autosubs (`facts/gameweek.py`). Successful transfers and
  whether a hit paid off are not computed.
- The table section (`facts/table.py`) has the rows with a one-gameweek `movement`, the top 3
  and the biggest climbers and fallers. The league's per-gameweek totals are in
  `manager_gameweek.total_points`, so earlier tables can be recomputed for any gameweek.
- Match data already collected (checked in the local database on 2026-10-10):
  - `player_gameweek_result.explain` (from `event/{gw}/live`) holds, per fixture, the
    point-bearing stats: minutes, goals, assists, clean sheets, goals conceded (only when
    they cost points), saves, bonus, yellow and red cards, own goals, penalties missed and
    saved, defensive contribution. A zero-point stat is left out: a defender with 90 minutes
    and 1 goal conceded has no `clean_sheets` and no `goals_conceded` entry.
  - `fixture` holds each fixture's teams and final score, and `player` the club
    (`team_fpl_id`), position and `web_name`.
  - `deadline_snapshot_player.selected_by_percent` holds each player's overall FPL
    ownership at the deadline, from GW6 onward only (the worker took its first snapshot at
    the GW6 deadline; GW1–5 have none).
  - Squads, captains, chips, transfers and autosubs of every league member are in
    `manager_pick`, `manager_gameweek`, `manager_transfer` and `manager_auto_sub`.
- What FPL does not give: the minute of a goal or card, substitution times other than a
  player's minutes, and goals disallowed by VAR. A "goal in stoppage time" or "VAR ruled
  out his goal" needs an outside match-events source (out of scope).
- From the data above these can be derived per fixture without new collection:
  - A goalkeeper or defender who played at least 60 minutes and has no clean-sheet entry
    lost the clean sheet.
  - One who has a clean-sheet entry while his team conceded in that fixture kept the clean
    sheet because he went off before the goal ("lucky sub").
  - One who started, played fewer than 60 minutes, while his team kept a clean sheet lost
    it by going off early ("unlucky sub").

## Read context

- `docs/ROADMAP.md`: read in full. This spec refines Stage 3 item 1 like spec 013 did and
  runs before item 3 (the model comparison, BACKLOG #32). Item 4 (the slang glossary from X
  and podcasts) stays separate. The definition of done for an LLM stage (Langfuse traces, an
  evaluation set with recorded results) applies.
- `docs/PROJECT.md`: read in full. FR-3.1 (a presser per league in Polish FPL slang,
  manager of the gameweek by net points, mild banter) and FR-3.3 (the season race) stand.
  The non-functional rules hold: the 20 PLN budget, Polish content in
  `backend/app/content/`, evaluation before done, and no manager names in logs or the
  repository. Player and club names are public FPL data.
- `docs/DECISIONS.md`: read in full. The 2026-10-09 and 2026-10-10 presser rows bind this
  spec:
  - the fact sheet is computed by SQL and Python and checked for consistency, and the LLM
    only writes; its "manager display names only, no IDs or team names" is changed by this
    spec: the managers' FPL team names and Premier League club names enter the sheet (see
    Decisions);
  - net points everywhere and whole points;
  - chip effects as defined (Bench Boost bench points, Triple Captain extra points, Free Hit
    and Wildcard against the squad before);
  - GW ranks and the "🌍 Overall" section with its thresholds;
  - writer temperature 0.8; the evaluation set and pass rule (faithfulness ≥ 0.95,
    style ≥ 3.5);
  - rank rounding (writer prompt version 4);
  - a refactor a feature needs is done in that feature's spec (2026-09-30).
- `docs/BACKLOG.md`: read in full. #32 (the presser model comparison, P1) runs after this
  spec, on its prompt and evaluation set. #5 (images and audio) and #25 (automatic WhatsApp
  posting) stay out of scope. No item covers a match-events source, so this spec adds one.
- `docs/CONVENTIONS.md`: searched for "content", "Polish", "test", "fixture", "migration".
  Product content stays in Polish in `backend/app/content/`. LLM steps are unit-tested
  against a fake model.
- `docs/DEPLOYMENT.md`: searched for "presser", "migration", "results". No migration and no
  new environment variable are expected, so the runbook does not change.

## Scope

- **Fact sheet version 3** (`FactSheet.version = 3`), computed from data already collected:
  1. **Sections split.** `bench_transfers_chips` becomes `bench_transfers` (bench points,
     hits, transfer misses, transfer wins, autosubs) and `chips` (chip plays with their
     `effect`, Free Hit and Wildcard squad changes). `chips` is empty, and listed in
     `empty_sections`, when no league member played a chip this gameweek.
  2. **Successful transfers.** `transfer_wins` lists one-for-one transfers outside Free Hit
     and Wildcard weeks where the player brought in scored at least 6 points more than the
     player sold, ranked by the difference. Transfer misses stay as they are.
  3. **Hits judged.** Every hit carries `transfer_gain`, the sum over that manager's
     transfers this gameweek of (points of the player in − points of the player out), and
     `paid_off`, true when `transfer_gain` exceeds the hit's cost.
  4. **The pitch section (`pitch`).** Notable players, each with the club, the points, the
     league managers whose points it counted for (with the multiplier: captain ×2, Triple
     Captain ×3), and its kind:
     - `differential_haul`: at least 10 points, and either a global differential (under 10%
       overall ownership at the deadline) or a league differential (counted for at most 2
       league managers). Both flags are given. The global flag is unknown (null) when the
       gameweek has no deadline snapshot.
     - `template_haul` / `template_blank`: a player in the squads of at least 50% of the
       league managers who scored at least 10 points, or at most 2 points (the current blank
       rule). The managers without him are listed too.
     - `red_card`, `own_goal`, `penalty_missed`: the event and its points.
     - `lucky_sub` and `unlucky_sub`: as defined in Context, per fixture.
     A player is listed only when his points counted for at least one league manager, the
     template kinds aside. One player can carry several kinds. The list is ranked by
     importance and capped at 8 items:
     1. kinds tied to a key moment;
     2. then red cards, own goals and penalties missed;
     3. then hauls and blanks by points;
     4. then lucky and unlucky subs.
  5. **Key moments (`key_moments`).** For each pair of managers whose result is a story,
     the events that alone are worth at least the margin between them. Without such an
     event the order would flip or end level.
     - The pairs:
       1. each manager of the gameweek against the best of the rest, with the gameweek's
          net points as the margin;
       2. each table rivalry swap and each podium change below, with the new gap in total
          points as the margin.
     - The events, each with its point swing between the two managers:
       1. a player who counted for one of them and not the other, or with a different
          multiplier (a differential haul, a different captain);
       2. a lost clean sheet in the trailing manager's counted players, with a club's
          defenders from one fixture grouped into one event ("obrona Arsenalu"), worth their
          clean-sheet points;
       3. a red card, an own goal or a penalty missed in either manager's counted players.
     - Capped at 5 moments: the gameweek-winner pair first, then podium
       changes, then rivalry swaps, each by the swing.
  6. **Table rivalries and the podium.**
     - `rivalry_swaps`: two managers who were next to each other in the league table
       (adjacent ranks) after each of the 3 previous gameweeks and swapped order this
       gameweek. Each swap gives both managers, their old and new ranks, the new gap in
       total points, and the number of consecutive gameweeks they were neighbours. Before
       GW4 there are none.
     - `podium_changes`: every change in the top 3 against the previous gameweek: a new
       leader, a manager entering or leaving the top 3, and two podium managers swapping
       order. There is no history rule, but not at GW1.
     - The current top 3, climbers and fallers stay.
  7. **FPL team names.** Every table row carries the manager's FPL team name
     (`manager.team_name`), so the writer can use it now and then instead of the name.
  8. `check_fact_sheet` checks the new facts for internal consistency:
     - a transfer gain against its pairs, `paid_off` against the cost;
     - a key moment's swing at least its margin, and its pair present in the winners, the
       swaps or the podium changes;
     - a rivalry swap's ranks against the table, a podium change against the top 3;
     - a pitch item's owners present in the table;
     - the caps;
     - `empty_sections` against the sections.
- **Eight sections.** In this order, each skipped when empty:
  1. `🏆 *Manager kolejki:*`
  2. `🤦 *Wtopa kolejki:*`
  3. `©️ *Kapitanowie:*`
  4. `⚽ *Z boiska:*`
  5. `🪑 *Ławka i transfery:*`
  6. `🃏 *Chipy:*`
  7. `📊 *Tabela:*`
  8. `🌍 *Overall:*`

  The limit rises from 1500 to 2500 characters.
- **Writer prompt version 5** (in `backend/app/content/prompts/`):
  - the eight sections and the new facts;
  - sharper banter at a failed transfer, a hit that did not pay off or a chip with a poor
    effect, and credit for a good transfer, a paid-off hit or a strong chip;
  - the winner and flop lines may name the decisive key moment in one sentence, with the
    details left for "⚽ Z boiska";
  - a rivalry swap mentions how long the two were neighbours;
  - a manager's FPL team name may stand in for the name now and then, never invented;
  - the 2500-character limit.

  The rules of version 4 (slang only where natural, no copying, rank rounding,
  "średnia ligowa") stay.
- **Judge prompt version 4.** Pitch items, key moments (swings and margins), transfer
  gains, paid-off hits, rivalry swaps and podium changes are factual claims it checks against
  the sheet.
- **Style examples rewritten** for the eight sections, each under 2500 characters, sharing
  no sentence, with a pitch paragraph that ties a key moment to a result. Made-up managers
  and numbers; the owner approves them at the final review.
- **Evaluation set v3** on fact sheet version 3 replaces v2:
  - the real cases, rebuilt from the local database: GW1–5, plus GW6 when it is finished
    and collected before the set is built; pseudonymised as before;
  - the synthetic cases of v2, updated;
  - new synthetic cases: a differential haul that decides the manager of the
    gameweek; a lost clean sheet of two defenders from one club as a key moment; a template
    blank; a red card and an own goal; a lucky sub; a hit that paid off and one that did
    not; a rivalry swap after 3 gameweeks as neighbours; a podium change with a new leader;
    a gameweek without chips; a gameweek without a deadline snapshot (global ownership
    unknown).
  - FPL team names are pseudonymised like the manager names, and the leak check covers
    them.
  - The pass rule and the `review` inflection count stay.
- **Documents.**
  - BACKLOG #32 runs on writer prompt version 5 and set v3, with trigger "spec 014 is
    merged".
  - A new BACKLOG item (P3) for an outside match-events source (goal minutes, stoppage
    time, VAR, substitution times), with trigger "the owner misses timeline stories in the
    presser after #32".
  - DECISIONS records the eight sections, the 2500 limit, the thresholds, the key-moment
    rule and the team and club names in the fact sheet.
  - ROADMAP gets this spec's Stage 3 item, ticked at merge.

## Out of scope

- An outside match-events source (minutes of goals and cards, stoppage time, VAR decisions,
  substitution times): a new BACKLOG item, P3, see Documents.
- Backfilling the overall ownership of GW1–5 from `element-summary` history: the owner chose
  the deadline snapshot from GW6 on. Earlier gameweeks have the league ownership only.
- A "match of the gameweek" (the fixture that moved the most points in the league): offered
  and not chosen by the owner.
- Rivalry swaps outside the 3-gameweek neighbour rule, apart from podium changes.
- Key moments for the flop of the gameweek against the second-worst: offered and not chosen.
- The model comparison run itself (BACKLOG #32) and the name-forms dictionary it may lead to.
- The slang glossary from X and podcasts (ROADMAP Stage 3 item 4).

## Requirements and acceptance criteria

- [ ] AC1: The fact sheet is version 3 with the sections `winners`, `flops`, `captaincy`,
  `pitch`, `bench_transfers`, `chips`, `table`, `overall` in this order. `chips` holds the
  chip plays and squad changes and is listed in `empty_sections` when no league member
  played a chip. `bench_transfers` holds no chip facts.
- [ ] AC2: `transfer_wins` lists a one-for-one transfer when the player in scored at least 6
  points more than the player out (exactly 6 is listed, 5 is not), never in a Free Hit or
  Wildcard week, ranked by the difference.
- [ ] AC3: Every hit carries `transfer_gain` (the sum of in − out over the manager's
  transfers this gameweek) and `paid_off` (true only when the gain exceeds the cost; a gain
  equal to the cost is false).
- [ ] AC4: A `differential_haul` needs at least 10 points (10 is a haul, 9 is not). Its
  global flag is true under 10% deadline ownership (9.9% is, 10.0% is not) and null without
  a deadline snapshot for the gameweek. Its league flag is true when it counted for at most
  2 league managers.
- [ ] AC5: `template_haul` / `template_blank` need the player in the squads of at least 50%
  of the league managers (6 of 12 is, 5 of 12 is not), with at least 10 or at most 2 points.
  The item lists the managers with and without him.
- [ ] AC6: Red cards, own goals and penalties missed are read from `explain` per fixture,
  with their points. A player who played in two fixtures (a double gameweek) has each
  fixture's events.
- [ ] AC7: A goalkeeper or defender is a `lucky_sub` when he has the clean-sheet points
  while his club conceded in that fixture, and an `unlucky_sub` when he started, played under
  60 minutes and his club kept a clean sheet. A player with 60+ minutes and no clean-sheet
  entry while his club conceded has lost the clean sheet (used by key moments).
- [ ] AC8: A pitch item names the club, the points and the league managers it counted for
  with the multiplier. A player who counted for no league manager is not listed, except a
  template blank or haul. The list is capped at 8 and ranked as in Scope.
- [ ] AC9: Key moments are computed for each manager of the gameweek against the best of
  the rest, and for each rivalry swap and podium change. An event is listed only when its
  swing is at least the margin. A swing below the margin is not listed, and a swing equal to
  the margin is.
- [ ] AC10: A key moment's swing counts the multipliers (a differential captain doubles), and
  a lost clean sheet groups a club's defenders and goalkeeper of one fixture into one event.
  At most 5 moments, ordered as in Scope.
- [ ] AC11: A rivalry swap needs the two managers adjacent in the table after each of the 3
  previous gameweeks and their order swapped now. Neighbours for 2 gameweeks give no swap.
  Before GW4 there are none. Each swap carries the old and new ranks, the gap in total
  points and the neighbour streak.
- [ ] AC12: `podium_changes` reports a new leader, a manager entering or leaving the top 3,
  and a swap within the top 3 against the previous gameweek, with ties ranked as in the
  table. At GW1 it is empty.
- [ ] AC13: `check_fact_sheet` reports each inconsistency listed in Scope item 8 (one test per
  rule).
- [ ] AC14: The writer prompt (version 5) covers the eight sections in order with the headers
  in Scope, skips empty ones, asks for banter at failed transfers, unpaid hits and poor
  chips and credit for good ones, allows one key-moment sentence in the winner and flop
  lines, and sets the limit to 2500 characters. The stored presser records
  `presser_writer@5`.
- [ ] AC15: The judge prompt (version 4) lists pitch items, key moments, transfer gains, hit
  verdicts, rivalry swaps and podium changes among the claims it checks. A wrong swing or a
  wrong neighbour streak is `unsupported` (tested with a fake judge on a recorded reply,
  plus the prompt text check).
- [ ] AC16: The 3 style examples carry the eight headers, each under 2500 characters. A test
  checks that no sentence is repeated across examples and that each has a pitch paragraph.
  The owner approves the text at the final review.
- [ ] AC17: Evaluation set v3 holds the rebuilt real cases on fact sheet version 3, the
  updated v2 synthetic cases and the new synthetic cases listed in Scope, split dev / test.
  A test checks that every case validates and that no real manager or FPL team name is
  present.
- [ ] AC18: Every table row carries the manager's FPL team name. The application logs carry
  no manager or FPL team names, and no player or club tied to a manager (the spec 012 log
  capture test passes with the new facts).
- [ ] AC19: BACKLOG #32 reads trigger "spec 014 is merged", writer prompt version 5 and set
  v3. BACKLOG has the match-events source item with its trigger. DECISIONS records the eight
  sections, the 2500 limit, the thresholds, the key-moment rule and the FPL team and club
  names in the fact sheet.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| FPL data only; match timeline (goal minutes, VAR) deferred to BACKLOG | an outside match API in this spec; a separate spec right after | the owner: FPL already yields lucky subs, lost clean sheets, cards and differentials; an outside source is its own collector with its own risk |
| Overall ownership from the deadline snapshot (GW6 on) plus league ownership from picks | backfill GW1–5 from `element-summary`; league ownership only | the owner: no new collection; #32 adds GW6+ cases anyway |
| A new "⚽ Z boiska" section after the captains and a separate "🃏 Chipy" section, skipped when empty | events woven into existing sections; the pitch section second | the owner: result first, then causes; chips drowned among bench and transfers |
| Limit 2500 characters | 2000; 3000 | the owner: room for two sections and richer table stories, still one screen of WhatsApp |
| Thresholds: haul ≥ 10, blank ≤ 2, global differential < 10%, league differential ≤ 2 managers, template ≥ 50% of the league, good transfer ≥ 6 points, hit paid off when the gain exceeds the cost | stricter (12 / 5% / 1 / 70% / 8); looser (8 / 15% / 3 / 40% / 4) | the owner: the thresholds FPL players use; each is one constant |
| A rivalry swap needs 3 gameweeks as table neighbours; every podium change is reported without that rule | any neighbour swap from GW8; a points-gap rule | the owner: no noise early in the season, yet the podium always matters |
| Key moments for the gameweek winner against the best of the rest, the rivalry swaps and the podium changes | winner pair only; also the flop against the second-worst | the owner: the moment explains stories the presser already tells, instead of opening new ones |
| A key moment is an event whose swing alone is at least the margin | the biggest swing regardless of margin; an LLM choosing causes | "without it the order flips" is checkable arithmetic; the LLM only writes (DECISIONS 2026-10-09) |
| Premier League club names and the managers' FPL team names are allowed in the fact sheet; logs and the repository still carry neither manager nor team names, and the evaluation set pseudonymises team names | player names only; manager display names only (DECISIONS 2026-10-09) | "obrona Arsenalu" needs the club; the owner: FPL team names are a fun addition; both are public FPL data, so the privacy rule stays at logs and the repository |
| Ideas added by the owner: global differential, penalty missed, red card, own goal | — | the owner's answer to point f |

## Owner decisions

- No migration and no new dependency expected. A change that needs either goes back to the
  owner.
- This spec runs before BACKLOG #32, and #32 runs on writer prompt version 5 and set v3.
- The production deployment stays deferred by the owner.
- Rejected by the owner: an outside match-events source now, an ownership backfill for
  GW1–5, a "match of the gameweek", key moments for the flop, the pitch section second.

## Open questions (non-blocking)

- Whether 8 pitch items and 5 key moments fit 2500 characters with the other sections. The
  writer picks the most notable, and #32's previews may move the caps (one constant each).
