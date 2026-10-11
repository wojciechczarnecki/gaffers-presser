---
status: implemented
stage_history:
  - "spec-draft — 2026-10-11"
  - "spec-ready — 2026-10-11"
  - "plan-draft — 2026-10-11"
  - "plan-approved — 2026-10-11"
  - "implemented — 2026-10-11"
metrics:
  started_at: 2026-10-11T18:52
  plan_steps: 14
  escalations: 0
  escalations_permission: 0
  escalations_tooling: 0
  plan_changes: 3
  plan_review_blockers: 0
  plan_review_majors: 1
  implement_steps: 14
  implement_iterations: 7
  deviations_minor: 6
  deviations_major: 0
  final_review_blockers: 0
  final_review_worth_fixing: 8
  final_review_nits: 5
---

# SPEC 015 — Full FPL match stats in the presser

## Goal

The presser should use all the match data the FPL API gives, not only the events of spec 014.
Today the "⚽ Z boiska" section and the key moments know red cards, own goals, penalties
missed and saved, and clean sheets (lost clean sheets, lucky and unlucky subs). Goals,
assists, bonus, DefCon, saves and cards reach the writer only inside a player's total points.
So "his captain's 3 bonus points won him the gameweek", "a yellow card cost him by 1 point"
and "zabrakło 1 BPS do bonusu" cannot be said. The feature works when:

- every stat FPL publishes per player and gameweek, and BPS and bonus per fixture, is stored;
- the fact sheet carries the stats that make a story: a points breakdown, the stat that
  alone decided a pair of managers, a bonus missed by one BPS, hat-tricks, a goalkeeper who
  kept his owners afloat, and xG flags;
- the stats without a story stay in the data only;
- the presser still fits 2500 characters.

## Context

