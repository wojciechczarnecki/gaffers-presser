# PLAN 015 — Full FPL match stats in the presser

## Owner summary

- **Approach:** Migration `0013` adds a JSONB column `player_gameweek_result.live_stats` (every
  field of FPL's live `stats`) and a table `player_fixture_stat` (one row per fixture, player
  and stat, BPS and bonus included). It backfills both from the latest stored payload of each
  gameweek, and the results job writes both on every sync. Fact sheet version 4 stays SQL +
  Python. A new `facts/stats.py` computes the points breakdown, the xG flags and the
  one-BPS-short bonuses. It feeds pitch items, captain picks and key moments, adds the
  `hat_trick` and `keeper_wall` pitch kinds, the `decisive` flag and the
  `bonus_missed_by_one` key moment. `check_fact_sheet` gets one rule per new fact. Then come
  writer prompt 6, judge prompt 5, three rewritten style examples and evaluation set v4
  (10 real + 31 synthetic cases).
- **Main risks:**
  1. The 31 synthetic cases are hand-written JSON. The consistency check catches arithmetic
     errors, but not dull stories.
  2. If GW6 is finished before the real cases are rebuilt, including it commits its overall
     ranks while they are current. The step stops and asks you, as in spec 014.
  3. A player moment can now be listed with a negative total swing, because one of his stats
     decided it (AC5). The writer and the judge get explicit rules for that case.
  4. A DefCon breakdown entry carries FPL's `explain` value, which is the action count of a
     gameweek that scored DefCon points. The sheet has no DefCon-count field (AC9 is a check
     on field names).
- **New dependency:** no.
- **Data migration:** yes. Migration `0013` adds a nullable column and a new table, and
  backfills them from the stored `raw_payload` with no FPL request. `downgrade` drops both.
  It was accepted up front in SPEC → "Owner decisions" (local and test databases now,
  production at the first deploy, which you run).
- **Manual scenarios for the owner:** 2. Approve the three rewritten style examples, and read
  one real preview (GW5 of your league) that uses the new stats.

## Approach

What the plan rests on:

- `specs/015-presser-full-match-stats/SPEC.md`: read in full.
- `docs/CONVENTIONS.md`: read in full. No docstrings, Polish content in `app/content/`, tests
  mirror `app/`, LLM steps run against a fake model, PostgreSQL tests run in a container, and
  a growing file is split by subdomain.
- `specs/014-presser-match-stories/PLAN.md`: read in full. It gives the step pattern: the
  interim red set test, the scratchpad helper for synthetic cases, the GW6 gate, the
  `postgresql+psycopg://` URL deviation, `build-cases --force`, and the final-review fixes
  (penalty saves, gap-0 podium, moment dedup).
- `docs/DECISIONS.md`: searched for "presser", "fact sheet", "key moment", "version 3",
  "team_name". It found the 2026-10-09/11 rows: the sheet built by SQL, net points, the eight
  sections and thresholds, the key-moment rule (swing ≥ margin, dedup), team and club names.
  Nothing conflicts with this spec.
- `docs/ROADMAP.md`: searched for "Stage 3", "014", "#32". Items 1–3 are ticked (specs 012,
  013, 014). The model-comparison line reads "(BACKLOG #32, after spec 014)".
- `docs/BACKLOG.md`: searched for "| 32 |" and "| 34 |". #32 has the trigger "spec 014 is
  merged" and the text "writer prompt version 5 and set v3". #34 stays as it is.
- `docs/DEPLOYMENT.md`: searched for "0010", "0012", "migration". The `0010` backfill note
  and the `0012` note (pre-deploy, clean downgrade) give the shape of the new note.
- `docs/PROJECT.md`: not searched again. The SPEC's "Read context" covers FR-3.1 and the
  non-functional rules, and nothing in this plan touches them beyond the SPEC.
- Code read in full:
  - `app/fpl/{results,schemas,reference,backfill}.py`;
  - `app/fpl/models/{snapshots,reference,__init__,columns}.py`;
  - migrations `0010`, `0011`, `0012`;
  - `app/presser/facts/{schema,load,build,matches,pitch,moments,checks}.py`;
  - `gameweek.py` (header and `captaincy`);
  - `app/presser/evaluation/{cases,building}.py`;
  - `app/content/prompts/presser_writer.md` and `presser_judge.md`;
  - `tests/presser/helpers.py`, `tests/fpl/test_results_sync.py`;
  - `tests/content/test_presser_content.py`, `tests/test_docs.py`;
  - `tests/presser/evaluation/{test_eval_set,factories}.py`.
- Code skimmed:
  - `tests/db/test_migrations.py`: the module constants, `_apply_bootstrap_at_revision`,
    and the `0010` and `0012` tests;
  - `tests/presser/test_facts_checks.py`: `problems_after` and the parametrised v3 test;
  - `test_runner.py::test_recorded_swing_and_streak_verdicts_counted`;
  - `test_service.py::test_logs_carry_no_names_with_match_facts`;
  - the test names of `test_facts_{moments,pitch,load,gameweek}.py`.
- Recorded payloads (`tests/fpl/payloads/`):
  - `fixtures[].stats` is a list of `{"identifier", "a": [{"value", "element"}], "h": [...]}`;
  - `elements[].stats` in `event-N-live` holds the fields listed in the SPEC context, with
    the xG fields as strings ("0.20").
- Local database (counts only, no names):
  - the sum of `explain` points equals `total_points` for every row of GW1–5;
  - `points_modification` is always 0;
  - the only zero-point `explain` entries are `minutes`;
  - a `defensive_contribution` entry has the action count as `value` (10–14) and 2 points;
  - `raw_payload` holds two `event/N/live` and two `fixtures` fetches for each of GW1–5;
  - no `bps` entry has the value 0, so a player with 0 BPS is absent from the list;
  - GW6 is not finished;
  - alembic is at `0012`;
  - no table has a `stats` or `live_stats` column yet.

### Design

**Storage** (`app/fpl/models/snapshots.py`, migration `0013`).

- `PlayerGameweekResult.live_stats: dict | None` is a JSONB column, nullable. It holds the
  whole `elements[].stats` object as FPL sends it. Null means the gameweek was collected
  before this spec and had no payload to backfill from.
- A new table `player_fixture_stat` holds the per-fixture stats.

  ```python
  class PlayerFixtureStat(SQLModel, table=True):   # __tablename__ = "player_fixture_stat"
      season: str            # PK, FK season.label
      fixture_fpl_id: int    # PK; FK (season, fixture_fpl_id) -> fixture
      player_fpl_id: int     # PK; FK (season, player_fpl_id) -> player
      identifier: str        # PK; "bps", "bonus", "goals_scored", ...
      side: str              # "h" or "a"
      value: int
  ```

- Variants considered:
  - (a) JSONB on the result row plus a normalised per-fixture table.
  - (b) Typed columns for about 30 live fields.
  - (c) JSONB `stats` on `fixture`.
  - Chosen: (a). It stores "every field" without a schema change when FPL adds one, and it
    follows the `explain` precedent. The table makes "per player and fixture" a direct read.
    (c) would also be refreshed by the reference sync, which the SPEC does not ask for.
- The column is named `live_stats`, not `stats`. The migration tests exclude columns by name
  across all tables, so the name must be unique.

**Results job** (`app/fpl/results.py`, `app/fpl/schemas.py`):

- `LiveStats` gets `model_config = ConfigDict(extra="allow")`. `live_stats` is
  `el.stats.model_dump()`, which keeps every field.
- `Fixture` gets `stats: list[FixtureStat] = Field(default_factory=list)`, with
  `FixtureStat(identifier: str, a: list[FixtureStatEntry], h: list[FixtureStatEntry])` and
  `FixtureStatEntry(element: int, value: int)`.
- After `apply_fixtures`, the job deletes the `player_fixture_stat` rows of the fixtures whose
  `event == gameweek` and inserts their stats anew. A re-sync therefore drops a stat that FPL
  withdrew, for example a rescinded card.

**Migration `0013`** is plain SQL and imports no app models.

- `upgrade` adds the column and creates the table.
- It backfills the column from the latest `event/{gw}/live` payload of each (season,
  gameweek):

  ```sql
  SELECT DISTINCT ON (season, gameweek_fpl_id) ...
  WHERE endpoint = 'event/' || gameweek_fpl_id || '/live'
  ORDER BY season, gameweek_fpl_id, fetched_at DESC, id DESC
  ```

  Then it runs `UPDATE player_gameweek_result ... FROM ...,
  jsonb_array_elements(payload->'elements')`, matched on `(e->>'id')::int`.
- It backfills the table from the latest `fixtures` payload stored for each gameweek, taking
  only the fixtures with `(f->>'event')::int = gameweek_fpl_id`. Both sides go through
  `jsonb_array_elements`, with `side` taken from the key. The insert joins `fixture` and
  `player`, so a row whose fixture or player is missing is skipped rather than failing the FK.
- `downgrade` drops the table and the column.

**Loaders** (`facts/load.py`):

- `MatchResult` gets `live: LiveLine | None`, with

  ```python
  LiveLine(goals_scored: int, expected_goals: Decimal, expected_assists: Decimal,
           expected_goals_conceded: Decimal)
  ```

  It is parsed from `live_stats` with `Decimal(str(value))`, and it is `None` when
  `live_stats` is null.
