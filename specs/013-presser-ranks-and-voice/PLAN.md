# PLAN 013 — Presser ranks and natural voice

## Owner summary

- **Approach:** The league sync stores FPL's gameweek rank in a new nullable
  `manager_gameweek.gameweek_rank` column (migration 0012). The fact sheet becomes version 2:
  winners and flops carry their GW rank, the season records are judged by GW rank, personal
  season bests and worsts are flagged, and a new `overall` section, with its rules in plain
  Python, reports threshold crossings (1M / 100k / 10k), every rise inside the top 10k and
  moves of at least 2×. The writer gets prompt version 3, rewritten style examples with a sixth
  "🌍 Overall" paragraph, and temperature 0.8 on the models that accept one. The evaluation set
  moves to v2 with 4 new rank cases, the judge prompt treats ranks as claims, and `review`
  records an inflection-error count that `summary` averages per model.
- **Main risks:** (1) The real cases of set v2 can be rebuilt only after your manual league
  sync of GW1–5 fills the GW ranks. The implementer runs every other step first and stops with
  an escalation at that point if the ranks are still empty. (2) Set v2 commits the real overall
  ranks of your leagues' managers under pseudonyms. The SPEC treats FPL ranks as public data,
  but an overall rank that is still current can be looked up on FPL's overall standings pages,
  which would undo the pseudonym for that manager. After GW6 the GW1–5 ranks are no longer
  current, so step 14 builds and commits the real cases only once GW6 is finished, and
  otherwise escalates (wait, or accept committing current ranks). (3) The temperature change has no effect on the current default writer
  (`openai/gpt-6-luna` accepts no temperature) until BACKLOG #32 picks a model.
- **New dependency:** no.
- **Data migration:** yes. Migration `0012` adds the nullable column `gameweek_rank` to
  `manager_gameweek`. Existing rows stay empty, and the downgrade drops the column. Accepted in
  SPEC → "Owner decisions".
- **Manual scenarios for the owner:** 2. You read a real GW5 presser preview with GW ranks and
  the Overall paragraph, and you approve the 3 rewritten style examples at the final review.

## Approach

**What the plan rests on (read in full / searched):**

- `specs/013-presser-ranks-and-voice/SPEC.md` — read in full, including `## Owner decisions`.
- `docs/CONVENTIONS.md` — read in full. Product content lives in `app/content/`. LLM steps are
  tested against a fake model. External APIs are tested on synthetic recorded payloads. Tests
  come first or with the code. Logs carry no names.
- `docs/DECISIONS.md` — searched for "presser" / "Presser". This found the 2026-10-09 rows on
  the fact sheet (SQL-computed, LLM only writes), the presser table and previous pressers (at
  most 2), the evaluation set v1 and its pass rule, and net points / `nth_of_season`. None of
  them conflicts with the plan. The new rows extend them.
- `docs/ROADMAP.md` — searched for "presser". Stage 3 item 1 is ticked and already links spec
  013, so nothing in the roadmap changes.
- `docs/BACKLOG.md` — searched for "#32" / "32 ". This found the row of #32 (trigger "spec 012
  is merged") that step 15 edits.
- `docs/DEPLOYMENT.md` — searched for "presser", "migration", "0011". Step 13 (Presser)
  lists migration 0011. Migration 0012 goes beside it.
- `docs/PROJECT.md` — searched for "FR-3", "section", "overall". FR-3.1 and FR-3.3 stand, and
  nothing in PROJECT names the five sections, so PROJECT does not change.
- `specs/012-league-presser/PLAN.md` — searched for the steps on `build-cases`, the synthetic
  cases and the end-to-end checks. The plan reuses its procedure for the local database and
  the history texts.
- Code read in full: `backend/app/presser/facts/{schema,season,build,load,table,errors,__init__}.py`,
  `backend/app/presser/{writer,render,config,service}.py`, `backend/app/presser/store.py`
  (`previous_pressers`), `backend/app/presser/evaluation/{cases,building,judge,runner,summary,cli}.py`,
  `backend/app/fpl/{leagues,schemas}.py`, `backend/app/fpl/models/leagues.py`,
  `backend/app/llm/{chat,settings}.py`, `backend/app/llm/model_settings.toml`, the prompts
  `presser_writer.md` and `presser_judge.md`, `presser_style_examples.md`, and the head of
  `presser_glossary.toml`. Read in part: `backend/app/presser/facts/gameweek.py` (winners,
  flops and `to_score`, lines 1–90; the rest is not changed) and the presser wiring in
  `backend/app/worker/cli.py` and `backend/app/presser/cli.py`.
- Tests read: `tests/presser/helpers.py` (`World`), `tests/presser/test_facts_table.py`,
  `tests/presser/test_writer.py`, `tests/presser/test_worker_run.py`,
  `tests/content/test_presser_content.py`, `tests/presser/evaluation/{test_eval_set,factories}.py`
  and the test names of the other evaluation tests, `tests/fpl/fakes.py`
  (`synthetic_league`, `table_contents`), `tests/fpl/test_league_sync.py` (the picks test),
  `tests/db/test_migrations.py` (the 0008 and 0011 tests), `tests/llm/test_providers.py`.

**Design.**

1. **Collector.** `EntryHistory` (`app/fpl/schemas.py`) gains `rank: int | None = None`.
   FPL sends `null` before a gameweek is ranked, and the old recorded payloads have no `rank`.
   `_sync_entry` writes it into the new `ManagerGameweek.gameweek_rank`, and
   `_no_team_gameweek` writes `None`. The upsert already overwrites every non-key column, so a
   re-sync of a past gameweek fills an empty value. The GW rank comes from the picks endpoint
   the sync already calls per gameweek. `entry/{id}/history/` → `current[]` holds the same
   number, but reading it would add a second source for one fact.
2. **Pure rank rules** go into a new `app/presser/facts/ranks.py`, which imports nothing from
   the presser. Both the builder and `check_fact_sheet` use it, so the check recomputes the
   rules instead of copying them, and `schema.py` ↔ builder imports stay acyclic:
   ```python
   THRESHOLDS = (1_000_000, 100_000, 10_000)
   TOP_TIER = 10_000
   def thresholds_entered(now: int, before: int | None, first_gameweek: bool) -> list[int]
   def thresholds_left(now: int, before: int | None) -> list[int]
   def is_notable(now: int, before: int | None, first_gameweek: bool) -> bool
   def move_ratio(now: int, before: int) -> Fraction   # before / now for a rise, now / before for a fall
   ```
   - "Inside T" is `rank <= T`.
   - Entered T: `now <= T < before`. At GW1 (`first_gameweek`, `before is None`) it is every
     T with `now <= T`.
   - Left T: `before <= T < now`.
   - When `before is None` after GW1, there is no movement and no threshold, because the
     previous rank is unknown.
   - Notable: a threshold entered or left; or a rise with `now <= TOP_TIER`; or a rise with
     `2 * now <= before`; or a fall with `now >= 2 * before`. Integer arithmetic, no floats.
   - The ratio is a `fractions.Fraction`, so ties are exact.