- Spec 014 (merged in PR #25) built fact sheet version 3 (`backend/app/presser/facts/`):
  - `schema.py`: eight sections, `PitchItem`, `KeyMoment`, `CaptainPick`;
  - `matches.py`: per-fixture events from `explain`, clean sheets lost, lucky and unlucky subs;
  - `pitch.py`: pitch items, capped at 8;
  - `moments.py`: key moments for the winner pair, podium changes and rivalry swaps, an
    event listed when its swing alone is at least the margin, capped at 5;
  - writer prompt version 5 (`backend/app/content/prompts/presser_writer.md`), judge prompt
    version 4, evaluation set v3 (`backend/evals/presser/v3/`).
- A key moment of kind `player` already carries the whole swing of a player who counted
  differently for the two managers, without saying which stat made it.
- What is stored today (`backend/app/fpl/results.py`):
  - `player_gameweek_result` keeps `starts`, `minutes`, `total_points` and `explain`
    (the point-bearing stats per fixture: value and points);
  - a zero-point stat is absent from `explain`.
- What is fetched but kept only in `raw_payload` (checked in the local database, GW1–5):
  - `event/{gw}/live` `elements[].stats`, per player **per gameweek** (summed over a double
    gameweek, not per fixture):
    - `bps`, `bonus`, `in_dreamteam`;
    - `expected_goals`, `expected_assists`, `expected_goal_involvements`,
      `expected_goals_conceded`;
    - `influence`, `creativity`, `threat`, `ict_index`;
    - `tackles`, `recoveries`, `clearances_blocks_interceptions`, `defensive_contribution`
      (the action count; its points are in `explain`);
    - `saves`, `goals_conceded`, `yellow_cards` and the other counts.
  - `fixtures[].stats`, **per fixture**, lists per side `(element, value)` for:
    - `goals_scored`, `assists`, `own_goals`;
    - `penalties_saved`, `penalties_missed`;
    - `yellow_cards`, `red_cards`, `saves`, `bonus`, `defensive_contribution`;
    - `bps` for every player who played (about 30 per fixture).
  - `raw_payload` holds two fetches per gameweek (the latest is authoritative).
- Frequency in the local data (GW1–5, 2 leagues, 27 managers, players in a starting XI):
  - 4 braces and 1 hat-trick;
  - 2 goalkeepers with 6+ saves;
  - 9 players with xG ≥ 0.7 and no goal, and 6 goals with xG < 0.15;
  - 8 players who missed a bonus by 1–2 BPS;
  - the manager of the gameweek won by 1, 2, 2, 5, 12, 2, 9, 4, 3 and 5 points, so a
    bonus, a DefCon or a yellow card often decides on its own.
- FPL's bonus rule: ties share the place, so players tied on the lowest bonus-earning BPS of a
  fixture each get that place's bonus. One more BPS for a player one below that BPS would have
  given him the same bonus.
- FPL gives no goal or card minute (BACKLOG #34), so no "stoppage-time goal" or "comeback".

## Read context

- `docs/ROADMAP.md`: read in full.
  - This spec refines Stage 3 item 1 like specs 013 and 014 did.
  - It runs before item 3, the model comparison (BACKLOG #32).
  - The definition of done for an LLM stage applies: Langfuse traces and an evaluation set
    with recorded results.
  - The production deployment (Stage 0) has not happened, so the migration runs only on the
    local and test databases now.
- `docs/PROJECT.md`: read in full. FR-3.1 (a presser in Polish FPL slang, mild banter)
  stands. The non-functional rules hold:
  - the 20 PLN budget;
  - Polish content in `backend/app/content/`;
  - evaluation before done;
  - no manager names in logs or the repository.
- `docs/DECISIONS.md`: read in full. The presser rows of 2026-10-09 to 2026-10-11 bind this
  spec:
  - the fact sheet is computed by SQL and Python from tables and checked for consistency, and
    the LLM only writes;
  - net and whole points;
  - the eight sections, the 2500-character limit and the 014 thresholds;
  - the key-moment rule "an event whose swing alone is at least the margin";
  - temperature 0.8, rank rounding, the pass rule;
  - a refactor a feature needs is done in that feature's spec (2026-09-30).
- `docs/BACKLOG.md`: read in full.
  - #32 (P1, trigger "spec 014 is merged") moves behind this spec.
  - #34 (an outside match-events source) stays: this spec still uses FPL data only.
- `docs/CONVENTIONS.md`: searched for "migration", "alembic", "backfill", "content", "test".
  Polish product content stays in `backend/app/content/`. LLM steps are tested against a fake
  model.
- `docs/DEPLOYMENT.md`: searched for "migration", "backfill", "presser". Every migration
  gets a runbook note (migration `0010` set the precedent of a backfill from stored payloads),
  so this spec adds one.

## Scope

- **Storage (migration on the local and test databases).**
  - Every field of `event/{gw}/live` `elements[].stats` is stored per player and gameweek.
  - The per-fixture lists of `fixtures[].stats` are stored per player and fixture, BPS and
    bonus included.
  - The results job stores both on every sync.
  - The migration backfills the gameweeks already collected from the latest stored
    `raw_payload` of each gameweek, with no FPL request.
  - A gameweek with no stored payload is left without the new stats.
- **Fact sheet version 4** (`FactSheet.version = 4`). The sections, their order and the
  2500-character limit stay.
  1. **Points breakdown.** Pitch items, captain picks and `player` key moments carry the
     player's non-zero point-bearing stats: identifier, value and points, from `explain`
     summed over the gameweek's fixtures. That covers minutes, goals, assists, clean sheets,
     goals conceded, saves, bonus, DefCon, yellow and red cards, own goals and penalties.
  2. **Decisive stat.** In a key moment of kind `player`, each breakdown entry carries its
     swing between the two managers (its points × the multiplier difference) and a
     `decisive` flag when that swing alone is at least the pair's margin.
     - A player whose whole swing is below the margin is still a key moment when one of his
       stats is decisive, e.g. the trailing manager's captain's yellow card at a margin of 1.
     - A stat told by its own key-moment kind (red card, own goal, penalty) is not flagged
       again.
  3. **Bonus missed by one BPS** (key-moment kind `bonus_missed_by_one`):
     - the player counted for the trailing manager of the pair;
     - his BPS in a fixture was exactly 1 below the lowest BPS that earned a bonus there;
     - he got no bonus in that fixture;
     - the bonus of that lowest place × his multiplier is at least the margin.
     It names the player, the fixture's club, the missed bonus and the swing.
  4. **New pitch kinds.**
     - `hat_trick`: 3 or more goals in one fixture.
     - `keeper_wall`: a goalkeeper with at least 2 save points in one fixture (6+ saves).
     Both are listed only when the player counted for a league manager. Each has its
     differential flags, like a haul.
  5. **xG flags.** On pitch items and captain picks:
     - `expected_goals` and `expected_assists` of the gameweek;
     - a `big_miss` flag: at most 2 points (the blank rule) with xG ≥ 0.7;
     - a `goal_from_nothing` flag: at least one goal with xG < 0.15.
     A lost clean sheet key moment carries `expected_goals_conceded`: the xGC of the group's
     player with the most minutes, null in a double gameweek.
  6. **Stats kept in the data only:**
     - ICT, influence, creativity and threat;
     - tackles, recoveries, clearances-blocks-interceptions;
     - the DefCon action count;
     - `in_dreamteam` and `expected_goal_involvements`.
     The "defence collapse" (a template club conceding 3+) and the Dream Team / player of the
     gameweek stories were offered and not chosen.
  7. Ranking: `hat_trick` and `keeper_wall` rank with the hauls and blanks (group 3, by
     points); the caps (8 pitch items, 5 key moments) stay.
  8. **Missing stats.** A gameweek without the new stats builds a sheet without breakdowns,
     xG flags, BPS moments and the two new kinds, rather than failing.
  9. `check_fact_sheet` checks the new facts:
     - a breakdown sums to the player's points;
     - a breakdown swing equals its points × the multiplier difference;
     - `decisive` matches the swing and the margin;
     - a `bonus_missed_by_one` swing is at least its margin;
     - a hat-trick has at least 3 goals and a keeper wall at least 2 save points;
     - the xG flags match the thresholds.
- **Writer prompt version 6**:
  - the breakdown may be named ("dublet i 3 bonusy");
  - a decisive stat may be named as the cause ("3 bonusy kapitana dały mu Managera kolejki");
  - a bonus missed by one BPS may be named;
  - hat-tricks and keeper walls;
  - the xG flags as a punchline only (a captain's blank with a big xG is "pech", not form),
    never a bare xG number list.
  The rules of version 5 stay.
- **Judge prompt version 5**: breakdowns, decisive stats, missed bonuses, hat-tricks, keeper
  walls and xG flags are factual claims it checks against the sheet.
- **Style examples.** The pitch paragraph of the examples uses one decisive stat and one xG
  punchline, each example under 2500 characters, sharing no sentence. The owner approves
  them at the final review.
- **Evaluation set v4** on fact sheet version 4 replaces v3:
  - the real cases are rebuilt from the local database: GW1–5, plus GW6 when it is
    finished and collected before the set is built;
  - the v3 synthetic cases are updated;
  - new synthetic cases:
    - a captain's bonus decides the manager of the gameweek;
    - a yellow card decides a pair by 1 point;
    - a DefCon decides a pair;
    - a bonus missed by one BPS flips a rivalry swap;
    - a hat-trick;
    - a differential keeper wall;
    - a captain's blank with a big miss;
    - a goal from nothing;
    - a lost clean sheet with its xGC;
    - a double-gameweek player with two fixtures' BPS;
    - a gameweek without the new stats.
  - Pseudonymisation, the leak check, the pass rule and the `review` inflection count stay.
- **Documents.**
  - BACKLOG #32 gets the trigger "spec 015 is merged", writer prompt version 6, judge
    version 5 and set v4.
  - DEPLOYMENT gets the migration note.
  - DECISIONS records:
    - the storage of all FPL match stats;
    - the decisive-stat rule;
    - the BPS rule;
    - the new thresholds;
    - the data-only stats.
  - ROADMAP gets this spec's link at the Stage 3 item it refines, ticked at merge.

## Out of scope

- An outside match-events source (goal and card minutes, stoppage time, VAR): BACKLOG #34
  stays.
- The "defence collapse" pitch kind and the Dream Team / player of the gameweek story: offered
  and not chosen by the owner. The data is stored, so a later spec can add them without new
  collection.
- A missed bonus as a pitch item or a captain attribute: the owner chose key moments only.
- xG stories as pitch items of their own ("big miss", "goal from nothing"): flags on existing
  items only.
- Per-fixture xG in a double gameweek: FPL's live endpoint sums the gameweek, and
  `element-summary` is not collected.
- The model comparison run (BACKLOG #32).

## Requirements and acceptance criteria

- [ ] AC1: After a results sync, every field of a player's `event/{gw}/live` `stats` for that
  gameweek is readable from the database. Every `(identifier, element, value)` of the
  gameweek's `fixtures[].stats` is readable per fixture, `bps` included.
- [ ] AC2: The migration fills these stats for every gameweek with a stored `event/{gw}/live`
  and `fixtures` payload, from the latest fetch of each. A test with two stored fetches of
  different values gets the later one. A gameweek without a payload stays without stats.
  `alembic downgrade` by one step removes the new storage.
- [ ] AC3: The fact sheet is version 4. Pitch items, captain picks and `player` key moments
  carry a breakdown of non-zero point-bearing stats that sums to the player's points, a
  double gameweek's fixtures summed.
- [ ] AC4: In a `player` key moment, each breakdown entry's swing is its points × (the ahead
  manager's multiplier − the behind manager's), and `decisive` is true exactly when that
  swing is at least the margin. A swing equal to the margin is decisive; one below is not.
- [ ] AC5: A player whose whole swing is below the margin is a key moment when one of his
  stats is decisive. Test: margin 1, the trailing manager's captain has a yellow card (−1 ×
  2), the player's total swing is negative, and the moment is listed with the yellow card
  decisive. Red cards, own goals and penalties keep their own kinds and are not flagged again.
- [ ] AC6: `bonus_missed_by_one` is listed when the trailing manager's counted player had a BPS
  exactly 1 below the fixture's lowest bonus-earning BPS, got no bonus there, and that bonus ×
  his multiplier is at least the margin. Not listed:
  - 2 BPS below;
  - a player of the leading manager;
  - a missed bonus × multiplier below the margin.
  When the lowest bonus place is shared, the missed bonus is that shared bonus.
- [ ] AC7: `hat_trick` needs 3 goals in one fixture (2 + 1 across a double gameweek is not
  one). `keeper_wall` needs a goalkeeper with at least 2 save points in one fixture. Both are
  listed only when the player counted for a league manager, and carry the differential
  flags.
- [ ] AC8: The xG flags:
  - `big_miss` is true for at most 2 points with xG ≥ 0.70 (0.69 is not);
  - `goal_from_nothing` is true for a goal with xG < 0.15 (0.15 is not);
  - both are false without the condition, and null without stats.
  A lost clean sheet key moment carries the xGC as in Scope item 5.
- [ ] AC9: ICT, influence, creativity, threat, tackles, recoveries, CBI, the DefCon action
  count, `in_dreamteam` and `expected_goal_involvements` are stored (AC1) and absent from the
  fact sheet (a test on the schema's field names).
- [ ] AC10: A gameweek without the new stats builds a valid version 4 sheet with no
  breakdowns, xG flags, `bonus_missed_by_one` moments, hat-tricks or keeper walls, and the
  014 facts unchanged.
- [ ] AC11: `check_fact_sheet` reports each inconsistency in Scope item 9 (one test per rule).
- [ ] AC12: Writer prompt version 6 covers breakdowns, decisive stats, missed bonuses, the two
  new kinds and the xG flags as punchlines. It keeps the version 5 rules. The stored presser
  records `presser_writer@6`.
- [ ] AC13: Judge prompt version 5 lists breakdowns, decisive stats, missed bonuses, hat-tricks,
  keeper walls and xG flags among the claims it checks. A wrong decisive stat and a wrong
  BPS story are `unsupported` (a fake judge on a recorded reply, plus the prompt text check).
- [ ] AC14: The 3 style examples keep the eight headers and stay under 2500 characters, with
  no sentence repeated across examples. Each pitch paragraph names a decisive stat. The owner
  approves the text at the final review.
- [ ] AC15: Evaluation set v4 holds the rebuilt real cases, the updated v3 synthetic cases and
  the new synthetic cases listed in Scope, split dev / test. A test checks that every case
  validates and that no real manager or FPL team name is present.
- [ ] AC16: The logs carry no manager or team names, and no player or club tied to a manager
  (the log capture test passes with the new facts).
- [ ] AC17: BACKLOG #32 reads trigger "spec 015 is merged", writer prompt version 6, judge
  version 5 and set v4. DEPLOYMENT notes the migration and its backfill. DECISIONS records the
  rules listed in Documents.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Store all live stats per player and gameweek and the per-fixture stats lists, with a migration that backfills from the latest stored payloads | parse `raw_payload` at fact-build time; backfill by re-fetching from FPL | facts come from tables (DECISIONS 2026-09-26, ADR 0002); `raw_payload` holds duplicate fetches; the payloads are already stored (migration `0010` precedent) |
| One rule for "this decided it": a breakdown entry whose swing alone is at least the margin | separate key-moment kinds per stat (bonus, DefCon, yellow); a breakdown without a flag | the 014 arithmetic, checkable; one rule covers bonus, DefCon and yellow cards; the LLM must not compute causes (DECISIONS 2026-10-09) |
| A bonus missed by one BPS is a key moment only when the missed bonus × multiplier is at least the margin | a pitch item or attribute; deferred to BACKLOG | rare but a strong story; as a standalone item it is noise (8 near-misses in five gameweeks) |
| New pitch kinds: hat-trick and keeper wall | defence collapse (a template club conceding 3+); Dream Team / player of the gameweek | the owner's choice; the collapse fired in 7 of 10 league-gameweeks at a third of the league |
| xG as attributes with threshold flags (big miss ≥ 0.7 xG on a blank, goal from nothing < 0.15 xG); xGC on a lost clean sheet | xG pitch items; data only | a weak story alone for a friends' league, a good punchline on a captain's blank; a flag spares the writer from interpreting numbers |
| Breakdown on pitch items, captain picks and `player` key moments | key moments only | "dublet i 3 bonusy" and "kapitan: 2 pkt za minuty i żółtka" need it; a few hundred tokens |
| ICT, influence, creativity, threat, tackles, recoveries, CBI, the DefCon count, `in_dreamteam` and xGI stay in the data | putting them in the sheet | no story a friends' league tells; DefCon points already reach the breakdown |
| BACKLOG #32 waits for this spec | running the comparison now on writer 5 / set 3 | the comparison must run on the final prompt and fact sheet; a run costs about $3–6 |

## Owner decisions

- Migration: accepted up front (local and test databases now; production gets it at the
  first deploy, which the owner runs). The backfill source is left to the spec: the latest
  stored payloads.
- No new dependency expected. A change that needs one goes back to the owner.
- BACKLOG #32 waits for this spec and runs on writer prompt version 6, judge version 5 and
  set v4.
- Chosen: one decisive-stat rule; hat-trick and keeper wall; xG attributes with flags;
  `bonus_missed_by_one` as a key moment only; the breakdown on pitch items, captains and key
  moments.
- Not chosen: defence collapse, Dream Team / player of the gameweek.

## Open questions (non-blocking)

- Whether 8 pitch items and 5 key moments with breakdowns still fit 2500 characters. The
  writer picks the most notable, and #32's previews may move the caps (one constant each).