- `FixtureBonus(bps: dict[int, int], bonus: dict[int, int])` and
  `load_fixture_bonus(session, season, fixture_ids) -> dict[int, FixtureBonus]` read the
  `bps` and `bonus` rows of `player_fixture_stat`.
- `load_club_fixture_counts(session, season, gameweek) -> dict[int, int]` gives each club's
  number of fixtures in the gameweek, home and away.

**Availability rule.** Every new fact of a player needs his `live_stats`; `MatchResult.live`
being `None` means "no new stats". A gameweek without the new stats has `live` `None` for
everyone. Its sheet then has `breakdown` and the xG fields `null`, no hat-trick or keeper
wall, no decisive flags and no `bonus_missed_by_one` (AC10). A `None` breakdown means
unknown, and an empty list means the player had no point-bearing stat. This is the same
null convention as `global_differential`.

**Fact sheet version 4 shapes** (`facts/schema.py`; `Strict` models; only the changes):

```python
class StatPoints(Strict): identifier: str; value: int; points: int   # summed over the GW's fixtures
class MomentStat(StatPoints): swing: int; decisive: bool
class PlayerStats(Strict):                       # mixin for PitchItem and CaptainPick
    breakdown: list[StatPoints] | None = None    # None: no stats for the gameweek
    expected_goals: Decimal | None = None
    expected_assists: Decimal | None = None
    big_miss: bool | None = None
    goal_from_nothing: bool | None = None
class CaptainPick(PlayerStats): ...                       # existing fields kept
class PitchItem(PlayerStats): ...                         # existing fields kept
PitchKind += "hat_trick", "keeper_wall"
class KeyMoment(Strict):                                   # existing fields kept, plus:
    kind: ... | "bonus_missed_by_one"
    ahead_multiplier: int | None = None          # kinds player and bonus_missed_by_one
    behind_multiplier: int | None = None
    breakdown: list[MomentStat] | None = None    # kind player only
    missed_bonus: int | None = None              # kind bonus_missed_by_one only
    expected_goals_conceded: Decimal | None = None   # kind clean_sheet_lost only
class FactSheet: version: Literal[4] = 4
```

**Rules** (`facts/stats.py`, one constant each):

- `BIG_MISS_MIN_XG = Decimal("0.70")`, `GOAL_FROM_NOTHING_BELOW_XG = Decimal("0.15")`,
  `HAT_TRICK_MIN_GOALS = 3`, `KEEPER_WALL_MIN_SAVE_POINTS = 2`.
- `OWN_KIND_STATS = ("red_cards", "own_goals", "penalties_missed", "penalties_saved")`.
- `breakdown(result)`:
  - `None` when `result.live is None`;
  - otherwise `explain` summed per identifier over the fixtures, keeping entries whose
    summed points are not 0;
  - ordered by `(-abs(points), identifier)`.
- xG fields come from `live`:
  - `big_miss = points <= BLANK_MAX_POINTS and expected_goals >= 0.70`;
  - `goal_from_nothing = goals >= 1 and expected_goals < 0.15`, where goals is the
    breakdown's `goals_scored` value (0 if absent);
  - all four fields are `None` without `live`.
  - On a captain pick, the points are `base_points`, the player's own points.
- Hat-trick and keeper wall: `fixture_facts` emits two new kinds per fixture, only when
  `result.live` is not `None`:
  - `hat_trick` when the fixture's `goals_scored` value is at least 3;
  - `keeper_wall` when `position == 1` and the fixture's `saves` points are at least 2.
  `pitch.py` adds them as kinds. They join `SCORE_KINDS`, ranking with the hauls (group 3).
  `pitch.py` appends either kind only when `holders` is not empty (the player counted for at
  least one manager). The existing "needs an owner unless template" rule is not enough: a
  template player benched by everyone is still listed for `template_haul`, and must not
  carry `hat_trick` or `keeper_wall` (AC7). 2 + 1 goals across a double gameweek is no
  hat-trick.
- Decisive stat. In a `player` moment, each breakdown entry gets
  `swing = points × (ahead_multiplier − behind_multiplier)`, where a manager who does not
  count the player has multiplier 0. `decisive = swing >= margin and identifier not in
  OWN_KIND_STATS`. A `player` moment is listed when `swing >= margin` **or** any entry is
  decisive (AC5).
- `bonus_missed_by_one`, per fixture from `FixtureBonus`:
  - `L` is the lowest BPS among the bonus recipients, and `B` the bonus given at `L` (a
    shared lowest place gives the shared bonus);
  - a near miss is a player with BPS exactly `L − 1` and no bonus in that fixture, and a
    fixture with no bonus recipients gives none;
  - for a pair, a near-miss player with `live` gives a moment with
    `ahead_points = B × ahead_multiplier`, `behind_points = B × behind_multiplier`,
    `swing = behind_points − ahead_points` and `missed_bonus = B`, the club being the
    player's club;
  - it is listed when `swing >= margin`.

  The swing reads "his multiplier for the trailing manager" as the 014 multiplier
  difference. A player both managers count alike moves nothing, and "a player of the
  leading manager" (behind multiplier 0) is never listed, as AC6 requires.
- Lost clean sheet xGC. On a `clean_sheet_lost` moment, `expected_goals_conceded` is the
  `live.expected_goals_conceded` of the group's player with the most minutes in that
  fixture, ties broken by name. It is `None` when the club has more than one fixture in the
  gameweek (`load_club_fixture_counts`) or no group player has `live`.
- Moment order. The sort key's `-swing` becomes `-strength`. Strength is the swing, except
  for a `player` moment whose decisive entries have a larger swing, which uses the largest
  of those. The dedup key `(ahead, behind, kind, players)` is unchanged, so one player
  missing a bonus by one in both fixtures of a double gameweek is told once.

**Variants considered** (beyond storage):

- Where the breakdown sits on a key moment.
  - (a) `breakdown` plus the two multipliers on `KeyMoment`, so every swing can be
    recomputed by `check_fact_sheet`.
  - (b) A breakdown without multipliers.
  - Chosen: (a). SPEC Scope 9 asks for the entry swing to be checked against
    "its points × the multiplier difference", which needs the multipliers.
- When the new facts are gated.
  - (a) Gate them per player on `live`.
  - (b) Gate them per gameweek on any stored stat.
  - Chosen: (a). It is one rule, AC10 follows from it, and a player whose live row is
    missing degrades alone.