3. **Fact sheet version 2** (`schema.py`):
   ```python
   SECTIONS = ("winners", "flops", "captaincy", "bench_transfers_chips", "table", "overall")
   class ManagerScore: ... + gameweek_rank: int | None = None
   class Record(Strict): manager: str; gameweek: int; gameweek_rank: int; net_points: int
   class PersonalRank(Strict): manager: str; gameweek_rank: int; ranked_gameweeks: int
   class Season: ... + personal_bests: list[PersonalRank]; personal_worsts: list[PersonalRank]
   class OverallRow(Strict):
       manager: str
       overall_rank: int
       previous_overall_rank: int | None
       movement: int | None        # previous - now; positive = climbed; None without a previous rank
       entered: list[int]          # thresholds in THRESHOLDS order
       left: list[int]
       notable: bool
   class Overall(Strict):
       rows: list[OverallRow]      # notable first, then by move ratio desc (rows without a
                                   # previous rank after those with one), then overall rank, then manager
       biggest_climbers: list[str] # among notable rises, the highest ratio; ties give several
       biggest_fallers: list[str]
   class FactSheet: version: Literal[2] = 2; ... overall: Overall (after `table`)
   ```
   - `empty_sections` lists `overall` when no row is `notable`. This covers AC7: no
     threshold, no rise inside the top 10k and no 2× move gives no notable row.
   - The biggest climbers and fallers come only from notable rows. A non-notable climber
     would contradict an empty section. AC6's example (300k → 100k beats 3M → 1.4M) uses two
     notable rows.
   - Rows exist for every manager with an overall rank this gameweek, as the SPEC says ("per
     manager"), so the writer can see who stayed put.
4. **Season records by GW rank** (`season.py`). `_records` takes the rows with a
   `gameweek_rank`. The best is the minimum rank and the worst the maximum, ties give several
   records, and the records are sorted by (gameweek, manager). A personal best or worst is
   flagged when the manager has at least 3 gameweeks with a rank up to and including this
   one, and this gameweek's rank is strictly better (best) or strictly worse (worst) than
   every earlier ranked gameweek. With strict comparison a manager is never both, and a rank
   that only equals an earlier one is not a new best.
5. **Loading.** `MemberRow` gains `overall_rank` and `gameweek_rank` (both `int | None`), and
   `load_member_rows` reads them. A new `app/presser/facts/overall.py` holds
   `build_overall(current, previous, names, gameweek) -> Overall`. `build.py` calls it with
   the `previous` rows it already computes for the table.
6. **`check_fact_sheet`** adds the rank checks (AC8):
   - Every rank is positive.
   - In each overall row, `movement == previous − now` when the previous rank is known and
     `None` otherwise. `entered`, `left` and `notable` equal `ranks.py`'s result.
   - The climbers and fallers are exactly the recomputed sets.
   - All best records share one rank, and so do all worst records. The best rank is at most
     the worst rank. No record is from a gameweek after the sheet's.
   - Every winner's and flop's known GW rank lies within [best, worst]; a known GW rank with
     no best or worst record is itself a problem (the records are built from the same rows).
   - Every personal best or worst has `ranked_gameweeks >= 3`, a personal best is no better
     than the league best, and a personal worst is no worse than the league worst.
   - `_managers_named` adds the overall rows and the personal ranks.
7. **Writer temperature.** `build_chat_model(config, temperature: float = 0.0)`. The
   factory sends it only when the model (and its fallback) accept a temperature, as today.
   `app/presser/config.py` gets `WRITER_TEMPERATURE = 0.8` and
   `writer_chat_model(config: LlmConfig) -> ChatModelSpec`. Three writer call sites use it:
   `app/worker/cli.py` (`make_presser_runtime`), `app/presser/cli.py` (`make_runtime`) and
   the writer in `app/presser/evaluation/cli.py` (`_caller` gains a `temperature` argument,
   `0.0` for the judge). Every other caller keeps the default 0.
   - Rejected: a `temperature` key per row in `model_settings.toml`. That row describes the
     model, while the temperature belongs to the step, and the same model can be the writer
     and the judge.
8. **Content.** Prompt `presser_writer.md` → version 3, `presser_judge.md` → version 2, and
   `presser_style_examples.md` is rewritten. All Polish text stays in `app/content/`. The
   six section headers are the five of spec 012 plus `🌍 *Overall:*`.
9. **Evaluation set v2.** `git mv backend/evals/presser/v1 backend/evals/presser/v2`: the
   history texts and the pseudonym pool carry over unchanged. Set v1 is version-1 JSON that
   the new schema cannot load, and git keeps it.
   - The step that changes the schema converts the committed cases to version 2 mechanically
     (GW ranks `null`, no records, an empty `overall`), so the suite stays green at every
     step.
   - The last evaluation steps replace the real cases with a rebuild from the database and
     add the rank cases.
   - Composition: 10 real + 10 synthetic = 20 cases, 10 dev / 10 test.
10. **Inflection count.** After the rating and the note, `review` asks for the number of
    inflection errors: a whole number ≥ 0, where Enter means 0. It stores it as
    `style.inflection_errors`. `summary` averages it per run (`RunSummary.inflection_errors`)
    and prints it, and runs rated before this field average only the rated pressers that
    carry it. There is no threshold and the pass rule does not change.

Existing patterns to reuse:

- the upsert in `app/db/upsert.py`;
- the migration test shape of `test_ownership_migration_keeps_rows_and_downgrades` (a nullable
  column added, contents unchanged, downgrade drops it) in `tests/db/test_migrations.py`;
- `World` in `tests/presser/helpers.py` to seed the presser facts;
- `FakeChatModel` (`tests/extraction/fakes.py`) and `StructuredCaller` for the writer and the
  judge;
- the evaluation factories in `tests/presser/evaluation/factories.py`;
- `synthetic_league` in `tests/fpl/fakes.py` for the sync and the worker simulation.

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 2 | `tests/fpl/test_league_sync.py::test_gameweek_rank_stored_and_none_without_team`, `::test_resync_fills_an_empty_gameweek_rank`; `tests/fpl/test_schemas.py::test_entry_history_rank_optional` |
| AC2 | 1, 15 | `tests/db/test_migrations.py::test_gameweek_rank_migration_adds_empty_column_and_downgrades`, `::test_models_match_migration`; `tests/test_docs.py::test_deployment_lists_migration_0012` |
| AC3 | 3 | `tests/presser/test_facts_ranks.py::test_winners_and_flops_carry_gameweek_rank`, `::test_unknown_gameweek_rank_is_null` |
| AC4 | 3 | `tests/presser/test_facts_table.py::test_records_by_gameweek_rank_with_ties`, `::test_records_skip_rows_without_gameweek_rank` |
| AC5 | 4 | `tests/presser/test_facts_ranks.py::test_personal_best_and_worst_need_three_ranked_gameweeks` |
| AC6 | 5 | `tests/presser/test_ranks.py` (pure rules: threshold edges, ratio edges, GW1) and `tests/presser/test_facts_ranks.py::test_overall_section_rows_and_movers` |
| AC7 | 5 | `tests/presser/test_facts_ranks.py::test_overall_empty_when_nothing_notable` |
| AC8 | 6 | `tests/presser/test_facts_checks.py` (one test per inconsistency) |
| AC9 | 8 | `tests/content/test_presser_content.py::test_writer_prompt_v3_rules`, `tests/presser/test_writer.py::test_prompt_version_label`, `tests/presser/test_service.py::test_generated_presser_stored_with_usage` (`presser_writer@3`), `::test_previous_pressers_latest_two_sent_before_gameweek` (kept) |
| AC10 | 9 | `tests/content/test_presser_content.py::test_style_examples_six_headers_no_shared_sentence` (owner approval: manual) |
| AC11 | 7 | `tests/llm/test_providers.py::test_temperature_argument_sent_only_where_accepted`, `::test_temperature_only_where_listed` (default 0 kept); `tests/presser/test_config.py::test_writer_chat_model_temperature`; `tests/presser/evaluation/test_cli.py::test_writer_gets_temperature_and_judge_zero` |
| AC12 | 13, 14 | `tests/presser/evaluation/test_eval_set.py` (composition 20 cases, 10/10, every case validates, names from the pool, no ID-like number outside rank fields, new tags), `tests/presser/evaluation/test_building.py::test_build_pseudonymises_overall_and_personal_names` |
| AC13 | 12 | `tests/presser/evaluation/test_cli.py::test_review_records_inflection_errors`, `tests/presser/evaluation/test_summary.py::test_summary_averages_inflection_errors` |
| AC14 | 10 | `tests/content/test_presser_content.py::test_judge_prompt_checks_ranks`, `tests/presser/evaluation/test_runner.py::test_recorded_rank_verdict_counted` |
| AC15 | 11 | `tests/presser/test_worker_run.py::test_logs_carry_no_names_or_text` (extended with rank values) |
| AC16 | 15 | `tests/test_docs.py::test_backlog_32_after_spec_013_with_inflection`, `::test_decisions_cover_gameweek_rank_and_overall` |

## Steps

- [x] 1. **Migration 0012 and the model column (data migration, its own step) (AC2).** `iterations: 0`
      - Add `gameweek_rank: int | None = None` to `ManagerGameweek` in
        `backend/app/fpl/models/leagues.py`, after `overall_rank`.
      - Add `backend/migrations/versions/0012_gameweek_rank.py` (revision `"0012"`,
        down_revision `"0011"`, header like 0011): `op.add_column("manager_gameweek",
        sa.Column("gameweek_rank", sa.Integer(), nullable=True))`; downgrade
        `op.drop_column("manager_gameweek", "gameweek_rank")`. No data backfill.
      - Test first, in `backend/tests/db/test_migrations.py`:
        `test_gameweek_rank_migration_adds_empty_column_and_downgrades`. It upgrades to
        `0011`, seeds the reference data with `_apply_bootstrap_at_revision` and, by SQL, one
        `manager` row and two `manager_gameweek` rows: one with a team and one without. Then:
        - Upgrade to `0012`. The column exists and is nullable, every value is NULL, and
          `table_contents(exclude=... | {"gameweek_rank"})` is unchanged.
        - Downgrade to `0011`. The column is gone and the contents are unchanged.
      - Trap: `table_contents` selects the columns of the current SQLModel metadata, so every
        existing migration test that reads `manager_gameweek` at a revision before 0012 would
        fail on the missing column. Add a `BEFORE_GAMEWEEK_RANK = {"gameweek_rank"}` set and
        union it into every `exclude=` / `excluded` used at a pre-0012 revision
        (`BEFORE_OWNERSHIP`, the `excluded` sets, and `DEFAULT_EXCLUDE` where a test passes
        none).
      - Also apply the migration to the local development database (allowed by the owner)
        with `DB=postgresql://presser:presser@localhost:5432/presser`, from `backend/`:
        `env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run alembic upgrade head`. If the
        database is not reachable, carry on: step 14 checks again and escalates there.
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_migrations.py`

- [x] 2. **Collector stores the GW rank (AC1).** `iterations: 0`
      - `backend/app/fpl/schemas.py`: `EntryHistory.rank: int | None = None`.
      - `backend/app/fpl/leagues.py`: `"gameweek_rank": eh.rank` in the `_sync_entry` upsert,
        and `"gameweek_rank": None` in `_no_team_gameweek`.
      - `backend/tests/fpl/fakes.py`: `synthetic_league` adds `"rank": 200000 + entry_id %
        1000 * 10 + gw` to `entry_history`. Synthetic data, with a different formula from
        `overall_rank`.
      - Tests first:
        - `tests/fpl/test_league_sync.py::test_gameweek_rank_stored_and_none_without_team`
          uses `no_team_for` for one manager. The manager with a team stores the payload's
          `rank`, and the no-team row stores `None`.
        - `::test_resync_fills_an_empty_gameweek_rank` syncs GW1, sets `gameweek_rank = NULL`
          on the rows by SQL (as a pre-migration row would be), re-syncs GW1 and asserts the
          payload value.
        - Add `"gameweek_rank"` to the field loop of the existing picks test, with the
          payload's `rank` added.
        - `tests/fpl/test_schemas.py::test_entry_history_rank_optional`: a payload without
          `rank` parses to `None`, and with `rank: 12345` to 12345.
      Automatic verification: `cd backend && uv run pytest -q tests/fpl tests/worker tests/presser/test_worker_run.py`

- [x] 3. **Fact sheet version 2: schema, loading, winners' and flops' GW rank, records by GW rank, set conversion (AC3, AC4).** `iterations: 0`
      - Add `backend/app/presser/facts/ranks.py` with the rules from Approach §2.
      - `schema.py`:
        - Add everything from Approach §3 at once: the `version` literal 2, `ManagerScore.gameweek_rank`,
          `Record.gameweek_rank`, `PersonalRank`, the `Season.personal_*` lists, `OverallRow`,
          `Overall`, `FactSheet.overall` and `"overall"` in `SECTIONS`.
        - `empty_sections()`: add `"overall": not any(row.notable for row in sheet.overall.rows)`.
        - `_managers_named`: add the overall rows, the climbers and fallers, and the personal
          ranks.
      - `load.py`: add `overall_rank` and `gameweek_rank` to `MemberRow` and read them in
        `load_member_rows`.
      - `gameweek.py`: `to_score` sets `gameweek_rank=row.gameweek_rank`.
      - `season.py`: `_records` by GW rank (Approach §4), with the rows without a rank
        skipped. `build.py` still leaves `overall` empty in this step.
      - `tests/presser/helpers.py`: `World.gw` gains the keyword arguments
        `overall_rank: int | None = None` and `gameweek_rank: int | None = None`.
      - Tests first:
        - New `tests/presser/test_facts_ranks.py::test_winners_and_flops_carry_gameweek_rank`
          and `::test_unknown_gameweek_rank_is_null`.
        - In `tests/presser/test_facts_table.py`, replace `test_records` with
          `test_records_by_gameweek_rank_with_ties`. It is built so that the best GW rank is
          not the most net points (60 points at GW rank 50 000 beats 90 points at GW rank
          900 000), checks the gameweek, rank and net points of each record, and includes a
          tie that gives two records. Add `test_records_skip_rows_without_gameweek_rank`:
          without any rank both lists are empty.
      - Keep the suite green:
        - `git mv backend/evals/presser/v1 backend/evals/presser/v2`.
        - Point `EVALS_DIR / "v1"` → `"v2"` in `app/presser/evaluation/cases.py`, and change
          `v1` to `v2` in the CLI help texts.
        - Convert the 16 cases with a one-off script in the scratchpad, not committed. It
          sets `version` 2, adds `gameweek_rank: null` to every winner and flop, empties
          `best_gameweek` / `worst_gameweek`, adds an empty `overall`, and appends
          `"overall"` to `empty_sections`.
        - `tests/presser/evaluation/factories.py` and the `facts()` of
          `tests/presser/test_writer.py` need only the derived `empty_sections` to include
          `overall`. The factories already compute it, and the writer test lists it by hand.
      Automatic verification: `cd backend && uv run pytest -q tests/presser`

- [x] 4. **Personal season bests and worsts (AC5).** `iterations: 0`
      - `season.py`: `build_season` fills `personal_bests` / `personal_worsts`
        (Approach §4), sorted by rank (best: ascending; worst: descending), then manager.
      - Test first: `tests/presser/test_facts_ranks.py::test_personal_best_and_worst_need_three_ranked_gameweeks`.
        - A manager with 2 ranked gameweeks and a new best at GW2 is not flagged.
        - A manager with ranks 500k, 400k and 100k is flagged best at GW3 with
          `ranked_gameweeks == 3`.
        - A new worst is flagged.
        - A gameweek without a rank does not count towards the 3, and a rank equal to an
          earlier one is not flagged.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_ranks.py tests/presser/test_facts_table.py`

- [x] 5. **The `overall` section (AC6, AC7).** `iterations: 0`
      - Add `backend/app/presser/facts/overall.py` with `build_overall(current, previous,
        names, gameweek) -> Overall`, from Approach §2–3. `build.py` sets `sheet.overall`
        before `empty_sections`.
      - Tests first:
        - New `tests/presser/test_ranks.py`, on the pure rules:
          - 10 000 is inside the top 10k and 10 001 is not, as an entry and as a drop.
          - A rise inside the top 10k is notable (8 000 → 7 990).
          - 4 000 000 → 2 000 000 is notable and 4 000 000 → 2 000 001 is not. This pair
            crosses no threshold, so it tests the ratio alone.
          - The SPEC's own pair, 2 000 000 → 1 000 000 notable and 2 000 000 → 1 000 001 not.
          - A fall from 2 000 000 to 4 000 000 is notable and one to 3 999 999 is not.
          - 400k → 200k and 30k → 15k are notable.
          - 1 100 000 → 1 000 001 is not notable.
          - At GW1, rank 9 000 has entered all three thresholds and rank 2 000 000 none.
          - After GW1, a missing previous rank gives no thresholds and is not notable.
          - `move_ratio` ranks 300k → 100k above 3M → 1.4M.
        - `tests/presser/test_facts_ranks.py::test_overall_section_rows_and_movers`: four
          managers through `World`. It checks `overall_rank` / `previous_overall_rank` /
          `movement` / `entered` / `left`, and the row order (notable first). The biggest
          climber is the 300k → 100k manager, not the 3M → 1.4M one, and the biggest faller
          is a manager who dropped out of the top 1M (900k → 1.9M). At GW1, `movement` is
          `None` and `entered` follows the rank.
        - `::test_overall_empty_when_nothing_notable`: only small moves outside the top 10k
          give `"overall"` in `empty_sections` and empty climbers and fallers, while the rows
          are still listed.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_ranks.py tests/presser/test_facts_ranks.py tests/presser`

- [x] 6. **`check_fact_sheet` for the rank facts (AC8).** `iterations: 0`
      - `schema.py`: `check_fact_sheet` adds the checks of Approach §6, using `ranks.py`.
      - Test first: new `tests/presser/test_facts_checks.py`. It builds a consistent v2 sheet
        through the evaluation `factories.sheet()`, extended with ranks, records, personal
        ranks and an overall row, and asserts `check_fact_sheet == []`. Then one test per
        mutation, each asserting a problem:
        - a GW rank of 0;
        - a negative overall rank;
        - `movement` ≠ previous − now;
        - `entered` without the matching ranks;
        - `left` set although no threshold was left;
        - `notable` flipped;
        - a climber who is not the highest ratio;
        - two best records on different ranks;
        - a best rank worse than the worst;
        - a record from a later gameweek;
        - a winner's GW rank better than the best record;
        - a winner's known GW rank with empty `best_gameweek` / `worst_gameweek`;
        - a personal best with `ranked_gameweeks` 2;
        - an overall row naming a manager outside the table.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_checks.py tests/presser`

- [x] 7. **Writer temperature (AC11).** `iterations: 0`
      - `backend/app/llm/chat.py`: `build_chat_model(config: LlmConfig, temperature: float =
        0.0)`. It sends `kwargs["temperature"] = temperature` under the existing
        `accepts_temperature` condition.
      - `backend/app/presser/config.py`: `WRITER_TEMPERATURE = 0.8` and
        `writer_chat_model(config) -> ChatModelSpec`.
      - Use it in `app/worker/cli.py` (`make_presser_runtime`) and `app/presser/cli.py`
        (`make_runtime`).
      - In `app/presser/evaluation/cli.py`, `_caller(settings, clock, model, temperature)`:
        `make_writer` passes `WRITER_TEMPERATURE` and `make_judge` passes `0.0`.
      - Tests first:
        - `tests/llm/test_providers.py::test_temperature_argument_sent_only_where_accepted`:
          0.8 on a row that accepts one, nothing on a `temperature=False` row, and nothing on
          a pair whose fallback refuses one. The existing `test_temperature_only_where_listed`
          keeps proving the default 0.
        - `tests/presser/test_config.py::test_writer_chat_model_temperature`:
          `single_model_config` with `google/gemini-3.1-flash-lite` gives
          `_default_params["temperature"] == 0.8`, and with `openai/gpt-6-luna` no
          `temperature`.
        - `tests/presser/evaluation/test_cli.py::test_writer_gets_temperature_and_judge_zero`
          monkeypatches `app.presser.evaluation.cli._caller` with a recorder that returns a
          `StructuredCaller` over `FakeChatModel`, and sets `OPENROUTER_API_KEY=dummy` with
          `DATABASE_URL` unset. It calls `_deps_from_settings().make_writer("m")` and
          `.make_judge("j")` and asserts the recorded temperatures 0.8 and 0.0.
      Automatic verification: `cd backend && uv run pytest -q tests/llm/test_providers.py tests/presser/test_config.py tests/presser/evaluation/test_cli.py tests/extraction/test_openrouter_payload.py`

- [x] 8. **Writer prompt version 3 (AC9).** `iterations: 0`
      - Rewrite `backend/app/content/prompts/presser_writer.md` with the header `version: 3`.
        Keep the rules of version 2 (net points, counts that include this gameweek, chips,
        `null` = unknown, autosub, names exactly as in the sheet, banter limits, at most 1500
        characters, WhatsApp format) and add these:
        - **Slang** — "use slang only where it sounds natural"; the glossary is a reference
          for correct usage, not a list of words to work in, and a plain sentence is better
          than forced slang.
        - **Style examples** — they show tone and form only: "never copy" a phrase, a
          sentence or a joke from them. Vary how each paragraph is built from one presser to
          the next, without the same opening formula (for example "X punktów netto, Y ponad
          średnią") in every paragraph.
        - **GW rank** — `gameweek_rank` is the score's rank against all FPL managers that
          gameweek, lower is better, and a modest score with a strong GW rank is a good week.
          Use it in the winner, flop and season-record lines when it adds something. The
          season records `best_gameweek` / `worst_gameweek` are by GW rank, and the
          personal bests and worsts are new season bests or worsts of a manager.
        - **Overall** — the sixth section `overall` comes after the table, with the header
          `🌍 *Overall:*`, and is skipped when it is in `empty_sections`. Report only the
          `notable` rows, the climbers and fallers first, with the thresholds entered or left
          and the movement in places as given.
        - **Ranks** — ranks may be quoted exactly or rounded the way FPL players say them
          ("top 10k", "1,2 mln"). This rounding is allowed and is not arithmetic. Never
          invent a rank, a threshold or a movement.
        - **Previous pressers** — at most 2. They are for referring back to events, "not a
          source of phrases".
        - **Headers** — list the six section headers in order:
          `🏆 *Manager kolejki:*` / `*Managerowie kolejki:*`, `🤦 *Wtopa kolejki:*`,
          `©️ *Kapitanowie:*`, `🪑 *Ławka, transfery, chipy:*`, `📊 *Tabela:*`,
          `🌍 *Overall:*`.
      - No code change is needed in `writer.py`: `PROMPT_VERSION` follows the file header.
      - Tests first:
        - `tests/content/test_presser_content.py::test_writer_prompt_v3_rules`: version 3;
          the text contains `only where it sounds natural`, `never copy`, `gameweek_rank`,
          `not a source of phrases`, `🌍` and `1500`, and `` `table` `` comes before
          `` `overall` ``. Keep the `autosub` assertion.
        - `tests/presser/test_writer.py::test_prompt_version_label` adds
          `PROMPT_VERSION == "presser_writer@3"`.
        - `tests/presser/test_service.py::test_generated_presser_stored_with_usage` asserts
          `row.prompt_version == "presser_writer@3"`.
      Automatic verification: `cd backend && uv run pytest -q tests/content tests/presser/test_writer.py tests/presser/test_service.py tests/presser/test_no_polish_literals.py`

- [x] 9. **Style examples rewritten (AC10).** `iterations: 0`
      - Rewrite `backend/app/content/presser_style_examples.md`. Keep the intro (spec 013,
        made-up managers and numbers) and 3 `## Example` sections:
        - a small league with a modest winning score and a strong GW rank;
        - a big league with a tie and a manager entering the top 10k;
        - GW1 with thresholds entered at the start.
      - Each example is a title line plus the six header paragraphs in order, under 1500
        characters, with slang used sparingly. Each has at least one sentence with "GW rank"
        and a `🌍 *Overall:*` paragraph. Each paragraph is built differently from its
        counterpart in the other examples.
      - The new text uses none of the phrases flagged in the SPEC ("odskok od peletonu",
        "peleton", "zielona strzałka", "transfer tygodnia w złą stronę", "X punktów netto, Y
        ponad średnią").
      - Test first: in `tests/content/test_presser_content.py`, replace
        `test_style_examples_have_three_examples` with
        `test_style_examples_six_headers_no_shared_sentence`. It splits the file by
        `## Example` and checks, for each of the 3 examples:
        - the paragraphs after the title start with `🏆`, `🤦`, `©️`, `🪑`, `📊`, `🌍` in this
          order;
        - `len(example) < 1500`;
        - `"gw rank" in example.casefold()`.
        Across the examples it checks:
        - no sentence (split on `(?<=[.!?])\s+`, compared after casefold and strip, header
          labels removed) appears in two examples;
        - no run of 5 consecutive words outside the header labels appears in two examples;
        - none of the flagged phrases above appears.
      Automatic verification: `cd backend && uv run pytest -q tests/content/test_presser_content.py tests/presser/test_writer.py`

- [x] 10. **Judge prompt version 2 (AC14).** `iterations: 0`
      - `backend/app/content/prompts/presser_judge.md` → `version: 2`. "What is a claim"
        adds GW ranks, overall ranks, movements in places, thresholds entered or left ("w top
        10k"), and personal and season bests by GW rank. "Labels" adds that a rank rounded
        the way FPL players say it ("1,2 mln" for 1 234 567, "top 10k" for a rank ≤ 10 000)
        is `supported`, while a rounding that changes the magnitude or a wrong threshold is
        `unsupported`.
      - Tests first:
        - `tests/content/test_presser_content.py::test_judge_prompt_checks_ranks`: version 2,
          and the text contains `rank`, `threshold`, `rounded` and `1,2 mln`.
        - `tests/presser/evaluation/test_runner.py::test_recorded_rank_verdict_counted`: a
          fake judge model replies with a recorded `JudgeVerdict` of two claims,
          `"Bartas w top 1,2 mln overall"` `supported` and `"Kuba wszedł do top 10k"`
          `unsupported`, on a case whose overall row has rank 1 234 567. The run records both
          labels and `faithfulness == 0.5`, and the judge's human message contains the
          overall row.
      Automatic verification: `cd backend && uv run pytest -q tests/content tests/presser/evaluation/test_runner.py`

- [x] 11. **Logs carry no names or ranks (AC15).** `iterations: 0`
      - No product change is expected: the presser facts code does not log. Only the
        existing log lines (gameweek, league ordinal, error class) remain.
      - Test first: extend `tests/presser/test_worker_run.py::test_logs_carry_no_names_or_text`.
        It computes the GW5 `rank` and `overall_rank` of each entry in `ENTRY_IDS` from the
        `synthetic_league` formulas and asserts that none of them appears in `caplog.text`,
        and that the writer's human message contains `"gameweek_rank"`. This proves that the
        ranks reached the sheet.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_worker_run.py tests/presser/test_trigger.py`

- [x] 12. **`review` records inflection errors; `summary` averages them (AC13).** `iterations: 1`
      - `app/presser/evaluation/cli.py` `review`: after the note, prompt
        `INFLECTION_PROMPT = "inflection errors in names (whole number, Enter = 0)"`. It
        re-asks on anything that is not a whole number ≥ 0 and stores
        `item["style"] = {"rating", "note", "inflection_errors"}`. `_echo_rating_summary` adds
        the average inflection errors.
      - `summary.py`: `RunSummary.inflection_errors: float | None`, the average over the
        pressers whose `style` has the key. `format_run` prints `inflection errors <x.xx>`
        or `n/a`. `passes` and `choose` do not change.
      - Tests first:
        - `tests/presser/evaluation/test_cli.py::test_review_records_inflection_errors`:
          input `4\nfajne\n2\n3\n\n\n` stores 2 and 0. A second run with `x` and `-1` before
          `1` re-asks and stores 1.
        - Update `test_review_records_rating_and_note` and the others that feed `review`
          input, adding the extra line.
        - `tests/presser/evaluation/test_summary.py::test_summary_averages_inflection_errors`:
          a run with 2 and 0 gives 1.0, and a run without the key gives `None` and prints
          `n/a`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation/test_cli.py tests/presser/evaluation/test_summary.py`

- [x] 13. **Set v2: composition, synthetic cases, leak check (AC12, synthetic part).** `iterations: 0`
      - `app/presser/evaluation/cases.py`:
        - `MIN_CASES = 18`, `MAX_CASES = 22`, `MIN_SYNTHETIC_CASES = 10` and
          `MIN_TEST_CASES = 10`.
        - `EDGE_TAGS` adds `enter_top_10k`, `rise_in_top_10k`, `drop_out_of_top_1m` and
          `gw_rank_unknown`.
      - `app/presser/evaluation/building.py` `_manager_names`: add the overall rows, the
        climbers and fallers, and the personal ranks.
      - Update the 6 synthetic cases in `backend/evals/presser/v2/cases.jsonl`. Each gets
        `gameweek_rank` on its winners and flops, records by GW rank, and an `overall` section
        consistent with the ranks, keeping its tag. `syn-first-gameweek` gets GW1 thresholds
        entered.
      - Add 4 synthetic cases with pool names and a pool league name, 2 dev and 2 test, each
        with GW > 1 and 1–2 previous pressers written for the project:
        - `syn-enter-top-10k` (`enter_top_10k`, test);
        - `syn-rise-in-top-10k` (`rise_in_top_10k`, dev);
        - `syn-drop-out-of-top-1m` (`drop_out_of_top_1m`, test);
        - `syn-gw-rank-unknown` (`gw_rank_unknown`, dev): every `gameweek_rank` is `null`,
          there are no records, and `overall` is empty and listed in `empty_sections`.
      - Write the cases as JSON with a scratchpad helper that validates each with
        `FactSheet` and `check_fact_sheet` before writing. The helper is not committed.
      - Tests first, in `tests/presser/evaluation/test_eval_set.py` and `test_cases.py`:
        - The split test becomes 10 dev and 10 test, with 5 real and 5 synthetic in each.
        - `test_edge_cases_cover_every_tag` covers the new tags.
        - `test_no_case_text_holds_an_entry_or_league_id` walks each case's JSON, drops the
          values of the rank keys (`gameweek_rank`, `overall_rank`,
          `previous_overall_rank`, `movement`, `entered`, `left`), and asserts that no
          `\d{6,}` remains. History and pseudonyms are checked as before.
        - `test_cases.py` count tests follow the new limits.
        - `tests/presser/evaluation/test_building.py::test_build_pseudonymises_overall_and_personal_names`
          seeds ranks so that the overall section and a personal best are filled, and
          asserts that those names come from the pool.
        - The existing leak test still passes.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation`

- [x] 14. **Set v2: real cases rebuilt with GW ranks (AC12, real part).** `iterations: 0`
      - Precondition (owner decision: the owner fills GW1–5 by a manual league sync).
        - With `DB=postgresql://presser:presser@localhost:5432/presser`, from `backend/`:
          `env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run alembic upgrade head`.
        - Then count the rows that still lack a rank:
          `env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run python -c "from sqlalchemy import text; from app.db.engine import make_engine; import os; e=make_engine(os.environ['DATABASE_URL']); print(e.connect().execute(text('SELECT count(*) FROM manager_gameweek WHERE has_team AND gameweek_fpl_id <= 5 AND gameweek_rank IS NULL')).scalar_one())"`.
          It prints a count, never a name.
        - If the database is unreachable, end with `RESULT: ESCALATE`, `KIND: tooling`.
        - If the count is > 0, end with `RESULT: ESCALATE`, `KIND: decision`. The message asks
          the owner to run `python -m app.fpl league-sync --gameweek N` for N = 1..5 (or
          `python -m app.fpl backfill`) in `backend/` with their `.env`, then resume.
        - Do not run the league sync yourself.
        - Privacy gate (the repository is public): a current overall rank maps to one manager
          on FPL's overall standings pages (page = rank ÷ 50), which would undo the pseudonym
          and, through that manager's leagues, reveal the league. The GW5 overall ranks stop
          being current once GW6 is finished. Check it the same way, printing only a boolean:
          `SELECT finished FROM gameweek WHERE fpl_id = 6 AND season = '<current season>'`
          (the owner's league sync refreshes the reference data). If GW6 is not finished, end
          with `RESULT: ESCALATE`, `KIND: decision`, before building or committing the real
          cases. Options: (a) wait until GW6 is finished, the owner re-runs the league sync,
          then resume (recommended); (b) the owner accepts committing the current GW5 overall
          ranks. The orchestrator records the answer in `## Owner decisions`.
      - Run `env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run python -m app.presser.evaluation build-cases --gameweeks 1-5 --force`.
        - It must report 10 real cases, 10 synthetic kept and no missing history.
        - Check the real cases for a 5/5 split and the `first_gameweek` tags.
        - The history texts in `v2/history.jsonl` stay as they are: they are earlier
          pressers.
      - Never run `python -m app.presser facts` against the development database: it would
        print real names.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation/test_eval_set.py tests/presser/evaluation`

- [x] 15. **Documents (AC16, AC2 runbook).** `iterations: 0`
      - `docs/BACKLOG.md` #32:
        - The trigger becomes "spec 013 is merged".
        - The item runs on prompt version 3 and set v2 and adds the inflection measure:
          `review` records the owner's inflection-error count, `summary` averages it per
          model, and #32 sets the threshold.
        - If no candidate inflects acceptably, #32 adds a BACKLOG item for a name-forms
          dictionary.
      - `docs/DECISIONS.md` gets new rows dated on the day of implementation:
        - The season's best and worst gameweeks and the personal bests and worsts are by
          FPL GW rank (`manager_gameweek.gameweek_rank`, from `entry_history.rank`). The
          manager of the gameweek stays the most net points, and no FPL-wide average is
          collected.
        - The sixth fixed section "🌍 Overall" comes after the table and is skipped when
          empty. Its thresholds are 1M / 100k / 10k, every rise inside the top 10k is
          notable, outside it a move must halve or double the rank, and the movers are
          ranked by that ratio.
        - The writer runs at temperature 0.8 where the model accepts one (`build_chat_model`
          `temperature` argument), and every other step stays at 0.
        - Set v2: 20 cases, and the inflection count in `review`.
      - `docs/DEPLOYMENT.md` step 13:
        - A bullet for migration `0012`: it adds the nullable `gameweek_rank` to
          `manager_gameweek`, existing rows stay empty until the next league sync of that
          gameweek, the first-deploy catch-up (step 6) syncs every finished gameweek and so
          fills them, and the migration runs in the pre-deploy and downgrades cleanly.
        - The presser description adds the Overall paragraph.
      - Tests first, in `backend/tests/test_docs.py`:
        - `test_backlog_32_after_spec_013_with_inflection`: the #32 row contains
          `spec 013 is merged` and `inflection`.
        - `test_decisions_cover_gameweek_rank_and_overall`: DECISIONS contains `GW rank`,
          `Overall` and `10k`.
        - `test_deployment_lists_migration_0012`: DEPLOYMENT contains `0012` and
          `gameweek_rank`.
      Automatic verification: `cd backend && uv run pytest -q tests/test_docs.py && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`

## Risks and traps

- **Old migration tests and the new column.** `table_contents` reads the current model's
  columns, so the tests that run at revisions before 0012 must exclude `gameweek_rank` (step
  1). Run the whole `tests/db/test_migrations.py`, not only the new test.
- **The version bump breaks set v1.** `FactSheet.version` is `Literal[2]`, so the committed
  v1 cases stop loading. Step 3 moves and converts them in the same step. Every hand-written
  `FactSheet` dict in the tests must list `overall` in `empty_sections` when it is checked by
  `check_fact_sheet`.
- **ID-like numbers.** Overall ranks have 6–7 digits. The old `\d{6,}` guard against entry
  and league IDs in the eval files would fail, and the guard must not be dropped: step 13
  narrows it to non-rank fields. `git grep -E '[0-9]{6,}' backend/evals/presser` is no longer
  a valid check.
- **Owner-dependent step.** Step 14 needs the owner's league sync. Every other step comes
  first, so an escalation there leaves a complete branch except the real cases.
- **FPL `rank` may be `null`**, for example before a gameweek is ranked. A re-sync then
  overwrites a stored rank with NULL. FPL is the source, and the presser runs after the
  gameweek is finished and checked, when ranks exist. The fact sheet treats NULL as unknown.
- **Hit and GW rank.** Whether FPL ranks a gameweek before or after hits is open. The
  sheet passes the number on as is, and the check does not compare GW rank with net points
  between managers. It compares only against the season records.
- **Privacy.** Logs stay limited to the gameweek, the league ordinal and the error class
  (AC15 test). The real overall ranks in set v2 are tied to pseudonyms only. This is the risk
  flagged in the owner summary; step 14's GW6 gate keeps current overall ranks out of the
  public repository.
- **Polish in code.** The thresholds are integers, and the headers and phrases live only in
  the prompt and the examples. `tests/presser/test_no_polish_literals.py` must stay green,
  and test files may contain Polish.
- **Floating-point ratios.** Use integer comparisons for "notable" and `Fraction` for the
  ranking, never `float`, or the AC6 edge cases (exactly 2×) flip.
- **The temperature is invisible on the default model.** `openai/gpt-6-luna` has
  `temperature = false`, so production behaviour does not change until #32 picks a model.
  The tests prove the request parameter on a row that accepts one.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

1. `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` —
   all green.
2. Migration on the local development database, from `backend/`, with
   `DB=postgresql://presser:presser@localhost:5432/presser`:
   `env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run alembic current` shows `0012 (head)`.
   Do not downgrade the local database: that would drop the GW ranks of the owner's sync. The
   upgrade → downgrade → upgrade round trip is proven on a container by
   `tests/db/test_migrations.py::test_upgrade_downgrade_upgrade` and the step 1 test.
3. `uv run python -m app.presser.evaluation --help` lists the five commands, and the
   `build-cases` help names set v2.
4. `uv run pytest -q tests/presser/evaluation/test_eval_set.py` passes on the committed v2
   set (20 cases, 10/10, every new tag, no ID-like number outside the rank fields).
5. Record the results in this PLAN under the Definition of Done.

### Manual (performed by the owner)

- Read a real GW5 presser with ranks (about $0.001). In `backend/` with your `.env`, after
  your league sync of GW1–5, run
  `uv run python -m app.presser facts --league <your league> --gameweek 5`, then
  `uv run python -m app.presser preview --league <your league> --gameweek 5`.
  Pass when: `facts` shows `version: 2`, a `gameweek_rank` on the winners and flops, the
  season records with `gameweek_rank`, and `overall.rows`. The preview is Polish, at most
  1500 characters, and keeps the headers in order. It has a `🌍 *Overall:*` paragraph exactly
  when `overall` is not in `empty_sections`, quotes no rank or threshold absent from `facts`,
  and copies no sentence from `backend/app/content/presser_style_examples.md`.
- Approve the rewritten style examples. Read
  `backend/app/content/presser_style_examples.md` at the final review.
  Pass when: you accept the three texts as the voice of the presser (slang used sparingly,
  GW rank and Overall lines natural). The final review then records your decision as
  accepted.

## Definition of Done

- [x] all steps ticked
- [x] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
      fully green (1694 passed)
- [x] end-to-end verification (automatic) performed, result recorded here: (1) full verify green, 1694 passed; (2) local DB `alembic current` = `0012 (head)`, 0 rows with a team in GW1-5 lack a GW rank; (3) `python -m app.presser.evaluation --help` lists the five commands, `build-cases` help names set v2; (4) `build-cases --gameweeks 1-5 --force` reported 10 real + 10 synthetic kept, real split 5 dev / 5 test, and `tests/presser/evaluation` (67 tests) passes on the 20-case set. Step 14: the owner's decision (sync done, GW6 privacy gate waived) is in Owner decisions.
- [ ] `docs/ROADMAP.md`: n/a, because Stage 3 item 1 already links spec 013 and no new item
      is completed. `docs/DECISIONS.md`, `docs/BACKLOG.md` #32 and `docs/DEPLOYMENT.md` are
      updated (step 15).
- [x] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

- 2026-10-09 — implement — `decision` — Step 14 is blocked: GW1–5 `gameweek_rank` is empty locally and GW6 is not finished, so the GW5 overall ranks are still current (privacy gate) — The owner runs the league sync of GW1–5 now and accepts committing the current GW5 overall ranks under pseudonyms; the GW6 privacy gate is waived for this spec
- 2026-10-10 — final-review — `gate` — Which final-review findings to fix? — `accepted`: F1, F2, F3, F4, F5, F6, F7, F8, F9, F10, F11; `rejected`: none

## Review log

2026-10-09 — plan review (fresh eye, anti-anchoring on the SPEC first).

Findings:

- `major` — Step 14 committed the real cases' GW5 overall ranks to a public repository
  (`gh repo view`: PUBLIC) while they are still current. A current overall rank points to one
  manager on FPL's overall standings pages, undoing the pseudonym and, through that manager's
  leagues, the league itself (PROJECT privacy: manager names and league IDs never in the
  repository). The plan only flagged it. Changed: step 14 gets a privacy gate (GW6 must be
  `finished` in the local `gameweek` table before the real cases are built and committed,
  otherwise `ESCALATE` / `decision` with wait vs. accept); the owner summary risk (2) and the
  privacy risk mention the gate.
- `minor` — Step 2 changes `synthetic_league`, which `tests/fpl/test_backfill.py`,
  `tests/fpl/test_cli.py` and `tests/worker/sim.py` also use, but its verification ran only two
  `tests/fpl` files. Changed to `tests/fpl tests/worker tests/presser/test_worker_run.py`.
- `minor` — The `Overall.rows` order ("by move ratio desc") was undefined for rows without a
  previous rank (every row at GW1). Changed: those rows come after the ones with a ratio, then
  by overall rank, then manager.
- `minor` — The §6 check "a winner's known GW rank lies within [best, worst]" said nothing
  when the records are empty while a winner has a rank. Changed: that is a problem, and step 6
  gets a mutation test for it.

Checked and found correct:

- Coverage: AC1–AC16 each have a step and a named proving test; the matrix matches the steps.
  Referenced existing tests and helpers exist (`test_records`, `test_prompt_version_label`,
  `test_generated_presser_stored_with_usage`, `test_previous_pressers_latest_two_sent_before_gameweek`,
  `test_review_records_rating_and_note`, `test_logs_carry_no_names_or_text`,
  `test_upgrade_downgrade_upgrade`, `table_contents` / `DEFAULT_EXCLUDE` / `BEFORE_OWNERSHIP`,
  `_apply_bootstrap_at_revision`, `World.gw`, `FakeChatModel`).
- Compliance: Polish text only in `app/content/` (prompt, examples); the writer and judge
  prompts are English with Polish examples, so the English assertions of steps 8 and 10 match;
  no DECISIONS row is broken (2026-10-09 presser rows extended; set v1 row superseded by the
  step 15 row); temperature stays a step parameter, not a `model_settings.toml` key.
- Feasibility: the order has no forward dependency (column in step 1 before the sync in step 2;
  schema in step 3 before overall/checks; set converted in step 3 so every step stays green);
  `build-cases --force` keeps the synthetic cases; real split `(gameweek + index) % 2` gives
  5/5; the migration-test trap with `table_contents` is real and handled; stored `presser`
  rows are never re-parsed into `FactSheet`, so `Literal[2]` breaks nothing outside the set;
  no writer/render code enforces the header list.
- Temperature: `build_chat_model` is the single place, its current `accepts_temperature`
  rule covers the fallback; `gemini-3.1-flash-lite` accepts a temperature and `gpt-6-luna`
  does not, as the step 7 test assumes; all other call sites keep 0.
- Rank rules: integer comparisons and `Fraction` give exact AC6 edges; climbers only among
  notable rows keeps AC7 consistent.
- Owner summary: new dependency no, migration 0012 yes and accepted in SPEC → Owner decisions;
  the owner-dependent league sync is an escalation in step 14, not a step for the owner.
- E2E: automatic part runnable by the implementer; both manual items have `Pass when:`; no
  UI scope. Language: English throughout.

Decision: the plan is ready — every AC is covered by a test-first step, the migration is
accepted by the owner, no dependency is added, and the only owner-dependent action (the league
sync, now with the GW6 privacy gate) is a guarded escalation point at the end.

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

- `minor` — Step 15 (documents) was done before step 14, because step 14 waits for the owner's league sync and the GW6 privacy gate and the plan says every other step comes first.
- `minor` — The test `test_writer_gets_temperature_and_judge_zero` (step 7) does not unset `DATABASE_URL`: the guard `tests/core/test_settings.py::test_database_url_not_read_by_tests` forbids that name in tests, and `_deps_from_settings` already tolerates a missing URL.
- `minor` — In `tests/presser/test_facts_checks.py` the consistent sheet is built from `factories.sheet()` by `ranked_sheet()` in the test file itself rather than by extending the factory.

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_

2026-10-10 — final review (report). Three independent perspectives (compliance, quality,
tests with 59 mutations: 46 killed, 4 equivalent, the rest listed as gaps below); every finding
checked in the code.

AC → evidence:

| AC | Evidence | Status |
|----|----------|--------|
| AC1 | `tests/fpl/test_league_sync.py::test_gameweek_rank_stored_and_none_without_team`, `::test_resync_fills_an_empty_gameweek_rank`; `tests/fpl/test_schemas.py::test_entry_history_rank_optional` | ok |
| AC2 | `tests/db/test_migrations.py::test_gameweek_rank_migration_adds_empty_column_and_downgrades`; `tests/test_docs.py::test_deployment_lists_migration_0012` | ok |
| AC3 | `tests/presser/test_facts_ranks.py::test_winners_and_flops_carry_gameweek_rank`, `::test_unknown_gameweek_rank_is_null` | ok |
| AC4 | `tests/presser/test_facts_table.py::test_records_by_gameweek_rank_with_ties`, `::test_records_skip_rows_without_gameweek_rank` | ok (F1) |
| AC5 | `test_facts_ranks.py::test_personal_best_and_worst_need_three_ranked_gameweeks`, `::test_equal_rank_is_not_a_personal_best_or_worst` | ok (F6) |
| AC6 | `tests/presser/test_ranks.py`; `test_facts_ranks.py::test_overall_section_rows_and_movers`, `::test_overall_first_gameweek_has_no_movement` | ok (F5) |
| AC7 | `test_facts_ranks.py::test_overall_empty_when_nothing_notable` | ok |
| AC8 | `tests/presser/test_facts_checks.py` | weak assertions (F3) |
| AC9 | `tests/content/test_presser_content.py::test_writer_prompt_v3_rules`; `test_writer.py::test_prompt_version_label`; `test_service.py::test_generated_presser_stored_with_usage` | ok |
| AC10 | `test_presser_content.py::test_style_examples_six_headers_no_shared_sentence`; owner approval pending | examples break the notable-only rule (F2) |
| AC11 | `tests/llm/test_providers.py::test_temperature_argument_sent_only_where_accepted`; `tests/presser/test_config.py::test_writer_chat_model_temperature`; `evaluation/test_cli.py::test_writer_gets_temperature_and_judge_zero` | production call sites untested (F4) |
| AC12 | `evaluation/test_eval_set.py`; `test_building.py::test_build_pseudonymises_overall_and_personal_names` | ok |
| AC13 | `evaluation/test_cli.py::test_review_records_inflection_errors`; `test_summary.py::test_summary_averages_inflection_errors` | ok |
| AC14 | `test_presser_content.py::test_judge_prompt_checks_ranks`; `evaluation/test_runner.py::test_recorded_rank_verdict_counted` | ok |
| AC15 | `tests/presser/test_worker_run.py::test_logs_carry_no_names_or_text` | ok |
| AC16 | `tests/test_docs.py::test_backlog_32_after_spec_013_with_inflection`, `::test_decisions_cover_gameweek_rank_and_overall` | ok |

Plan steps: all 15 delivered as described; the 3 minor deviations are justified; nothing
outside the scope; owner decisions (migration, GW6 privacy gate waived) honoured.

Findings:

- **F1** `blocker` — `backend/app/presser/facts/schema.py:221-222` (with `season.py` `_records`, `build.py:33`) — a manager who leaves the league after holding the season's best or worst GW rank keeps old `manager_gameweek` rows (memberships are only upserted), is named in `best_gameweek` / `worst_gameweek` but not in the table, so `check_fact_sheet` reports "a section names a manager who is not in the table" and `build_fact_sheet` raises `FactSheetError` for every later gameweek: the league gets no presser for the rest of the season (reproduced with a scratch test) — in `build_fact_sheet` keep only the entries present this gameweek before building the season facts, and add a test.
- **F2** `worth-fixing` — `backend/app/content/presser_style_examples.md:38,54` — example 2 reports Ola 21 tys. → 12 tys. (no threshold, under 2×) and example 3 reports Bartas at 2,4 mln at GW1 (no threshold entered): both rows are not `notable`, so the examples teach the writer to break the prompt's "Report the `notable` rows only" rule — replace those sentences with notable moves (the owner approves the texts at this review).
- **F3** `worth-fixing` — `backend/tests/presser/test_facts_checks.py:143` — each mutation case asserts only that the problem list is non-empty, so the "rank is not positive", "notable does not match", "best worse than worst" and overall-row naming checks can be deleted with the suite green (mutation-proven), and "a personal worst is below the league worst" has no case — parametrise the expected message and assert it is in the list; add a personal-worst case.
- **F4** `worth-fixing` — `backend/app/worker/cli.py:235`, `backend/app/presser/cli.py:92` — reverting either production writer site from `writer_chat_model` to `build_chat_model` keeps the suite green, so AC11's temperature 0.8 is proven only for the evaluation CLI — add a test per module that records the temperature requested by `make_presser_runtime` / `make_runtime`.
- **F5** `worth-fixing` — `backend/tests/presser/test_ranks.py`, `test_facts_ranks.py` — `now < before` → `<=` in the top-10k rule (an unchanged 8 000 becomes notable), `TOP_TIER = 10_001` and truncating `ranks.leaders` to one name all survive — add `not is_notable(8_000, 8_000, False)`, `not is_notable(10_001, 10_002, False)` and a tie of two climbers with the same ratio.
- **F6** `worth-fixing` — `backend/tests/presser/test_facts_ranks.py` — no case with ≥ 3 earlier ranked gameweeks and a null GW rank this gameweek; removing the `gameweek not in ranks` guard in `season.py` `_personal_ranks` survives and would raise `KeyError` in production — add GW1–3 ranked, GW4 null, `sheet(world, 4)` without flags or error.
- **F7** `nit` — `backend/app/presser/facts/ranks.py:30-33`, `overall.py` — an `overall_rank` of 0 (`EntryHistory.overall_rank` is a plain `int`) raises `ZeroDivisionError` in `move_ratio` before `check_fact_sheet` can report it; the service's generic handler contains it, so the effect is the same failed presser — treat a non-positive rank as unknown at load time.
- **F8** `nit` — `backend/app/fpl/leagues.py:145` — removing `"gameweek_rank": None` from `_no_team_gameweek` survives; a gameweek re-synced as no-team would keep a stale rank — add a re-sync test with `no_team_for`.
- **F9** `nit` — `backend/app/presser/evaluation/cli.py:275` — `answer.isdigit()` accepts `"²"`, then `int()` raises and `review` crashes — `answer.isascii() and answer.isdigit()` plus a test.
- **F10** `nit` — `backend/app/presser/facts/schema.py:274` vs `season.py` `MIN_RANKED_GAMEWEEKS` — the 3-gameweek rule is a literal in the check and a constant in the builder, so they can drift — move the constant into `ranks.py` and use it in both.
- **F11** `nit` — `backend/app/presser/facts/ranks.py:37` — `leaders` has a docstring, which CONVENTIONS forbids — remove it.

Rejected:

- AC14 "proves only the plumbing": the AC asks for a fake judge on a recorded reply plus the prompt text check, which is what the test does; not a defect.

Left out: 12 nit findings

2026-10-10 — final review (apply). Owner decision: F1–F11 accepted, none rejected. Fixed:

- **F1** → `season.py` `build_season` builds the GW-rank records only from managers with a row in this gameweek; test `tests/presser/test_facts_table.py::test_records_ignore_managers_absent_this_gameweek` (failed with `FactSheetError` before the fix). DECISIONS 2026-10-09 GW-rank row updated.
- **F2** → `presser_style_examples.md`: example 2 now reports Ola 30 tys. → 14 tys. (a 2× move); example 3 drops the non-notable Bartas line.
- **F3** → `test_facts_checks.py` parametrised with the expected problem message (`expected in problems`), plus a personal-worst-below-the-league-worst case.
- **F4** → `tests/worker/test_cli.py::test_presser_runtime_writer_gets_temperature` and `tests/presser/test_cli.py::test_make_runtime_writer_gets_temperature`; both fail when the site is reverted to `build_chat_model`.
- **F5** → `test_ranks.py`: unchanged 8 000 and 10 001 → 10 002 are not notable; `test_leaders_keep_every_manager_tied_on_the_biggest_ratio`.
- **F6** → `test_facts_ranks.py::test_unranked_gameweek_after_three_ranked_has_no_personal_flags` (fails with the guard removed).
- **F7** → `load.py` `_known_rank`: a non-positive overall or GW rank loads as unknown; `test_facts_ranks.py::test_non_positive_ranks_are_unknown`.
- **F8** → `tests/fpl/test_league_sync.py::test_resync_without_team_clears_the_gameweek_rank`.
- **F9** → `_ask_inflection_errors` accepts ASCII digits only; `test_review_records_inflection_errors` feeds `²`.
- **F10** → `MIN_RANKED_GAMEWEEKS` moved to `ranks.py`, used by `season.py` and `check_fact_sheet` (message now "too few ranked gameweeks").
- **F11** → docstring removed from `ranks.leaders`.

Verification: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` — green, 1702 passed. BACKLOG: no new items, none delivered; #32's trigger (spec 013 merged) fires at the merge.