**Interim red test.** From step 4 until step 13, the committed set v3 no longer validates
against `FactSheet` version 4, so `tests/presser/evaluation/test_eval_set.py` fails at
collection. The verification of steps 4–12 runs with
`--ignore=tests/presser/evaluation/test_eval_set.py`. Step 12 converts the synthetic cases
and step 13 rebuilds the real ones. The full `verify.command` runs at the end.

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 1, 2 | `tests/fpl/test_results_sync.py::test_results_stored_and_archived` (`live_stats == el["stats"]`), `::test_fixture_stats_stored_per_player_and_fixture`, `::test_resync_replaces_fixture_stats` |
| AC2 | 1 | `tests/db/test_migrations.py::test_match_stats_migration_backfills_from_the_latest_payload_and_downgrades`, `::test_models_match_migration` |
| AC3 | 4, 6 | `tests/presser/test_facts_stats.py::test_breakdown_sums_explain_over_a_double_gameweek`, `::test_pitch_item_and_captain_carry_the_breakdown`; `tests/presser/test_facts_moments.py::test_player_moment_carries_breakdown_and_multipliers` |
| AC4 | 6 | `tests/presser/test_facts_moments.py::test_breakdown_swing_and_decisive_boundary` |
| AC5 | 6 | `tests/presser/test_facts_moments.py::test_trailing_captains_yellow_card_decides_at_margin_1`, `::test_red_card_entry_never_decisive` |
| AC6 | 7 | `tests/presser/test_facts_moments.py::test_bonus_missed_by_one_listed`, `::test_bonus_missed_by_two_not_listed`, `::test_bonus_missed_by_leading_managers_player_not_listed`, `::test_bonus_missed_below_margin_not_listed`, `::test_shared_lowest_bonus_place_gives_the_shared_bonus`, `::test_bonus_missed_by_a_player_both_managers_count_uses_the_difference` |
| AC7 | 5 | `tests/presser/test_facts_stats.py::test_hat_trick_needs_three_goals_in_one_fixture`, `::test_keeper_wall_needs_two_save_points_in_one_fixture`, `::test_hat_trick_of_an_uncounted_player_not_listed`, `::test_new_kinds_carry_differential_flags` |
| AC8 | 4, 8 | `tests/presser/test_facts_stats.py::test_big_miss_boundary_070_and_069`, `::test_goal_from_nothing_boundary_014_and_015`, `::test_xg_flags_false_without_condition_and_null_without_stats`; `tests/presser/test_facts_moments.py::test_lost_clean_sheet_carries_xgc_of_most_minutes`, `::test_lost_clean_sheet_xgc_null_in_double_gameweek` |
| AC9 | 2, 4 | `tests/fpl/test_results_sync.py::test_results_stored_and_archived` (stored); `tests/presser/test_facts_stats.py::test_data_only_stats_absent_from_the_schema` |
| AC10 | 4, 5, 7 | `tests/presser/test_facts_stats.py::test_gameweek_without_match_stats_builds_a_v4_sheet` |
| AC11 | 4, 5, 6, 7, 8 | `tests/presser/test_facts_checks.py::test_inconsistent_v4_fact_is_reported` (one parametrised case per rule) |
| AC12 | 9 | `tests/content/test_presser_content.py::test_writer_prompt_v6_rules`; `tests/presser/test_writer.py::test_prompt_version_label`; `tests/presser/test_service.py` (stored `presser_writer@6`) |
| AC13 | 10 | `tests/content/test_presser_content.py::test_judge_prompt_v5_checks_full_match_stats`; `tests/presser/evaluation/test_runner.py::test_recorded_decisive_stat_and_bps_verdicts_counted` |
| AC14 | 11 | `tests/content/test_presser_content.py::test_style_examples_eight_headers_no_shared_sentence` (plus the owner's approval, manual) |
| AC15 | 12, 13 | `tests/presser/evaluation/test_eval_set.py` (all, including `::test_full_stats_cases_show_what_their_tag_says`), `tests/presser/evaluation/test_cases.py` |
| AC16 | 9 | `tests/presser/test_service.py::test_logs_carry_no_names_with_full_match_stats` |
| AC17 | 14 | `tests/test_docs.py::test_backlog_32_after_spec_015`, `::test_decisions_cover_full_match_stats`, `::test_deployment_lists_migration_0013` |

## Steps

- [x] 1. **Storage and migration `0013` with its backfill (AC2, the AC1 storage).** `iterations: 0`
      Files: `backend/app/fpl/models/snapshots.py`, `backend/app/fpl/models/__init__.py`
      (export `PlayerFixtureStat`), new `backend/migrations/versions/0013_match_stats.py`
      (revision `0013`, down `0012`), `backend/tests/db/test_migrations.py`.
      - Test first:
        `test_match_stats_migration_backfills_from_the_latest_payload_and_downgrades`.
        1. Upgrade to `0012` and run `_apply_bootstrap_at_revision`.
        2. Insert by SQL two fixtures of the first gameweek, one fixture of the second, and
           `player_gameweek_result` rows of both gameweeks for two players.
        3. Insert two `event/{gw1}/live` payloads and two `fixtures` payloads of the first
           gameweek, with different `fetched_at` and different values (bps 10 and 12, xG
           "0.10" and "0.80"). Insert no payload for the second gameweek.
        4. Upgrade to `0013` and assert:
           - the first gameweek's `live_stats` equals the later payload's `stats`;
           - `player_fixture_stat` holds the later payload's `(fixture, player, identifier,
             side, value)` rows, `bps` included, and only those of the first gameweek's
             fixtures;
           - the second gameweek's rows have `live_stats` null and no fixture stat rows;
           - the other tables are unchanged (`table_contents`).
        5. `downgrade -1` removes the column and the table, and leaves the rest unchanged.
      - The existing migration tests read the columns of the current metadata, so they need
        two changes:
        - add `MATCH_STATS_COLUMNS = {"live_stats"}` to every `excluded` set and to
          `BEFORE_OWNERSHIP`, the same way `BEFORE_GAMEWEEK_RANK` is used;
        - take `player_fixture_stat` out of `PRE_0010_TABLES` and out of the `tables` set of
          `test_gameweek_rank_migration_adds_empty_column_and_downgrades`.
      - Code:
        - the model and the migration exactly as in Approach;
        - the migration's DDL matches the model, so that `test_models_match_migration`
          stays green.
      - After the tests are green, apply it to the local development database, which the
        approvals allow, and print counts only.
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_migrations.py`, then
      `cd backend && env -u OPENROUTER_API_KEY DATABASE_URL=postgresql+psycopg://presser:presser@localhost:5432/presser uv run alembic upgrade head`,
      then
      `cd backend && env -u OPENROUTER_API_KEY uv run python -c "from sqlalchemy import create_engine, text; c=create_engine('postgresql+psycopg://presser:presser@localhost:5432/presser').connect(); print(c.execute(text('SELECT gameweek_fpl_id, count(*) FILTER (WHERE live_stats IS NOT NULL), count(*) FROM player_gameweek_result GROUP BY 1 ORDER BY 1')).all()); print(c.execute(text('SELECT f.gameweek_fpl_id, count(*) FROM player_fixture_stat s JOIN fixture f ON f.season = s.season AND f.fpl_id = s.fixture_fpl_id GROUP BY 1 ORDER BY 1')).all())"`.
      The expected output is every GW1–5 row with `live_stats`, and fixture stat rows for
      GW1–5. If the database is unreachable, end with `RESULT: ESCALATE`, `KIND: tooling`.

- [x] 2. **The results job stores both (AC1, the AC9 storage).** `iterations: 0` Files: `backend/app/fpl/schemas.py`,
      `backend/app/fpl/results.py`, `backend/tests/fpl/test_results_sync.py`.
      - Tests first:
        - `test_results_stored_and_archived` also asserts `row.live_stats == el["stats"]`
          for every element. That includes the data-only fields `ict_index`, `tackles`,
          `recoveries`, `clearances_blocks_interceptions`, `defensive_contribution`,
          `in_dreamteam` and `expected_goal_involvements`.
        - `test_fixture_stats_stored_per_player_and_fixture`: for every gameweek-1 fixture
          of the recorded payload, the stored `(identifier, element, value, side)` set
          equals the payload's `stats` lists, `bps` included. Fixtures of other gameweeks
          get no rows.
        - `test_resync_replaces_fixture_stats`: a second sync with one yellow card removed
          from the payload leaves no row for it.
        - `test_rerun_is_idempotent` and `test_double_gameweek` stay green.
      - Code as in Approach (Results job).
      Automatic verification: `cd backend && uv run pytest -q tests/fpl`

- [x] 3. **Loaders and test-world helpers.** `iterations: 0` Files: `backend/app/presser/facts/load.py`,
      `backend/tests/presser/helpers.py`, `backend/tests/presser/test_facts_load.py`.
      - Tests first:
        - `test_live_line_parsed_from_live_stats`: the xG strings become `Decimal`, and
          `live` is `None` without `live_stats`;
        - `test_fixture_bonus_loaded_per_fixture`;
        - `test_club_fixture_counts_count_home_and_away`.
      - Code: `LiveLine`, `MatchResult.live`, `FixtureBonus`, `load_fixture_bonus` and
        `load_club_fixture_counts`, as in Approach.
      - `World` changes, keeping today's defaults so that existing tests do not change:
        - `result(..., live: dict | None = None)` stores `live_stats`;
        - a helper `live(goals=0, xg="0.00", xa="0.00", xgc="0.00", **extra) -> dict` builds
          a `live_stats` object with the keys `LiveLine` reads;
        - `fixture_stat(fixture_id, player_id, identifier, value, side="h")`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_load.py tests/presser/test_facts_gameweek.py tests/presser/test_facts_pitch.py tests/presser/test_facts_moments.py`

- [x] 4. **Fact sheet v4: breakdown and xG flags on pitch items and captain picks (AC3, the
      AC8 flags, AC9, AC10, AC11 rules).** `iterations: 1` Files: `backend/app/presser/facts/schema.py`, new
      `backend/app/presser/facts/stats.py`, `backend/app/presser/facts/gameweek.py`
      (`captaincy` takes the per-player stat facts), `backend/app/presser/facts/pitch.py`,
      `backend/app/presser/facts/build.py`, `backend/app/presser/facts/checks.py`, new
      `backend/tests/presser/test_facts_stats.py`, `backend/tests/presser/test_facts_checks.py`,
      `backend/tests/presser/test_facts_gameweek.py` (`test_sheet_v3_sections_in_order`
      becomes `test_sheet_v4_sections_in_order` with `version == 4`).
      - Tests first, in `test_facts_stats.py`, through `World` + `build_fact_sheet`:
        - `test_breakdown_sums_explain_over_a_double_gameweek`: 2 + 1 goals in two fixtures
          give one `goals_scored` entry of value 3, the summed points, and no zero-point
          `minutes` entry;
        - `test_pitch_item_and_captain_carry_the_breakdown`: an item's breakdown sums to its
          `points`, and a captain's to `base_points`, the vice's when he stepped in;
        - `test_big_miss_boundary_070_and_069`: 2 points with xG 0.70 is true, 0.69 is false,
          and 3 points with 0.90 is false;
        - `test_goal_from_nothing_boundary_014_and_015`: one goal with xG 0.14 is true, 0.15
          is false;
        - `test_xg_flags_false_without_condition_and_null_without_stats`;
        - `test_data_only_stats_absent_from_the_schema`: walk `FactSheet.model_json_schema()`
          (with `$defs`) and assert that no property is named `ict_index`, `influence`,
          `creativity`, `threat`, `tackles`, `recoveries`,
          `clearances_blocks_interceptions`, `defensive_contribution`, `in_dreamteam` or
          `expected_goal_involvements`;
        - `test_gameweek_without_match_stats_builds_a_v4_sheet`. The world has no
          `live_stats`, an `explain` with a 14-point differential haul that decides the
          winner, and a red card. The sheet is version 4; every breakdown and xG field is
          `None`; the 014 facts are as before: the item kinds are
          `["differential_haul"]` / `["red_card"]`, and there is a `player` moment of
          swing 14. The check list is `[]`. Steps 5 and 7 extend this test.
      - In `test_facts_checks.py`, the new parametrised `test_inconsistent_v4_fact_is_reported`
        (`problems_after` with `recompute=False`) starts with these rules:
        - "breakdown: does not sum to the player's points", for a pitch item and for a
          captain pick;
        - "xG: a flag does not match the thresholds": `big_miss` true at 3 points,
          `goal_from_nothing` true with xG 0.15, and a flag set while `expected_goals` is
          null.
        - `test_chips_and_pitch_sections_consistent_when_listed` stays green with a
          breakdown added to its pitch item.
      - Code:
        - the schema and `version: Literal[4] = 4`, as in Approach;
        - `stats.py` with the constants, `breakdown(result)` and
          `player_stat_facts(result) -> PlayerStats` (no `points` argument: it uses
          `result.points`);
        - `build.py` computes `player_facts = {id: player_stat_facts(r) for id, r in
          match_results.items()}` and passes it to `captaincy` and `build_pitch`;
        - the two check rules.
      Automatic verification: `cd backend && uv run pytest -q tests/presser tests/content --ignore=tests/presser/evaluation/test_eval_set.py`

- [x] 5. **Hat-trick and keeper wall (AC7, AC10, the AC11 rule).** `iterations: 1` Files:
      `backend/app/presser/facts/matches.py`, `backend/app/presser/facts/pitch.py`,
      `backend/app/presser/facts/schema.py` (`PitchKind`), `backend/app/presser/facts/checks.py`,
      `backend/tests/presser/test_facts_stats.py`, `backend/tests/presser/test_facts_matches.py`,
      `backend/tests/presser/test_facts_checks.py`.
      - Tests first:
        - `test_hat_trick_needs_three_goals_in_one_fixture`: 3 goals in one fixture is a
          hat-trick, 2 + 1 across a double gameweek is not;
        - `test_keeper_wall_needs_two_save_points_in_one_fixture`: a goalkeeper with saves
          (6, 2) is a wall and (5, 1) is not, and a defender never is;
        - `test_hat_trick_of_an_uncounted_player_not_listed`: benched or in nobody's squad;
          plus a template player (in the squads of half the league) benched by every
          manager, whose item is still listed for `template_haul` but without `hat_trick`;
        - `test_new_kinds_carry_differential_flags`: the global and league flags are set as
          for a haul;
        - `test_new_kinds_rank_with_hauls`: a hat-trick ranks by points among the hauls,
          after the cards;
        - in `test_facts_matches.py`, `test_hat_trick_and_keeper_wall_need_live_stats`;
        - `test_gameweek_without_match_stats_builds_a_v4_sheet` gets a 3-goal fixture and
          a 6-save goalkeeper, and still has no new kinds.
      - `test_inconsistent_v4_fact_is_reported` gets "pitch: a hat-trick without 3 goals"
        (breakdown `goals_scored` below 3, or no breakdown) and "pitch: a keeper wall without
        2 save points".
      - Code as in Approach (hat-trick and keeper wall).
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_stats.py tests/presser/test_facts_matches.py tests/presser/test_facts_pitch.py tests/presser/test_facts_checks.py`

- [x] 6. **Key moments: breakdown, swing per stat and the decisive stat (AC3 moments, AC4,
      AC5, the AC11 rules).** `iterations: 1` Files: `backend/app/presser/facts/moments.py`,
      `backend/app/presser/facts/stats.py` (`moment_breakdown(entries, ahead_multiplier,
      behind_multiplier, margin) -> list[MomentStat]`), `backend/app/presser/facts/build.py`,
      `backend/app/presser/facts/checks.py`, `backend/tests/presser/test_facts_moments.py`,
      `backend/tests/presser/test_facts_checks.py`.
      - Tests first:
        - `test_player_moment_carries_breakdown_and_multipliers`: the breakdown sums to the
          player's points, and `ahead_points` / `behind_points` equal it × each multiplier;
        - `test_breakdown_swing_and_decisive_boundary`: a bonus of 3 on the winner's captain
          held ×1 by the runner-up gives swing 3, which is decisive at margin 3 and not at
          margin 4;
        - `test_trailing_captains_yellow_card_decides_at_margin_1`: at margin 1, the
          runner-up's captain has 1 point (minutes 2, yellow −1) and the winner does not own
          him. The moment's swing is −2, it is listed, the `yellow_cards` entry has swing 2
          and is decisive, and the `minutes` entry is not;
        - `test_red_card_entry_never_decisive`: the `red_cards` entry has its swing and
          `decisive` false, and the `red_card` moment is still listed;
        - `test_decisive_moment_ranks_by_its_decisive_swing`;
        - `test_player_moment_without_live_stats_has_no_breakdown`: the 014 listing rule
          applies.
      - `test_inconsistent_v4_fact_is_reported` gets:
        - "breakdown: does not sum to the player's points" for a moment (against the
          multipliers);
        - "key moments: a breakdown swing does not match its points and multipliers";
        - "key moments: decisive does not match the swing and the margin": true below the
          margin, false at it, and true on a `red_cards` entry;
        - "key moments: a swing is below its margin": a `player` moment below the margin
          with no decisive entry. The rule now exempts a `player` moment with a decisive
          entry, and so does the "swing does not match the points" rule's `swing <= 0`
          part;
        - "key moments: breakdown or multipliers on a kind that has none".
      - Code as in Approach (decisive stat, moment order). `_pair_moments` gets
        `player_facts` for the breakdown.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_moments.py tests/presser/test_facts_pitch.py tests/presser/test_facts_checks.py tests/presser/test_facts_stats.py`

- [x] 7. **Bonus missed by one BPS (AC6, AC10, the AC11 rule).** `iterations: 1` Files: `backend/app/presser/facts/stats.py`
      (`bonus_near_misses(bonus_by_fixture) -> list[NearMiss(player_id, fixture_id,
      missed_bonus)]`), `backend/app/presser/facts/moments.py`, `backend/app/presser/facts/build.py`
      (`load_fixture_bonus` over the gameweek's fixture IDs), `backend/app/presser/facts/schema.py`
      (kind), `backend/app/presser/facts/checks.py`, `backend/tests/presser/test_facts_moments.py`,
      `backend/tests/presser/test_facts_checks.py`, `backend/tests/presser/test_facts_stats.py`.
      - Tests first, with `fixture_stat` rows:
        - `test_bonus_missed_by_one_listed`: the bonus BPS is 30/28/25, the runner-up's
          ×1 player has 24 and no bonus, and the margin is 1. The moment names the player
          and his club, with `missed_bonus` 1 and swing 1;
        - `test_bonus_missed_by_two_not_listed`: 23;
        - `test_bonus_missed_by_leading_managers_player_not_listed`;
        - `test_bonus_missed_below_margin_not_listed`: swing 1 at margin 2. The same player
          as the trailing manager's captain, giving swing 2, is listed;
        - `test_shared_lowest_bonus_place_gives_the_shared_bonus`: BPS 30/28/28 with bonus
          3/2/2, and a near miss at 27 gives `missed_bonus` 2;
        - `test_bonus_missed_by_a_player_both_managers_count_uses_the_difference`: the
          trailing manager's captain (×2) is also counted ×1 by the leader, the missed bonus
          is 1 and the margin 2. The swing is 1, so no moment is listed (the bonus would have
          reached the leader too; the 2026-10-11 key-moment rule "swing ≥ margin" binds);
        - `test_bonus_in_the_other_fixture_of_a_double_gameweek`: a bonus in fixture 1 and
          a near miss in fixture 2 is listed;
        - `test_gameweek_without_match_stats_builds_a_v4_sheet` gets near-miss rows, with
          the players' `live_stats` absent, and has no such moment.
      - `test_inconsistent_v4_fact_is_reported` gets:
        - "key moments: a missed bonus swing is below its margin";
        - "key moments: a swing does not match the points", where for this kind
          `swing != behind_points − ahead_points` or the points are not `missed_bonus` ×
          each multiplier;
        - "key moments: missed_bonus on a kind that has none".
      - Code as in Approach (`bonus_missed_by_one`). The `_check_key_moments` swing rule
        branches on the kind. The `KeyMoment.swing` comment in `schema.py` (today
        "ahead_points - behind_points") says that for `bonus_missed_by_one` the points are
        the bonus each manager would have gained, and the swing is `behind_points -
        ahead_points`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_moments.py tests/presser/test_facts_stats.py tests/presser/test_facts_checks.py`

- [x] 8. **xGC on a lost clean sheet (AC8 xGC, a check rule).** `iterations: 1` Files:
      `backend/app/presser/facts/moments.py`, `backend/app/presser/facts/build.py`
      (`load_club_fixture_counts`), `backend/app/presser/facts/checks.py`,
      `backend/tests/presser/test_facts_moments.py`, `backend/tests/presser/test_facts_checks.py`.
      - Tests first:
        - `test_lost_clean_sheet_carries_xgc_of_most_minutes`: a goalkeeper with 90 minutes
          and xGC 1.40, and a defender with 70 minutes and 1.10, give 1.40;
        - `test_lost_clean_sheet_xgc_null_in_double_gameweek`: the club has two fixtures in
          the gameweek;
        - `test_lost_clean_sheet_xgc_null_without_stats`.
      - `test_inconsistent_v4_fact_is_reported` gets "key moments: expected goals conceded
        outside a lost clean sheet".
      - Code as in Approach (lost clean sheet xGC).
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_moments.py tests/presser/test_facts_checks.py`

- [x] 9. **Writer prompt version 6 and log privacy (AC12, AC16).** `iterations: 1` Files:
      `backend/app/content/prompts/presser_writer.md`, `backend/tests/content/test_presser_content.py`,
      `backend/tests/presser/test_writer.py`, `backend/tests/presser/test_service.py`.
      - Tests first:
        - `test_writer_prompt_v6_rules` replaces `test_writer_prompt_v5_rules`. It asserts
          `version == 6`, keeps every v5 needle and header-order assertion, and adds the
          needles `breakdown`, `decisive`, `bonus_missed_by_one`, `missed_bonus`,
          `hat_trick`, `keeper_wall`, `big_miss`, `goal_from_nothing`,
          `expected_goals_conceded`, `punchline` and `never a bare xG`.
        - `test_prompt_version_label` and the stored version in `test_service.py` use `@6`.
        - New `test_logs_carry_no_names_with_full_match_stats` in `test_service.py`, modelled
          on `test_logs_carry_no_names_with_match_facts`. Its world gives the player
          `live_stats` and a hat-trick `explain`, and a near-miss `fixture_stat`, so the
          stored facts hold a breakdown and the `hat_trick` kind. On both the success and
          the failure path, `caplog.text` holds none of the manager names, "Synthetic XI",
          the club or the player.
      - Prompt v6 (English instructions, Polish examples of phrasing allowed):
        - `breakdown`: the player's point-bearing stats. The writer may name them ("dublet
          i 3 bonusy") and never sums or recomputes them.
        - A `decisive` entry of a `player` key moment may be named as the cause ("3 bonusy
          kapitana dały mu Managera kolejki"). A moment can be listed with a negative total
          `swing` when one stat decided it: then name the stat, not the player's total.
        - `bonus_missed_by_one`: `behind`'s player missed `missed_bonus` by one BPS;
          `ahead_points` and `behind_points` are what each manager would have gained, and
          `swing` is how much closer it would have brought `behind`. Never say `behind`
          lost those points.
        - `hat_trick` (3+ goals in one match: never call goals across two matches a
          hat-trick) and `keeper_wall` (6+ saves in one match).
        - xG flags as a punchline only: a captain's blank with `big_miss` is bad luck
          ("pech"), not form; `goal_from_nothing` is a goal out of nothing; never a bare
          list of xG numbers. `expected_goals_conceded` on a lost clean sheet may say the
          goal was coming. `null` means unknown, so leave it out.
        - Every v5 rule stays.
      Automatic verification: `cd backend && uv run pytest -q tests/content/test_presser_content.py tests/presser/test_writer.py tests/presser/test_service.py`

- [x] 10. **Judge prompt version 5 (AC13).** `iterations: 0` Files: `backend/app/content/prompts/presser_judge.md`,
      `backend/tests/content/test_presser_content.py`, `backend/tests/presser/evaluation/test_runner.py`.
      - Tests first:
        - `test_judge_prompt_v5_checks_full_match_stats` replaces the v4 test. It asserts
          `version == 5`, every v4 needle, and the needles `breakdown`, `decisive`,
          `missed a bonus by one BPS`, `hat-trick`, `keeper wall`, `big miss`,
          `goal from nothing` and `xG`.
        - `test_recorded_decisive_stat_and_bps_verdicts_counted`, modelled on
          `test_recorded_swing_and_streak_verdicts_counted`:
          - the sheet has a `player` moment whose `yellow_cards` entry is decisive (swing 2,
            margin 1), and a `bonus_missed_by_one` moment for another player;
          - the presser says that the player's bonus decided it, and that a third player
            missed the bonus by one BPS;
          - the recorded verdict labels both claims `unsupported`, so the faithfulness is
            0.0;
          - the sheet JSON sent to the judge holds `"decisive": true` and
            `"bonus_missed_by_one"`.
      - Prompt v5:
        - "What is a claim" adds a breakdown stat (value and points), a decisive stat ("his
          yellow card decided it"), a bonus missed by one BPS, a hat-trick, a keeper wall
          and an xG flag (big miss, goal from nothing, xGC).
        - "Labels" adds that the following are `unsupported`:
          - a stat named as decisive whose entry is not `decisive`, or that belongs to
            another player or pair;
          - a missed bonus not in a `bonus_missed_by_one` moment;
          - a hat-trick without the kind;
          - a quoted xG number that differs from the sheet.
      Automatic verification: `cd backend && uv run pytest -q tests/content/test_presser_content.py tests/presser/evaluation/test_runner.py`

- [x] 11. **Style examples rewritten (AC14).** `iterations: 1` Files: `backend/app/content/presser_style_examples.md`,
      `backend/tests/content/test_presser_content.py`.
      - Test first: `test_style_examples_eight_headers_no_shared_sentence` keeps every
        current assertion, and adds that each example's `⚽` paragraph:
        - names a decisive stat: it contains one of `bonus`, `żółt`, `defcon` or `bps`,
          casefolded;
        - has an xG punchline: it contains one of `xg`, `pech` or `z niczego`, casefolded.
      - Rewrite the 3 examples in Polish, with made-up managers, leagues and numbers:
        - each `⚽` paragraph uses one decisive stat (a captain's bonus, a yellow card, a
          DefCon) and one xG punchline;
        - the examples share no sentence and stay under 2500 characters;
        - the rest of their structure is kept: a team name standing in for a manager, a
          chips paragraph, "autosub", sparing slang;
        - the header note says "spec 015".
      Automatic verification: `cd backend && uv run pytest -q tests/content/test_presser_content.py`

- [x] 12. **Evaluation set v4: layout and synthetic cases (AC15, the synthetic part).** `iterations: 0` Files:
      `git mv backend/evals/presser/v3 backend/evals/presser/v4`,
      `backend/app/presser/evaluation/cases.py`, `backend/app/presser/evaluation/cli.py` (help
      texts "set v4"), `backend/evals/presser/v4/cases.jsonl`,
      `backend/tests/presser/evaluation/{test_eval_set,test_cases}.py`.
      - Tests first:
        - `test_eval_set.py`:
          - 41 cases, 20 dev (5 real, 15 synthetic) and 21 test (5 real, 16 synthetic);
          - every case validates, and the names come from the pools, as today;
          - the new `test_full_stats_cases_show_what_their_tag_says`, one assertion per tag:
            - `captain_bonus_decides_winner`: a winner-pair `player` moment with a decisive
              `bonus` entry;
            - `yellow_card_decides_pair`: a decisive `yellow_cards` entry at margin ≤ 2;
            - `defcon_decides_pair`: a decisive `defensive_contribution` entry;
            - `bonus_missed_flips_swap`: a rivalry-pair `bonus_missed_by_one` moment;
            - `hat_trick`: an item of that kind;
            - `differential_keeper_wall`: a `keeper_wall` item with a differential flag true;
            - `captain_big_miss`: a captain pick with `big_miss` true;
            - `goal_from_nothing`: an item with the flag true;
            - `lost_clean_sheet_xgc`: a `clean_sheet_lost` moment with
              `expected_goals_conceded` set;
            - `double_gameweek_bps`: one player with a `bonus` breakdown entry and a
              `bonus_missed_by_one` moment;
            - `no_match_stats`: every breakdown and xG field null, and no new kind or moment.
          - every v3 tag assertion stays.
        - `test_cases.py`: the counts follow the new limits.
      - Code in `cases.py`:
        - the paths point to `v4`;
        - `MIN_CASES = 39`, `MAX_CASES = 43`, `MIN_SYNTHETIC_CASES = 31`,
          `MIN_TEST_CASES = 20`;
        - `EDGE_TAGS` gets the 11 tags above.
      - Cases are written with a scratchpad helper, not committed, that validates each one
        with `FactSheet` and `check_fact_sheet` before writing:
        - convert the 20 v3 synthetic cases to version 4. Their tags and texts are kept.
          Every pitch item, captain pick and `player` moment gets a breakdown that sums to
          its points (and the multipliers), plus xG fields consistent with the flags.
          `syn-ownership-unknown` keeps its null ownership;
        - add 11 new cases, 5 dev / 6 test, with GW > 1 and 1–2 previous pressers written
          for the project, under the ids `syn-<tag-with-dashes>`;
        - the 10 real v3 cases cannot validate until step 13, so the helper drops them here
          and the set briefly has 31 cases.
      - Only `test_cases.py`, `test_building.py`, `test_cli.py` and the synthetic-only
        assertions run green in this step.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation/test_cases.py tests/presser/evaluation/test_building.py tests/presser/evaluation/test_cli.py`

- [x] 13. **Set v4: the real cases rebuilt (AC15, the real part).** `iterations: 0` Files:
      `backend/evals/presser/v4/cases.jsonl`, plus `REAL_CASES` in `cases.py` only if GW6
      joins.
      - With `DB=postgresql+psycopg://presser:presser@localhost:5432/presser`, from
        `backend/`:
        1. Check GW6, printing booleans and counts only:
           `env -u OPENROUTER_API_KEY uv run python -c "from sqlalchemy import create_engine, text; c=create_engine('$DB').connect(); print(c.execute(text('SELECT finished AND data_checked FROM gameweek WHERE fpl_id = 6 ORDER BY season DESC LIMIT 1')).scalar_one(), c.execute(text('SELECT count(*) FROM manager_gameweek WHERE gameweek_fpl_id = 6 AND has_team')).scalar_one())"`.
        2. If the database is unreachable, end with `RESULT: ESCALATE`, `KIND: tooling`.
        3. If GW6 is finished and collected (true and a count above 0), end with
           `RESULT: ESCALATE`, `KIND: decision`, before building. Including GW6 commits its
           overall ranks while they are current (the spec 013 privacy gate). Options:
           - (a) build GW1–6 and accept the current GW6 ranks under pseudonyms;
           - (b) build GW1–5 only (recommended);
           - (c) wait until GW7 is finished.
        4. Otherwise run
           `env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run python -m app.presser.evaluation build-cases --gameweeks 1-5 --force`.
           It must report 10 real cases, 31 synthetic cases kept and no missing history.
        5. Under option (a), use `--gameweeks 1-6`, set `REAL_CASES = 12` and
           `MAX_CASES = 45`, and adjust the split counts in `test_eval_set.py`.
      - Step 1 must have backfilled the local database, or the real cases come out with null
        breakdowns. Check that at least one real case has a non-null breakdown, printing a
        count only.
      - If the leak check trips, end with `RESULT: ESCALATE`, `KIND: decision`, naming the
        case id only. Never weaken the check or print the name.
      - Never run `python -m app.presser facts` against the development database: it prints
        real names. `v4/history.jsonl` stays as it is.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation`

- [x] 14. **Documents (AC17).** `iterations: 0` Files: `docs/BACKLOG.md`, `docs/DECISIONS.md`, `docs/DEPLOYMENT.md`,
      `docs/ROADMAP.md`, `backend/tests/test_docs.py`.
      - Tests first, in `test_docs.py`:
        - `test_backlog_32_after_spec_014` becomes `test_backlog_32_after_spec_015`: the row
          holds `spec 015 is merged`, `writer prompt version 6`, `judge prompt version 5`,
          `set v4` and `inflection`;
        - `test_decisions_cover_full_match_stats`: DECISIONS holds `live_stats`,
          `player_fixture_stat`, `decisive`, `bonus_missed_by_one`, `hat_trick`,
          `keeper_wall`, `0.70`, `0.15`, `ICT` and `version 4`;
        - `test_deployment_lists_migration_0013`: DEPLOYMENT holds `0013`, `live_stats`,
          `player_fixture_stat` and `backfill`.
      - BACKLOG #32 reads "on writer prompt version 6, judge prompt version 5 and set v4 of
        spec 015", with the trigger "spec 015 is merged".
      - DEPLOYMENT, after the `0012` note: migration `0013` adds `live_stats` and
        `player_fixture_stat` and backfills them from the latest stored payload of each
        gameweek, with no FPL request. A gameweek without a payload stays without stats
        until its next results sync. It runs through the pre-deploy and downgrades cleanly.
      - DECISIONS, rows dated on the day of implementation:
        1. All FPL match stats are stored: `live_stats` per player and gameweek, and
           `player_fixture_stat` per fixture, backfilled from the latest payloads.
        2. The decisive-stat rule: an entry's swing = its points × the multiplier
           difference, and it is decisive when that is at least the margin, except for the
           own-kind stats. A player moment is listed on a decisive stat.
        3. The BPS rule: exactly 1 below the lowest bonus-earning BPS, no bonus in that
           fixture, and the swing (the missed bonus × the multiplier difference) at least
           the margin.
        4. The new thresholds: a hat-trick is 3+ goals in one fixture, a keeper wall is 2+
           save points in one fixture, a big miss is ≤ 2 points with xG ≥ 0.70, and a goal
           from nothing is a goal with xG < 0.15. The xGC belongs to the group's player
           with the most minutes, and is null in a double gameweek. Fact sheet version 4,
           writer prompt 6, judge prompt 5, set v4 (41 cases).
        5. Data-only stats: ICT, influence, creativity, threat, tackles, recoveries, CBI,
           the DefCon count field, `in_dreamteam` and xGI. Defence collapse and Dream Team
           were not chosen.
      - ROADMAP Stage 3 gets a new item before the model comparison, ticked `[x]` because
        it merges with this PR: "Full FPL match stats: points breakdown, decisive stats, a
        bonus missed by one BPS, hat-tricks and keeper walls, xG flags, evaluation set v4
        (spec: [015](../specs/015-presser-full-match-stats/SPEC.md))". The #32 line changes
        to "after spec 015".
      Automatic verification: `cd backend && uv run pytest -q tests/test_docs.py`

## Risks and traps

- **Interim red set test** (steps 4–12). The step commands ignore
  `tests/presser/evaluation/test_eval_set.py` until step 13. Do not "fix" it by loosening
  `FactSheet`, for example with `Literal[3, 4]`.
- **Old migration tests.** `table_contents` selects the current metadata's columns and
  tables. Every earlier migration test must exclude `live_stats` and `player_fixture_stat`
  (step 1), or it fails at an old revision with "column does not exist".
- **Backfill source.** Use the payload stored *with* the gameweek (`gameweek_fpl_id = gw`),
  taking the latest `fetched_at`, then the highest `id`. A later gameweek's `fixtures`
  payload also lists earlier fixtures, but the SPEC names the gameweek's own latest fetch.
- **Decimals.** xG arrives as a string. Parse it with `Decimal(str(value))` and compare it
  with `Decimal("0.70")` and `Decimal("0.15")`, never as a float, because 0.69 and 0.70 sit
  on the boundary (AC8). Pydantic serialises a `Decimal` as a JSON string ("0.74"), and
  evaluation cases written by the helper must use that form.
- **A negative-swing moment.** A `player` moment listed on a decisive stat breaks the old
  "swing ≥ margin, swing > 0" invariant. Both check rules gain the exemption in step 6. The
  writer is told to name the stat, not the total.
- **A BPS of 0.** FPL leaves a player with 0 BPS out of the list, so a player who would sit at
  `L − 1 = 0` is not seen. It is an accepted blind spot: no moment rather than a guessed one.
- **Double gameweeks.** The breakdown and xG cover the gameweek, while hat-tricks, keeper
  walls and BPS work per fixture. The xGC is null when the club has two fixtures. One player
  missing a bonus by one in both fixtures is told once, through the dedup.
- **Club changes.** The `bonus_missed_by_one` club is the player's current club, as in spec
  014's events.
- **Privacy.**
  - Player and club names are public. Manager and team names never reach logs (the step 9
    test).
  - Run every database command with `env -u OPENROUTER_API_KEY`, and print counts or
    booleans only.
  - Never print a real fact sheet in the session.
- **Prompt size.** Breakdowns add a few hundred tokens per sheet. The caps stay one constant
  each (SPEC open question), and #32's previews may move them.
- **Polish content.** Kinds, identifiers and keys stay English. Polish words live only in
  `app/content/` and `evals/` (`tests/presser/test_no_polish_literals.py`).

## End-to-end verification

### Automatic (performed by /pipeline:implement)

1. `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
   is fully green.
2. Real data, counts only. With `DB=postgresql+psycopg://presser:presser@localhost:5432/presser`,
   run from `backend/`:
   `env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run python -c "import os; from sqlmodel import Session, select; from app.db.engine import make_engine; from app.fpl.models import League; from app.presser.facts import build_fact_sheet, check_fact_sheet; from app.presser.store import current_season; s=Session(make_engine(os.environ['DATABASE_URL'])); season=current_season(s); ids=[l.fpl_id for l in s.exec(select(League).where(League.season==season))]; print([(n, g, f.version, sum(i.breakdown is not None for i in f.pitch.items), len(f.pitch.items), sum(p.breakdown is not None for p in f.captaincy.picks), len(f.captaincy.picks), sum(e.decisive for m in f.key_moments for e in (m.breakdown or [])), sum(m.kind == 'bonus_missed_by_one' for m in f.key_moments), sum(k in ('hat_trick','keeper_wall') for i in f.pitch.items for k in i.kinds), sum(bool(i.big_miss) for i in f.pitch.items), check_fact_sheet(f)) for n, i in enumerate(ids) for g in range(1,6) for f in [build_fact_sheet(s, season, i, g, {})]])"`.
   Expect:
   - every version is 4;
   - every check list is `[]`;
   - the breakdown is set on every pitch item and captain pick (the two counts are equal);
   - at least one decisive stat across GW1–5.

   The other counts are recorded in the plan as found. The command prints counts only, and
   `n` is the league ordinal.
3. `uv run python -m app.presser.evaluation --help` and `build-cases --help` name set v4.
4. `uv run pytest -q tests/presser/evaluation/test_eval_set.py` passes on the committed v4 set.
5. Run
   `cd backend && env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run alembic downgrade -1 && env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run alembic upgrade head`
   against the local development database, then the step 1 count query again. The counts
   must be the same as after step 1, which shows that the backfill re-runs cleanly.

Result (2026-10-11, implement):

1. `verify.command` is green: ruff check and format, 1902 tests passed.
2. Real data, GW1–5 of both local leagues (10 sheets): every version is 4 and every check list
   is `[]`. The breakdown is set on every pitch item and captain pick (pitch 7/7, 8/8, 3/3, 8/8,
   8/8 in league 0 and 8/8, 8/8, 5/5, 7/7, 8/8 in league 1; captains 5/5 and 22/22). Decisive
   stats per sheet: 9, 0, 4, 6, 2 and 12, 15, 9, 2, 0 (at least one in eight of ten sheets).
   `bonus_missed_by_one` moments: 0 in all ten sheets. Hat-tricks and keeper walls: 5 sheets with
   one each. Big-miss flags on pitch items: 2, 0, 1, 0, 0 and 2, 0, 0, 0, 1.
3. `python -m app.presser.evaluation --help` and `build-cases --help` name set v4.
4. `pytest -q tests/presser/evaluation/test_eval_set.py` passes on the committed v4 set
   (74 evaluation tests green).
5. `alembic downgrade -1` and `upgrade head` on the local database: the counts after step 1 and
   after the re-run are the same: live stats on 610, 626, 654, 659 and 667 rows for GW1–5 (all
   rows), and 714, 712, 680, 711 and 685 fixture stat rows for GW1–5.

Step 13: GW6 was not finished (`False`, 0 managers with a team), so the real cases are GW1–5,
10 real and 31 synthetic cases, no missing history. All 10 real cases have a breakdown, 8 have a
decisive stat and none has a missed-bonus moment.

### Manual (performed by the owner)

- Read the three rewritten style examples in `backend/app/content/presser_style_examples.md`.
  Pass when: you approve the text at the final review. It should have eight sections and
  natural slang, and each "⚽ Z boiska" paragraph should use one decisive stat and one xG
  punchline that sound right to you.
- Generate one real preview with your `.env`: in `backend/`, run
  `uv run python -m app.presser preview --league <your league ID> --gameweek 5`.
  Pass when:
  - the printed presser is at most 2500 characters;
  - its "⚽ Z boiska" paragraph names at least one stat (a bonus, a card, a DefCon or a
    hat-trick) that matches what you know of GW5;
  - no xG number appears as a bare list.

## Definition of Done

- [x] all steps ticked
- [x] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` fully green
- [x] end-to-end verification (automatic) performed, result recorded here
- [x] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, `docs/BACKLOG.md` and `docs/DEPLOYMENT.md` updated
- [x] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

- 2026-10-11 — final-review — `gate` — Which final review findings are accepted? (F2 ruling: routine stats such as `minutes` are never `decisive`; amend AC4 in the SPEC. F5 ruling: the near-miss swing counts the real reduction of the gap, i.e. the multiplier difference when both managers count the player; keep the code and amend AC6 in the SPEC.) — `accepted`: F1, F2, F3, F4, F5, F6, F7, F8, F9, F10, F11, F12, F13; `rejected`: none
## Review log

2026-10-11, plan review (pipeline:plan-review).

Findings:

- `major` — Approach and step 5 relied on the existing "needs an owner unless template" rule
  in `pitch.py` to list `hat_trick` and `keeper_wall` only for a counted player. That rule
  lets a template player benched by every manager through for `template_haul`, so he would
  carry `hat_trick` against AC7 ("listed only when the player counted for a league
  manager"). Changed: the Approach states that `pitch.py` appends either kind only when
  `holders` is not empty, and `test_hat_trick_of_an_uncounted_player_not_listed` gets the
  benched template player case.
- `minor` — `bonus_missed_by_one` uses `swing = behind_points − ahead_points`, the reverse
  of the `KeyMoment.swing` comment ("ahead_points - behind_points"), and its points are
  what each manager would have gained, not points scored. Changed: step 7 updates the schema
  comment, and the step 9 writer rule explains both fields and forbids saying `behind` lost
  those points.
- `minor` — The plan reads AC6's "bonus × his multiplier" as the multiplier difference
  between the two managers, which differs from the literal SPEC only when both managers
  count the player. The difference is the reading the binding 2026-10-11 key-moment rule
  (swing ≥ margin) requires, so it is kept, but no test pinned it. Changed: step 7 adds
  `test_bonus_missed_by_a_player_both_managers_count_uses_the_difference`, and the AC
  matrix lists it.

Checked and found correct:

- Coverage: AC1–AC17 each have steps and a named proving test; the matrix matches the steps.
  Every SPEC Scope 9 check rule maps to a step (4: breakdown sum, xG; 5: hat-trick, keeper
  wall; 6: entry swing, decisive; 7: missed-bonus swing; 8: xGC kind).
- Decisive-stat arithmetic against `moments.py`: `swing = ahead_points − behind_points =
  points × (ahead − behind multiplier)`; the AC5 test (runner-up's captain, minutes 2,
  yellow −1, margin 1) gives a total swing of −2 and a yellow entry swing of 2, as planned.
  The two `_check_key_moments` rules that assume `swing ≥ margin > 0` get the exemption.
- Storage: `player_gameweek_result` and the new table share the `(season, player)` FK
  pattern; the recorded `fixtures` payload has 110 stats lists for GW1 fixtures, every
  element is in the recorded bootstrap, and the live `stats` keys match the SPEC.
  `StrictModel` uses `extra="ignore"`, so `extra="allow"` on `LiveStats` plus `model_dump()`
  keeps every field. `apply_fixtures` runs before the fixture-stat insert.
- Migration tests: every older test derives its tables from `PRE_0010_TABLES` or the
  metadata and its exclusions from `BEFORE_OWNERSHIP` or the listed `excluded` sets, so the
  step 1 changes cover all of them.
- Decimal handling: `Strict` is `extra="forbid"` only (no strict mode), the service stores
  `model_dump(mode="json")`, and cases load with `model_validate_json`, so xG strings
  round-trip.
- Evaluation set counts: v3 has 10 real (5/5) and 20 synthetic (10/10) cases; plus 11 new
  (5/6) gives 41 cases, 20 dev and 21 test, inside the new limits. `build-cases` has
  `--gameweeks` and `--force`; the interim 31-case file is read by no test in step 12.
- The GW6 gate escalation follows the spec 014 precedent (the spec 013 privacy waiver
  covered spec 013 only); the owner decides, so it does not contradict the SPEC's "plus GW6".
- The E2E count query uses existing APIs (`build_fact_sheet` signature, `current_season`,
  `make_engine`, `check_fact_sheet` export); the check messages are fixed strings, so it
  prints no names.
- Compliance: Polish only in `app/content/` and `evals/` (`test_no_polish_literals.py`
  scans `app/presser` only), tests first in every step, fake LLM, DECISIONS rows recorded.
  No conflict with the 2026-10-09/11 presser decisions.
- Owner summary: no new dependency; the migration is flagged and accepted in SPEC → Owner
  decisions. Manual items both have `Pass when:`, and neither can be automated (owner's
  credentials, real names, taste).
- Language: the plan is in English.

The plan is ready: the one major gap is fixed in place, every AC has a proving test, and the
only migration is accepted by the owner up front with no new dependency.

## Deviations

- `minor` — `build_key_moments` takes `player_facts`, `near_misses` and `club_fixture_counts` as
  optional arguments (default empty) instead of required ones, because an existing test calls it
  positionally and the plan does not change that test.
- `minor` — `_replace_fixture_stats` in the results job skips stat entries of players missing from
  the `player` table (the migration backfill skips them the same way), so a stray FPL element
  cannot fail the whole sync on the foreign key.
- `minor` — `matches.py` got a small `_stat_facts` helper for the hat-trick and keeper-wall facts,
  and the four thresholds live in `facts/stats.py` as the plan says (`matches.py` imports them).
- `minor` — the new `MomentStat`, `PlayerStats` and `StatPoints` models and every new schema field
  were added in step 4 together, including the `bonus_missed_by_one` kind and the multiplier
  fields that steps 6 and 7 fill, to keep the schema in one change.
- `minor` — the 11 new synthetic cases are built from converted v3 synthetic cases (their
  captain picks, pitch items, key moments and, for the rivalry case, one table total and the swap
  gap adjusted), not from scratch; `syn-no-match-stats` is the v3 lost-clean-sheet case without
  any new stats.
- `minor` — several new tests added beyond the plan's list (breakdown order, the consistent v4
  stats and moments, a fixture without bonus rows, an xGC from a player with stats), and the
  `test_service` log test also asserts a `bonus_missed_by_one` moment in the stored facts.

## Final review

2026-10-11, final review (pipeline:final-review, report mode). Three independent perspectives
ran as separate subagents: compliance, quality and tests. Full `verify.command` is green
(ruff check and format, 1902 passed). Of 14 mutations of key code, 11 failed a test.

AC → evidence:

| AC | Evidence | Test |
|----|----------|------|
| AC1 | `fpl/results.py:sync_results` (`live_stats`), `_replace_fixture_stats`; `fpl/schemas.py:LiveStats` (`extra="allow"`), `FixtureStat`; `models/snapshots.py:PlayerFixtureStat` | `test_results_sync.py::test_results_stored_and_archived`, `::test_fixture_stats_stored_per_player_and_fixture`, `::test_resync_replaces_fixture_stats` |
| AC2 | `migrations/versions/0013_match_stats.py` upgrade/downgrade | `test_migrations.py::test_match_stats_migration_backfills_from_the_latest_payload_and_downgrades` (see F6), `::test_models_match_migration` |
| AC3 | `facts/stats.py:breakdown`, `player_stat_facts`; `gameweek.py:captaincy`; `pitch.py:build_pitch`; `moments.py:_pair_moments`; `schema.py` version 4 | `test_facts_stats.py::test_breakdown_sums_explain_over_a_double_gameweek`, `::test_pitch_item_and_captain_carry_the_breakdown`; `test_facts_moments.py::test_player_moment_carries_breakdown_and_multipliers` |
| AC4 | `facts/stats.py:moment_breakdown` | `test_facts_moments.py::test_breakdown_swing_and_decisive_boundary` |
| AC5 | `moments.py:_pair_moments` filter; `stats.py:OWN_KIND_STATS` | `test_facts_moments.py::test_trailing_captains_yellow_card_decides_at_margin_1`, `::test_red_card_entry_never_decisive` |
| AC6 | `stats.py:bonus_near_misses`; `moments.py:_pair_moments` near-miss loop (multiplier-difference reading, see F5) | `test_facts_moments.py::test_bonus_missed_by_one_listed`, `::…_by_two_not_listed`, `::…_by_leading_managers_player_not_listed`, `::…_below_margin_not_listed`, `::test_shared_lowest_bonus_place_gives_the_shared_bonus`, `::test_bonus_missed_by_a_player_both_managers_count_uses_the_difference` |
| AC7 | `matches.py:_stat_facts` (per fixture, live-gated); `pitch.py:build_pitch` (`if holders`) | `test_facts_stats.py::test_hat_trick_needs_three_goals_in_one_fixture`, `::test_keeper_wall_needs_two_save_points_in_one_fixture`, `::test_hat_trick_of_an_uncounted_player_not_listed`, `::test_new_kinds_carry_differential_flags`, `::test_new_kinds_rank_with_hauls` |
| AC8 | `stats.py:player_stat_facts`; `moments.py:_conceded_xg`; `load.py:load_club_fixture_counts` | `test_facts_stats.py::test_big_miss_boundary_070_and_069`, `::test_goal_from_nothing_boundary_014_and_015`, `::test_xg_flags_false_without_condition_and_null_without_stats`; `test_facts_moments.py::test_lost_clean_sheet_*` (see F8) |
| AC9 | whole `stats` object in `live_stats`; no data-only field in the schema | `test_results_sync.py::test_results_stored_and_archived`; `test_facts_stats.py::test_data_only_stats_absent_from_the_schema` |
| AC10 | live-gating in `stats.py`, `matches.py`, `moments.py` (see F3) | `test_facts_stats.py::test_gameweek_without_match_stats_builds_a_v4_sheet`, `::test_bonus_near_miss_without_live_stats_is_not_a_moment` |
| AC11 | `checks.py:_check_player_stats`, `_check_moment_breakdown`, `_missed_bonus_matches`, pitch and xGC rules | `test_facts_checks.py::test_inconsistent_v4_fact_is_reported`, `::test_inconsistent_v4_moment_is_reported`, `::test_inconsistent_missed_bonus_is_reported` |
| AC12 | `content/prompts/presser_writer.md` version 6 | `test_presser_content.py::test_writer_prompt_v6_rules`; `test_writer.py::test_prompt_version_label`; `test_service.py` (`presser_writer@6`) |
| AC13 | `content/prompts/presser_judge.md` version 5 | `test_presser_content.py::test_judge_prompt_v5_checks_full_match_stats`; `test_runner.py::test_recorded_decisive_stat_and_bps_verdicts_counted` |
| AC14 | `content/presser_style_examples.md` (1391, 1516, 1374 characters) | `test_presser_content.py::test_style_examples_eight_headers_no_shared_sentence`; the owner's approval is pending (manual) |
| AC15 | `evals/presser/v4/cases.jsonl` (41: 10 real, 31 synthetic; 20 dev / 21 test) | `tests/presser/evaluation/test_eval_set.py`, `test_cases.py` |
| AC16 | no new logging of names | `test_service.py::test_logs_carry_no_names_with_full_match_stats` |
| AC17 | `docs/BACKLOG.md` #32, `docs/DEPLOYMENT.md` 0013, `docs/DECISIONS.md` five rows (see F4) | `test_docs.py::test_backlog_32_after_spec_015`, `::test_decisions_cover_full_match_stats`, `::test_deployment_lists_migration_0013` |

Findings:

- **F1** `worth-fixing` — `backend/app/presser/facts/load.py:347`, `stats.py:18-28`, `build.py:184` — FPL's per-stat `points_modification` in `explain` is dropped. A manual points correction on any counted player makes the breakdown no longer sum to `total_points`, so `check_fact_sheet` fails and `build_fact_sheet` raises `FactSheetError`, which blocks the whole league's presser. — Add `points_modification` to `StatLine.points`, or set `breakdown` to `None` when the sum differs from `total_points`; add a test.
- **F2** `worth-fixing` — `backend/app/presser/facts/stats.py:58` — `minutes` (and other routine entries such as `goals_conceded`) get `decisive` at the usual margins of 1–2. In set v4 `minutes` is decisive 25 times, against 10 for `bonus` and 1 for `yellow_cards`. The writer may then name "his appearance points decided it", judge v5 accepts it, and such moments use up the cap of 5. This follows AC4 literally, so a change amends the SPEC rule. — Leave `minutes` out of `decisive` (a non-decisive set next to `OWN_KIND_STATS`), update the DECISIONS row and the AC4 test.
- **F3** `worth-fixing` — `backend/app/presser/facts/load.py:315-323` — `_live_line` indexes `live_stats` with `[]`. A stored object missing `goals_scored`, `expected_goals`, `expected_assists` or `expected_goals_conceded` (for example a field FPL renames or drops) raises `KeyError` from the build, instead of the "no stats" degradation that Scope 8 / AC10 promise. `LiveLine.goals_scored` is also never read. — Return `None` when a key is missing, drop `goals_scored`, and add a test.
- **F4** `worth-fixing` — `docs/DECISIONS.md:88`, `:90` — The rule at `DECISIONS.md:6` says a change to a row on `main` is a new row that names the one it supersedes. The decisive-stat row changes the 2026-10-11 key-moment rule (a `player` moment can now be listed below the margin), and the thresholds row replaces fact sheet v3 / writer 5 / judge 4 / set v3. Neither names what it supersedes. — Add "amends the 2026-10-11 key-moment row" and "supersedes the version 3, writer 5, judge 4 and set v3 parts of the 2026-10-11 rows", and pin both in `test_docs.py`.
- **F5** `worth-fixing` — `SPEC.md` AC6 vs `backend/app/presser/facts/moments.py:172-199` — AC6 says "bonus × his multiplier ≥ margin". The code uses the multiplier difference: a trailing captain (×2) also held ×1 by the leader, with a missed bonus of 1 at margin 2, is not listed. The plan review chose this reading because the key-moment rule (swing ≥ margin) binds, but it is neither in `## Deviations` nor confirmed by the owner. — Owner confirms; then record an owner-decision entry and amend the AC6 wording.
- **F6** `worth-fixing` — `backend/tests/db/test_migrations.py:809-814` — The early fetch is always inserted before the late one, so `ORDER BY …, id DESC` alone picks the same row. Dropping `fetched_at DESC` from the backfill kept the test green (mutation survived). — Insert the late payload first (lower `id`), or add a fetch with an earlier `fetched_at` and a higher `id`.
- **F7** `worth-fixing` — `backend/tests/fpl/test_results_sync.py:89-122` — The deviation that skips fixture stat entries of players missing from `player` (`results.py`, `if entry.element in known_players`) has no test. Without the filter, a stray FPL element fails the whole sync on the FK; removing it kept the tests green. — A sync test with an unknown element, asserting the sync succeeds and stores no row for it.
- **F8** `worth-fixing` — `backend/tests/presser/test_facts_moments.py:544-570` — In the xGC "most minutes" test, the 90-minute keeper also wins the name tie-break ("Keeper" < "Left"). Code that ignores minutes and sorts only by name stays green (mutation survived). — Give the 90-minute player a name that sorts last.
- **F9** `nit` — `backend/app/presser/facts/checks.py:311-320` — The hat-trick and keeper-wall checks read the gameweek-summed breakdown, so a wrongly added `hat_trick` for 2 + 1 goals across a double gameweek passes `check_fact_sheet`. The builder is correct (per fixture). — Document the limit in a comment, or carry per-fixture values on the item.
- **F10** `nit` — `backend/app/content/prompts/presser_writer.md:59` — "is the one stat whose own swing…" suggests a single decisive entry, but several can be decisive in one moment. — "is a stat whose own swing…".
- **F11** `nit` — `backend/tests/presser/test_facts_checks.py:539-566` — The check tests never pin swing == margin (base margin 10, swings 2 and 12). Changing `>=` to `>` in `checks.py` was caught only by the committed eval set data. — Add a case at swing == margin with `decisive=False`.
- **F12** `nit` — `backend/tests/presser/test_facts_moments.py:387` — The "not flagged again" rule of AC5 is tested only for `red_cards`. Dropping `own_goals` or `penalties_missed` from `OWN_KIND_STATS` would go unnoticed. — Parametrise over red card, own goal and missed penalty.
- **F13** `nit` — `backend/tests/presser/test_facts_moments.py` (no test) — Nothing tests the plan's claim that a player missing a bonus by one in both fixtures of a double gameweek is told once through the dedup. — Add a test with near misses in both fixtures, asserting one moment.

Rejected:

- "AC14 needs the owner's approval of the style examples": this is the manual Gate 2 item, not a defect. The examples were checked for consistency (decisive swings match their margins, all under 2500 characters).

Left out: 11 nit findings

