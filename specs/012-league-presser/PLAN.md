# PLAN 012 — League presser

## Owner summary

- **Approach:** a new module `app/presser/` computes a deterministic fact sheet per league and
  gameweek with SQL plus plain Python, an LLM writer (one LangGraph node over the shared
  `StructuredCaller`) turns it into the Polish presser, and a new `presser` table logs every
  generation. The worker calls a presser hook after each successful league sync; the hook acts
  only for the latest finished gameweek and sends through the existing `DeliveryService` with
  the key `presser:<season>:gw<N>:league<id>`. A CLI (`facts`, `preview`, `send`, `status`)
  and an evaluation package (`build-cases`, `evaluate`, `review`, `judge-review`, `summary`)
  follow the patterns of the corroboration and extraction evaluations.
- **Main risks:** a wrong fact (winner, captain credited, table movement) would break the
  joke. Each fact rule has its own DB test. The real evaluation cases come from the local
  development database and must leave no real name in the repository. The builder replaces
  names before anything is written or printed and checks the output against the real names.
  The real cases have no previous pressers. The implementer writes short frozen ones from the
  earlier gameweeks' pseudonymised fact sheets (`history.jsonl`). The owner can replace them.
- **New dependency:** no.
- **Data migration:** yes. Migration `0011` adds the new table `presser`. It is accepted in
  SPEC → "Owner decisions".
- **Manual scenarios for the owner:** 3. One reads a real preview from the local data. One
  checks the e-mail and the WhatsApp button in a real inbox. One checks that nicknames from
  `PRESSER_NICKNAMES` appear.

## Approach

**What the plan rests on (read and how):**

- `specs/012-league-presser/SPEC.md`: read in full.
- `docs/CONVENTIONS.md`: read in full.
- `docs/DECISIONS.md`: searched for "presser", "judge", "eval", "Stage 3". The search found
  the module-per-area rows (2026-09-26/27, `app/presser`), facts via SQL (ADR 0002) and fake
  LLM in unit tests with evaluations outside pytest (2026-09-26). It also found the shared
  `app/llm/` (2026-09-29/30), the evaluation sets in JSONL dev/test with a judge pre-labelled
  by another vendor (2026-09-28, 2026-09-30) and our own Langfuse tracer with cost from
  `prices.toml` (2026-09-30). It also found the delivery idempotency key and log (2026-10-01)
  and the Stage 3 exception (2026-10-09).
- `docs/ROADMAP.md`: searched for "presser", "Stage 3". The search found the Stage 3 item 1
  line to tick.
- `docs/BACKLOG.md`: searched for "presser", "32". The search found #5 and #32, which already
  exist and stay out of scope.
- `docs/DEPLOYMENT.md`: searched for headings and "presser". Steps 1–12 exist. Step 11
  mentions the presser. The new step is 13.
- `README.md`: searched for headings. The `## Development` section lists the commands.
- `docs/PROJECT.md`: searched for "FR-3". The search found FR-3.1–3.3.
- Code read in full:
  - FPL models: `app/fpl/models/{leagues,reference,snapshots,columns,__init__}.py`.
  - Worker: `app/worker/{jobs,loop,schedule,models,store,cli}.py`.
  - Delivery: `app/delivery/{service,models,store,content,config}.py`,
    `app/delivery/channels/base.py`.
  - Alerts: `app/alerts/{config,render,service,models,store,status,cli}.py` and
    `app/content/alert_email.toml`.
  - LLM: `app/llm/{chat,models,pricing,settings,structured,tracing}.py`, `model_settings.toml`,
    `prices.toml`.
  - Core: `app/core/{retry,settings,errors}.py`.
  - Corroboration: `app/corroboration/{judge,tracing,runtime,config}.py`.
  - Corroboration evaluation: `app/corroboration/evaluation/{cases,cli,runner,metrics}.py`.
  - Extraction evaluation: `app/extraction/evaluation/{selection,compare,review}.py`.
  - Content: `app/content/__init__.py`.
  - Migrations: `migrations/versions/0009_alert_log.py`, `migrations/env.py` (imports).
  - Tests: `tests/conftest.py`, `tests/db/test_migrations.py` (structure),
    `tests/test_{module_boundaries,shared_code,env_example,readme,docs}.py`,
    `tests/worker/{sim,test_loop}.py` (relevant parts), `tests/extraction/fakes.py`
    (`FakeChatModel`), `tests/delivery/fakes.py`, `tests/corroboration/fakes.py`,
    `tests/alerts/helpers.py` (`seed_league`), `tests/fpl/fakes.py` (`synthetic_league`).
  - Content: `app/content/presser_glossary.toml` (70 `[[term]]` entries with `term`,
    `meaning`, `example`, `source`) and `presser_style_examples.md` (3 examples).

**Patterns reused:**

- Settings and the disabled reason: `AlertSettings` + `alerts_disabled_reason`
  (`app/alerts/config.py`), and the worker logging `alerts disabled: <reason>` once at start.
- The LLM call: `single_model_config` + `build_chat_model` (`app/llm/chat.py`) and
  `StructuredCaller.call_with_usage` (`app/llm/structured.py`), which bring the shared retry,
  usage and cost. The one-node `StateGraph` follows `app/corroboration/judge.py`.
- Tracing: our own tracer with an explicit generation and the cost from `prices.toml`, like
  `app/corroboration/tracing.py`. Tests use `tests/corroboration/fakes.py::FakeLangfuseClient`.
- Delivery: `DeliveryService.send(key, "presser", Message)`. The kind `presser` already
  exists.
- The e-mail: a TOML template in `app/content/` loaded with `tomllib`, values escaped, and the
  `wa.me` share button. This is the same idea as `app/alerts/render.py::_share`. The presser
  keeps its own small renderer, so it does not import `app.alerts`.
- Prompts: `app/content/load_prompt` with a `version: N` header. The prompt version is
  recorded as `presser_writer@N`.
- The evaluation layout: JSONL cases with atomic writes, a typer CLI with injectable deps and
  result JSON under `evals/<feature>/results/`, as in `app/corroboration/evaluation/`. The
  interactive review loop follows `review` in `app/corroboration/evaluation/cli.py`.
- Tests:
  - `FakeChatModel` (`tests/extraction/fakes.py`) answers structured calls.
  - `FixedClock` and `FakeChannel` (`tests/delivery/fakes.py`).
  - The worker simulation (`tests/worker/sim.py`: `SimulatedFpl`, `FakeClock`, `seed_done`)
    covers the worker trigger and the log-privacy run.

**Design choices (with the variant rejected):**

1. **The trigger is a hook in the worker loop after a successful league sync.** The rejected
   variant is a separate polling thread that sends the presser of any unsent gameweek. The
   hook sends exactly once per league-sync success. A deploy or `PRESSER_ENABLED=true` turned
   on mid-week does not send last week's presser. A catch-up runs its league syncs in
   gameweek order, and the hook drops every gameweek but the latest finished one. The cost is
   that a worker crash between the league sync and the presser loses that presser, and the
   owner then uses `send`. The hook runs after the job's transaction commits, outside the job
   lock, so the slow LLM call never holds the job lock.
2. **The writer returns structured output `{text}` through `StructuredCaller`.** The rejected
   variant is a plain-text chat call. The structured call reuses the shared retry, usage, cost
   and the fake model of the tests. No second call path is added to `app/llm/`.
3. **Each generation is a row in `presser`, and the idempotency key is not unique there.** A
   preview, a failed attempt and a later manual `send` are separate rows. The delivery log
   keeps the uniqueness of the key. The worker skips a league when a row with the key has the
   status `sent` or `failed` (AC9, AC10). `send` skips only on `sent` (AC13).
4. **The fact sheet is a Pydantic model (`extra="forbid"`) with English keys and manager
   display names only.** It carries no entry IDs, league IDs or team names. It is stored as
   JSONB, given to the writer and the judge as JSON, and frozen in the evaluation cases.
5. **Display-name collisions.** Two managers can resolve to the same name, for example two
   managers called "Tomasz" without nicknames. Then each gets the initial of the last word of
   their `manager_name` ("Tomasz K."). If they still collide, they get a number suffix
   ("Tomasz K. 2") by entry ID order. A shared name would make "who won" ambiguous. This
   fills a case the SPEC does not mention. It never changes a configured nickname.
   Configured nicknames must be unique, or the config is invalid.
6. **Previous pressers of the real evaluation cases.** No presser has been sent yet, so the
   frozen previous pressers of the real GW2–5 cases are short texts the implementer writes.
   It writes them from the pseudonymised fact sheets of the same league's earlier gameweeks,
   which are themselves cases. They go in `evals/presser/v1/history.jsonl` (league alias,
   gameweek, text). `build-cases` attaches the latest two before each case's gameweek. The
   owner can replace them before BACKLOG #32.

**Fact rules, exact (the implementer does not choose):**

- **Members** are the managers in `league_membership` for the league and season that have a
  `manager_gameweek` row at N with `has_team = true`. Everyone else is in no section (AC1).
  A league with no such manager has no presser (`NoFactsError`, logged as a class name).
- **Net points** are `points − event_transfers_cost` (a null cost counts as 0).
- **Winners** are every member on the maximum net points. **Flops** are every member on the
  minimum.
- **Average:** `average_points` is the mean of the gross `points` of the members, rounded to
  1 decimal place.
- **Captain:**
  - The credited player is the captain if his `player_gameweek_result.minutes` at N is
    greater than 0. If not, it is the vice-captain, if the vice played more than 0 minutes.
    Otherwise it is the captain, with 0 points.
  - A missing result row counts as 0 minutes and 0 points.
  - `multiplier` is 3 when `active_chip == "3xc"`, otherwise 2. The stored pick multiplier
    is not used.
  - `base_points` is the credited player's `total_points`, and
    `points = base_points × multiplier`.
  - `vice_stepped_in` is true when the vice was credited. `blank` is true when
    `base_points ≤ 2`.
  - Best and worst captain are the maximum and minimum `points`, with ties giving several.
- **Bench:** members with `points_on_bench > 0`, ranked by points descending. A member with
  `bboost` is not in this list.
- **Hits:** members with `event_transfers_cost > 0`, ranked by cost descending.
- **Transfer misses:**
  - These are the `manager_transfer` rows with `gameweek_fpl_id = N` where the incoming
    player's points at N are lower than the outgoing player's.
  - `difference = out − in`. They are ranked by difference descending, then by manager name.
- **Chips** come from `active_chip`:
  - `bboost`: the effect is the sum of the points of pick positions 12–15.
  - `3xc`: the effect is the extra points (`base_points × 1`).
  - `freehit` / `wildcard`: the effect is `points − average_points` (1 decimal place).
  - Any other chip: the chip name with `effect = null`.
- **Auto-subs:** `manager_auto_sub` rows at N, with the player names and the incoming
  player's points.
- **Table:**
  - Rows are ordered by `total_points` at N, descending. The rank is a competition rank
    (1, 2, 2, 4). Ties are ordered by name for display.
  - `movement` is the rank at N−1 minus the rank at N. Both ranks are computed over the same
    members who have a `total_points` at N−1.
  - `movement` is null for every member at the season's first gameweek (N = 1, or no
    member has a row at N−1). It is also null for a member with no row at N−1.
  - The top 3 come with `behind_leader` in points.
  - The climbers are the members with the maximum positive movement. The fallers are the
    members with the most negative movement. Both are empty when no member moved.
- **Season, gameweeks 1..N:**
  - Each gameweek uses the same member rule, the league's members with a team that
    gameweek.
  - `wins` and `flops` count the gameweeks where the member was among the winners or the
    flops.
  - The current streaks of wins, flops and captain blanks count the consecutive gameweeks
    that end at N and meet the condition. A gameweek without a team breaks a streak.
  - Records: the season's best and worst net points of a member in one gameweek (manager,
    gameweek, net points), with ties giving several.
- **Empty sections:** `empty_sections` lists the keys of the sections with nothing
  notable, from `winners`, `flops`, `captaincy`, `bench_transfers_chips` and `table`.
  `bench_transfers_chips` is empty when it has no bench, hit, transfer miss, chip or
  auto-sub item.
- **Ranking:** each list in the sheet is already in the ranked order. The writer picks from
  the top. The orders not given above, so that the sheet is deterministic:
  - winners and flops: by manager name;
  - captain picks: by `points` descending, then manager name; `best` and `worst`: by
    manager name;
  - chips: by `effect` descending with `null` last, then manager name;
  - auto-subs: by `player_in_points` descending, then manager name;
  - season rows: by `wins` descending, then `flops` ascending, then manager name; records:
    by gameweek, then manager name;
  - climbers and fallers: by manager name.

**Fact sheet shape (`app/presser/facts/schema.py`, all models `extra="forbid"`):**

```python
class ManagerScore(BaseModel): manager: str; points: int; transfers_cost: int; net_points: int
class CaptainPick(BaseModel): manager: str; player: str; base_points: int; multiplier: int
    points: int; triple_captain: bool; vice_stepped_in: bool; blank: bool
class Captaincy(BaseModel): picks: list[CaptainPick]; best: list[str]; worst: list[str]
class BenchItem(BaseModel): manager: str; points_on_bench: int
class Hit(BaseModel): manager: str; transfers: int; cost: int
class TransferMiss(BaseModel): manager: str; player_in: str; player_in_points: int
    player_out: str; player_out_points: int; difference: int
class ChipPlay(BaseModel): manager: str; chip: str; effect: float | None
class AutoSub(BaseModel): manager: str; player_out: str; player_in: str; player_in_points: int
class BenchTransfersChips(BaseModel): bench: list[BenchItem]; hits: list[Hit]
    transfer_misses: list[TransferMiss]; chips: list[ChipPlay]; auto_subs: list[AutoSub]
class TableRow(BaseModel): rank: int; manager: str; total_points: int; movement: int | None
class TopThree(BaseModel): manager: str; total_points: int; behind_leader: int
class Table(BaseModel): rows: list[TableRow]; top3: list[TopThree]
    climbers: list[str]; fallers: list[str]
class SeasonRow(BaseModel): manager: str; wins: int; flops: int; win_streak: int
    flop_streak: int; captain_blank_streak: int
class Record(BaseModel): manager: str; gameweek: int; net_points: int
class Season(BaseModel): rows: list[SeasonRow]; best_gameweek: list[Record]
    worst_gameweek: list[Record]
class FactSheet(BaseModel): version: Literal[1]; league: str; season: str; gameweek: int
    managers: int; average_points: float; winners: list[ManagerScore]
    flops: list[ManagerScore]; captaincy: Captaincy; bench_transfers_chips: BenchTransfersChips
    table: Table; season_facts: Season; empty_sections: list[str]
```

`check_fact_sheet(sheet) -> list[str]` (same file) returns the internal inconsistencies:

- winners not on the maximum net points of the table members;
- flops not on the minimum;
- table ranks not ascending, or a rank that does not match the points;
- `top3.behind_leader` not matching the table;
- a manager in a section who is not in the table;
- captain `points != base_points × multiplier`;
- a wrong `empty_sections`.

The evaluation set test uses it (AC16).

**Presser table (`app/presser/models.py`, migration `0011_presser.py`):**

- `id`: int, primary key.
- `season`: str, FK `season.label`.
- `league_fpl_id`: int, with the FK `(season, league_fpl_id)` → `league`.
- `gameweek_fpl_id`: int, with the FK `(season, gameweek_fpl_id)` → `gameweek`.
- `idempotency_key`: str, indexed.
- `facts`: JSONB, not null.
- `text`: str, nullable.
- `model`: str.
- `prompt_version`: str.
- `input_tokens`, `output_tokens`: int, nullable.
- `cost_usd`: float, nullable.
- `latency_seconds`: float, nullable.
- `status`: str (`generated | sent | failed`).
- `error_class`: str, nullable.
- `delivery_log_id`: int, nullable, FK `delivery_log.id`.
- `trace_id`: str, nullable.
- `created_at`: utc.

The index `ix_presser_league_gameweek` covers
`(season, league_fpl_id, gameweek_fpl_id, created_at)`.

**Configuration (`app/presser/config.py`):**

`PresserSettings(LlmSettings)` adds three fields:

- `presser_enabled: str = "true"`.
- `presser_model: str = "openai/gpt-6-luna"` (the `DEFAULT_PRESSER_MODEL` constant).
- `presser_nicknames: str = ""`. This is a JSON object of entry ID to nickname, for example
  `{"123": "Bartas"}`. It is a `str`, so pydantic never echoes the value in an error.

`parse_nicknames` raises `ConfigError` with one of three messages. None of them carries
the value:

- `"PRESSER_NICKNAMES must be a JSON object of FPL entry ID to nickname"`, for a value that
  is not a JSON object.
- `"... with non-empty nicknames of at most 30 characters"`, for a key that is not a
  positive integer or a nickname that is empty or longer than 30 characters.
- `"PRESSER_NICKNAMES has a duplicate nickname"`, for two nicknames that are equal ignoring
  case.

`presser_disabled_reason(settings, delivery: bool)` checks in order:

1. `"PRESSER_ENABLED=false"`. A value other than true/false raises a `ConfigError`.
2. `"OPENROUTER_API_KEY is not set"`.
3. `"delivery disabled"`.
4. Otherwise it returns None.

`PRESSER_MODEL` must have a row in `model_settings.toml`. The
`single_model_config(..., variable="PRESSER_MODEL")` call raises otherwise.

**Logs (AC14, CONVENTIONS).** Presser log lines carry only the gameweek, the league ordinal
in `FPL_LEAGUE_IDS` (`league=#1`), the status and the error class. They never carry the
league ID, the key (it contains the league ID), names or the text.

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 5, 13 | `tests/presser/test_facts_gameweek.py::test_winners_by_net_points_with_tie`, `::test_manager_without_team_in_no_section`; `tests/presser/test_cli.py::test_facts_prints_sheet` |
| AC2 | 5 | `tests/presser/test_facts_gameweek.py::test_captain_points_with_multiplier`, `::test_triple_captain_marked`, `::test_vice_credited_when_captain_played_zero_minutes` |
| AC3 | 6 | `tests/presser/test_facts_gameweek.py::test_transfer_misses_ordered_by_difference`, `::test_hits_with_cost` |
| AC4 | 6 | `tests/presser/test_facts_gameweek.py::test_chip_effects` (bboost, 3xc, freehit, wildcard) |
| AC5 | 7 | `tests/presser/test_facts_table.py::test_table_from_total_points_with_movement`, `::test_no_movement_at_first_gameweek`, `::test_top3_gaps_climber_faller` |
| AC6 | 7 | `tests/presser/test_facts_table.py::test_season_wins_flops_and_streaks`, `::test_captain_blank_streak_threshold` |
| AC7 | 3, 4, 7, 12, 13 | `tests/presser/test_config.py::test_invalid_nicknames_error_names_variable_not_value`; `tests/presser/test_names.py`; `tests/presser/test_facts_table.py::test_nicknames_used_everywhere`; `tests/worker/test_cli.py::test_invalid_presser_nicknames_stops_worker`; `tests/presser/test_cli.py::test_invalid_nicknames_stops_cli` |
| AC8 | 8, 9 | `tests/presser/test_writer.py::test_input_holds_facts_glossary_examples_previous`, `::test_trace_created`; `tests/presser/test_service.py::test_generated_presser_stored_with_usage`, `::test_previous_pressers_latest_two_sent_before_gameweek` |
| AC9 | 11, 12 | `tests/presser/test_trigger.py::test_sends_one_per_league_latest_gameweek`, `::test_second_trigger_sends_nothing`, `::test_older_gameweek_sends_nothing`; `tests/presser/test_worker_run.py::test_catch_up_sends_only_latest_gameweek_and_restart_sends_nothing` |
| AC10 | 9, 11 | `tests/presser/test_service.py::test_writer_failure_recorded_failed_and_logged_by_class`; `tests/presser/test_trigger.py::test_failure_carries_on_and_is_not_retried` |
| AC11 | 3, 12, 13 | `tests/presser/test_config.py::test_disabled_reasons`; `tests/worker/test_cli.py::test_presser_disabled_logged_once_and_in_status`; `tests/presser/test_cli.py::test_status_says_why_disabled` |
| AC12 | 10 | `tests/presser/test_render.py::test_title_text_and_whatsapp_button`; `tests/content/test_presser_email.py` |
| AC13 | 13 | `tests/presser/test_cli.py::test_preview_neither_sends_nor_marks_sent`, `::test_send_older_gameweek_then_already_sent` |
| AC14 | 12 | `tests/presser/test_worker_run.py::test_logs_carry_no_names_or_text` |
| AC15 | 8, 10, 14 | `tests/content/test_presser_content.py`; `tests/presser/test_no_polish_literals.py` |
| AC16 | 15, 16, 17 | `tests/presser/evaluation/test_cases.py`; `tests/presser/evaluation/test_building.py::test_build_pseudonymises`; `tests/presser/evaluation/test_eval_set.py` |
| AC17 | 18, 19, 20 | `tests/presser/evaluation/test_runner.py`; `tests/presser/evaluation/test_cli.py::test_evaluate_writes_result`, `::test_review_records_rating_and_note`, `::test_summary_reports_and_applies_thresholds` |
| AC18 | 19 | `tests/presser/evaluation/test_cli.py::test_judge_review_records_verdicts_and_agreement` |
| AC19 | 1, 3, 20 | `tests/llm/test_presser_catalogue.py`; `tests/presser/test_config.py::test_default_model`; `tests/presser/evaluation/test_summary.py::test_cheapest_passing_model_wins` |
| AC20 | 21 | `tests/test_env_example.py::test_every_presser_setting_*`; `tests/test_readme.py::test_presser_documented` |

## Steps

Every test step runs with Docker available. Tests that need PostgreSQL use the `db` fixture.
The command in each step is the contract. After a step's own tests pass, the step also runs
`cd backend && uv run ruff check . && uv run ruff format --check .`.

- [x] 1. **Model catalogue: five candidates and the judge.** `iterations: 0`
      - Fetch the public model list (`curl -s https://openrouter.ai/api/v1/models`, no
        credentials) and add rows to `backend/app/llm/model_settings.toml` for
        `anthropic/claude-haiku-5.5`, `deepseek/deepseek-v4.1-flash`,
        `google/gemini-3.8-flash`, `mistralai/mistral-large-4-0` and `openai/gpt-6.1-sol`.
        Follow the file's header rules for `reasoning_effort` and `temperature`. Each row has
        `checked = "2026-10-09"`.
      - Add rows to `backend/app/llm/prices.toml` with the SPEC's prices, `checked =
        "2026-10-09"`. Set the `openai/gpt-6-luna` price row's `checked` to `"2026-10-09"`
        (unchanged $0.10/$0.50).
      - Keep `openai/gpt-6-luna`'s model_settings row.
      - If the model list is unreachable, or a model ID is missing from it, end with
        `RESULT: ESCALATE`, `KIND: tooling`. Do not guess the settings.
      - Test first: `backend/tests/llm/test_presser_catalogue.py` asserts that the six IDs
        are in both files. It asserts each price equals the SPEC's and `checked ==
        "2026-10-09"` in `prices.toml`.
      Automatic verification: `cd backend && uv run pytest -q tests/llm`

- [x] 2. **Migration 0011 and the `presser` model (data migration, its own step).** `iterations: 0`
      - Add `backend/app/presser/__init__.py` (empty) and `backend/app/presser/models.py`
        with the `Presser` table above.
      - Write `backend/migrations/versions/0011_presser.py` with `down_revision = "0010"`.
        The downgrade drops the index and the table.
      - Add `import app.presser.models` to `backend/migrations/env.py` and to
        `backend/tests/db/test_migrations.py`.
      - In `test_migrations.py`, change `PRE_0010_TABLES` to exclude
        `{"list_membership", "presser"}`.
      - Add `test_presser_migration_adds_only_new_table`, in the shape of
        `test_delivery_migration_adds_only_new_table`. It upgrades to 0010, seeds the
        bootstrap and compares the other tables before and after the upgrade to 0011. It
        checks that `presser` exists after the upgrade and is gone after `downgrade 0010`.
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_migrations.py`

- [x] 3. **Presser configuration (AC7 parsing, AC11 reasons, AC19 default).** `iterations: 0`
      - Write `backend/app/presser/config.py` as specified in Approach → Configuration. It
        has `PresserSettings`, `DEFAULT_PRESSER_MODEL`, `parse_nicknames`,
        `presser_disabled_reason` and `resolve_presser_llm(settings) -> LlmConfig | None`
        (`single_model_config` with `variable="PRESSER_MODEL"`).
      - Test first: `backend/tests/presser/__init__.py` and `test_config.py`. Its cases are
        `test_default_model`, valid JSON parsing, and
        `test_invalid_nicknames_error_names_variable_not_value`. The invalid values are not
        JSON, a list, a key `"abc"`, an empty nickname, a 31-character nickname and duplicate
        nicknames. The message contains `PRESSER_NICKNAMES` and never the raw value or the
        nickname. It also has `test_disabled_reasons` (each of the three reasons and None)
        and an invalid `PRESSER_ENABLED`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_config.py`

- [x] 4. **Display names (AC7).** `iterations: 0`
      - Write `backend/app/presser/names.py` with `display_names(managers: dict[int, str],
        nicknames: dict[int, str]) -> dict[int, str]`. The argument maps entry ID to
        `manager_name`.
      - The rules: the nickname, otherwise the first word of `manager_name`, and the
        collision rule from design choice 5. An empty `manager_name` falls back to
        `"Manager"` plus the collision rule. This is not a Polish word, so it stays a code
        constant.
      - Test first: `backend/tests/presser/test_names.py`. It covers a nickname, the first
        word, a collision on the first word ("Tomasz K.", "Tomasz W."), a collision on the
        initial too ("… 2") and a nickname that keeps priority.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_names.py`

- [x] 5. **Fact sheet schema, loading, winners, flops and captaincy (AC1, AC2).** `iterations: 0`
      - Create the package `backend/app/presser/facts/` with these files:
        - `__init__.py` re-exports `FactSheet`, `build_fact_sheet`, `check_fact_sheet`,
          `NoFactsError` and `FactSheetError`.
        - `schema.py` holds the models above and `check_fact_sheet`.
        - `load.py` holds the SQL loads. One query each gets the members of league L at
          gameweek g with `manager_gameweek` and the display names. Others get the captain
          and vice picks with result minutes and points, the picks of positions 12–15, the
          transfers, the auto-subs and the per-player results. Each load takes
          `(session, season, league_id, gameweeks)`.
        - `gameweek.py` holds the winners, flops, average and captaincy rules.
      - At this step `build_fact_sheet(session, season, league_id, gameweek, nicknames)`
        fills only these sections. The other sections are empty models.
      - Test first:
        - `backend/tests/presser/helpers.py` holds the seeding helpers on the `db` fixture.
          The helpers seed a season with gameweeks 1..N, players with
          `player_gameweek_result`, a league and managers with synthetic names, and
          `manager_gameweek` rows with points, cost, bench, chip and `has_team`. They also
          seed picks with captain and vice, transfers and auto-subs. They are written as
          small builders like `tests/alerts/helpers.py::seed_league`.
        - `backend/tests/presser/test_facts_gameweek.py` has
          `test_winners_by_net_points_with_tie` (two managers on the same top net score, one
          of them through a hit) and `test_manager_without_team_in_no_section`.
        - It also has `test_captain_points_with_multiplier`, `test_triple_captain_marked`
          and `test_vice_credited_when_captain_played_zero_minutes`. The last one also
          covers both captain and vice on 0 minutes.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_gameweek.py`

- [x] 6. **Bench, hits, transfer misses, chips and auto-subs (AC3, AC4).** `iterations: 0`
      - Extend `backend/app/presser/facts/gameweek.py` with the rules above, then fill
        `bench_transfers_chips` and its entry in `empty_sections`.
      - Test first: add these to `test_facts_gameweek.py`:
        - `test_transfer_misses_ordered_by_difference`: a transfer out of a player who
          outscored the incoming one, a good transfer that is not listed, and the order by
          difference.
        - `test_hits_with_cost`.
        - `test_chip_effects`: bboost bench points, 3xc extra, freehit and wildcard against
          the average, and an unknown chip with a null effect.
        - `test_bench_excludes_bench_boost`, `test_auto_subs_listed` and
          `test_empty_section_marked`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_gameweek.py`

- [x] 7. **Table, season facts and the complete sheet (AC5, AC6, AC7 in the sheet).** `iterations: 0`
      - Write `backend/app/presser/facts/table.py` (table, movement, top 3, climbers and
        fallers) and `facts/season.py` (wins, flops, streaks and records over gameweeks
        1..N).
      - `build_fact_sheet` now fills every section and `empty_sections`, and it ends by
        raising `FactSheetError` (exported next to `NoFactsError`, message without names)
        when `check_fact_sheet(sheet)` is not empty. It is not an `assert`, which `python
        -O` strips.
      - Test first: `backend/tests/presser/test_facts_table.py` has these tests:
        - `test_table_from_total_points_with_movement`;
        - `test_no_movement_at_first_gameweek`;
        - `test_top3_gaps_climber_faller`;
        - `test_season_wins_flops_and_streaks` (a tie counts as a win for both, and a
          gameweek without a team breaks the streak);
        - `test_captain_blank_streak_threshold` (2 points is a blank, 3 is not);
        - `test_records`;
        - `test_nicknames_used_everywhere`. It dumps the sheet to JSON and asserts the
          nickname appears and no `manager_name` or `team_name` of the seeded managers does.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_table.py tests/presser/test_facts_gameweek.py`

- [x] 8. **Writer prompt, writer and tracer (AC8 input and trace, AC15 content).** `iterations: 0`
      - Write `backend/app/content/prompts/presser_writer.md` with a `version: 1` header.
        The instructions are in English, like the other prompts. The rules:
        - Write in Polish FPL slang, in the language of the style examples and the glossary.
        - Use "autosub", never "autozmiana".
        - Banter only about FPL decisions (captain, transfers, bench, chips, hits), never
          about personal traits, and no vulgar words.
        - At most 1500 characters.
        - Cover the non-empty sections in the order winners → flop → captaincy →
          bench/transfers/chips → table and season race. Pick the top-ranked items.
        - Use only facts from the fact sheet or the previous pressers. Continue running
          jokes, but do not repeat the same joke.
        - Name managers exactly as the sheet does.
      - Write `backend/app/presser/writer.py`:
        - `PresserDraft(BaseModel)` has the single field `text: str`.
        - `WriterInput` is a dataclass with `facts: FactSheet` and
          `previous: list[PreviousPresser]`, where `PreviousPresser` has `gameweek` and
          `text`.
        - `render_input(item, glossary, style_examples) -> str` builds the human message.
          It has four headed parts in this order: the fact sheet JSON
          (`model_dump_json(indent=1)`), the glossary (one line per term:
          `term — meaning — example`), the style examples text, and the previous pressers
          ("GW<n>:" + text, oldest first, or the line "none").
        - `load_glossary()` and `load_style_examples()` read `app/content/` and are cached.
        - `PROMPT_VERSION = f"presser_writer@{version}"`.
        - `Writer` and `build_writer(caller: StructuredCaller) -> Writer` form a one-node
          `StateGraph`, as `app/corroboration/judge.py`. `Writer.run(item) ->
          StructuredReply[PresserDraft]`.
      - Write `backend/app/presser/tracing.py` in the pattern of
        `app/corroboration/tracing.py`:
        - The `PresserTracer` protocol has `span(name, input)` and
          `generation(model, input, output, usage, cost_usd, latency_seconds, error_class)`
          (the generation is named `presser-writer`) and `flush()`. The corroboration
          pattern starts and ends the generation after the call, so its own duration is
          about 0. The SPEC asks for latency in the trace, so the generation carries
          `metadata={"latency_seconds": …}`, and the `presser` span wraps the writer call.
        - The implementations are `NullPresserTracer` and `LangfusePresserTracer`. A tracing
          failure is logged by class name and never fails the work.
        - `make_presser_tracer(tracing)` builds the tracer.
      - Test first:
        - `backend/tests/content/test_presser_content.py` checks that the prompt loads with a
          version, and that the glossary has exactly 70 terms, each with `term`, `meaning`,
          `example` and `source` not empty. It checks that the style examples have 3
          `## Example` headings.
        - `backend/tests/presser/test_writer.py::test_input_holds_facts_glossary_examples_previous`
          uses `FakeChatModel`. It asserts that the received human message contains the
          sheet JSON, a glossary term, a style-example line and the previous presser text
          labelled "GW4", and that the system message is the prompt.
        - `::test_trace_created` uses `FakeLangfuseClient` and asserts a `presser-writer`
          generation with model, usage, cost and `metadata.latency_seconds` under a
          `presser` span.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_writer.py tests/content/test_presser_content.py`

- [ ] 9. **Store and generation service (AC8 stored row, AC10 failure).**
      - Write `backend/app/presser/store.py` with these functions:
        - `insert_presser(engine, **fields) -> int` and `update_presser(engine, id, **fields)`;
        - `previous_pressers(session, season, league_id, gameweek, limit=2)`, which returns
          the latest `sent` row per gameweek before `gameweek`, the newest two, as
          `PreviousPresser` oldest first;
        - `key_has_status(session, key, statuses) -> bool`;
        - `presser_status(session, season, league_ids)`, the latest row per league plus the
          number of `failed` rows.
      - Write `backend/app/presser/service.py`:
        - `presser_key(season, gameweek, league_id) ->
          f"presser:{season}:gw{gameweek}:league{league_id}"`.
        - `PresserRuntime` is a dataclass with `writer: Writer`, `model: str`,
          `tracer: PresserTracer`, `nicknames: dict[int, str]`,
          `delivery: DeliveryService | None`, `league_ids: list[int]` and
          `stop_event: threading.Event`.
        - `generate_presser(engine, runtime, season, league_id, gameweek) -> GeneratedPresser`.
          It builds the sheet, loads the previous pressers and runs the writer inside the
          tracer span. It measures the latency with `time.monotonic()` and inserts the row
          with the status `generated`, the usage, the cost, the latency, the model, the
          prompt version and the trace ID.
        - When the writer raises after its retries, `generate_presser` inserts the row with
          the status `failed` and `error_class`. It logs
          `presser generation failed: gameweek=%s league=#%s error=%s` (class name) and
          raises `PresserFailed`. If `runtime.stop_event` is set, it records no row and
          raises `PresserStopped`, so a stop never records `failed`.
      - Test first: `backend/tests/presser/test_service.py`:
        - `test_generated_presser_stored_with_usage` checks the text, model, tokens, cost
          (from `prices.toml` for `openai/gpt-6-luna`), latency ≥ 0, `generated` status and
          the facts JSON.
        - `test_previous_pressers_latest_two_sent_before_gameweek`: sent rows at GW1, 2 and
          3, a failed one at GW4, and one at GW6, for gameweek 5. GW2 and GW3 are given. At
          GW1 none is given.
        - `test_writer_failure_recorded_failed_and_logged_by_class` uses the `caplog`
          fixture. Every scripted attempt raises. The test asserts a `failed` row and that
          the log has the class name and no manager name.
        - `test_stop_records_nothing`.
        - All of them use `FixedClock`, so no real sleep happens.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_service.py`

- [ ] 10. **E-mail template and renderer (AC12, AC15).**
      - Write `backend/app/content/presser_email.toml`:
        - `[title] presser = "Presser GW{gameweek} — {league}"`;
        - `[whatsapp] button = "📲 Wyślij na WhatsApp"`;
        - `[footer] text`;
        - `[html] document` / `share` / `paragraph`, in the visual style of
          `alert_email.toml`'s header and share button.
      - Write `backend/app/presser/render.py` with `render_presser(league_name, gameweek,
        text) -> Message`:
        - The title comes from the template.
        - `text` is the presser unchanged.
        - The HTML has the escaped presser, with each line a paragraph or `<br>`, and the
          share button. The button's `https://wa.me/?text=` URL carries the presser,
          `quote(text, safe="")`, escaped into the attribute.
      - Test first:
        - `backend/tests/content/test_presser_email.py` checks that the template is valid
          TOML and that every leaf is not empty.
        - `backend/tests/presser/test_render.py::test_title_text_and_whatsapp_button` checks
          the exact title for GW6 and a league name with `&`, `text == presser`, and the HTML
          containing the escaped presser and an `href` starting with
          `https://wa.me/?text=`. The URL-decoded text equals the presser.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_render.py tests/content/test_presser_email.py`

- [ ] 11. **Send and the post-league-sync trigger (AC9, AC10 at service level).**
      - Add to `backend/app/presser/service.py`:
        - `send_presser(engine, runtime, season, league_id, gameweek, *, skip_statuses) ->
          str`. It returns `sent`, `failed`, `already_sent` or `skipped`.
          - If a row with the key has a status in `skip_statuses`, it returns `skipped`.
            For `send` that status is `("sent",)`, and the result `skipped` is reported as
            `already_sent`.
          - Otherwise it generates, renders with the league name from `league`, and calls
            `runtime.delivery.send(key, "presser", message)`.
          - It updates the row to `sent` with `delivery_log_id`. On a delivery `failed` the
            row becomes `failed` with the delivery error class. On `already_sent` the row
            stays `generated` and the result is `already_sent`.
          - It logs `presser %s: gameweek=%s league=#%s`.
        - `latest_finished_gameweek(session, season) -> int | None` (max `fpl_id` with
          `finished`).
        - `run_after_league_sync(engine, runtime, season, gameweek) -> None`:
          - If `gameweek != latest_finished_gameweek`, it logs
            `presser skipped: gameweek=%s is not the latest finished` and returns.
          - Otherwise, for each league in `runtime.league_ids` (stopping on `stop_event`),
            it calls `send_presser(..., skip_statuses=("sent", "failed"))`.
          - It catches `PresserFailed`, `NoFactsError` and any other `Exception` per league.
            It logs the class and continues with the next league.
      - Test first: `backend/tests/presser/test_trigger.py` seeds two leagues with GW1–5
        finished, uses `FakeChannel` and `FakeChatModel`, and has these tests:
        - `test_sends_one_per_league_latest_gameweek`: two deliveries with the keys
          `presser:2026/27:gw5:league<id>`, and rows `sent`.
        - `test_second_trigger_sends_nothing`: a second call makes no LLM call and no
          delivery.
        - `test_older_gameweek_sends_nothing`.
        - `test_failure_carries_on_and_is_not_retried`: league 1's writer fails and league 2
          is sent. A second trigger makes no LLM call for league 1.
        - `test_delivery_failure_marks_failed`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_trigger.py`

- [ ] 12. **Worker wiring (AC9 end to end, AC11 and AC7 in the worker, AC14).**
      - `backend/app/worker/loop.py`:
        - `Worker.__init__` takes `after_league_sync: Callable[[str, int], None] | None =
          None`.
        - `_run_job` keeps the `RunRecord` from `run_job`. When the job is `league_sync`,
          the outcome is `succeeded` and the hook is set, it calls
          `hook(action.season, action.gameweek)`. A non-`Shutdown` exception from the hook
          is logged by class name (`presser hook failed: %s`) and swallowed.
      - `backend/app/worker/cli.py`:
        - `PresserSetup(config_model, nicknames, make_runtime)` is added. `WorkerDeps` gains
          `presser: PresserSetup | None = None` and `presser_disabled_reason: str | None =
          None`.
        - `_deps_from_settings` loads `PresserSettings` and parses the nicknames, so an
          invalid value fails at start through the existing `fail(...)`. It computes
          `presser_disabled_reason(..., delivery=delivery_config is not None)`.
        - `run` builds the runtime when it is enabled. That is a separate channel from
          `build_channel(delivery_config)`, a `DeliveryService` with the worker's
          `stop_event`, the writer from `resolve_presser_llm` + `build_chat_model` +
          `StructuredCaller.from_spec`, the tracer and the league IDs. `from_spec` gives
          the caller a fresh stop event, so `run` passes `StopAwareClock(stop_event)` as the
          caller's clock and sets `caller.stop_event = stop_event`: a SIGTERM then ends the
          writer's back-off at once and reaches the `PresserStopped` path instead of
          finishing the retries. It logs
          `presser enabled: model=%s` or `presser disabled: %s` once. It passes
          `after_league_sync=lambda season, gw: run_after_league_sync(...)` to `Worker` and
          closes the channel in `finally`.
        - `status` prints `Presser: disabled (<reason>)` or `Presser: model=<m>  last:
          GW<n> <status>  failed: <k>`, with `never` when there is no row.
      - Test first:
        - `backend/tests/worker/test_loop.py::test_league_sync_success_calls_hook_once`
          records the calls (season, 6) after the GW6 league sync. A failing league sync
          calls nothing. A hook raising `ValueError` does not stop the worker.
        - `backend/tests/presser/test_worker_run.py` uses `tests/worker/sim.py`.
          `test_catch_up_sends_only_latest_gameweek_and_restart_sends_nothing` runs the
          catch-up from an empty DB at 2026-09-27 and asserts that exactly one presser is
          sent, for GW5 of `LEAGUE_ID`. A second worker run sends nothing and calls the LLM 0
          times.
        - `test_logs_carry_no_names_or_text` uses `caplog` at DEBUG over the same run with a
          nickname for `ENTRY_IDS[0]`. It asserts that the log has no `Synthetic` at all
          (this covers `Synthetic Manager`, `Synthetic XI`, `Synthetic League` and the
          collision-rule display name of `ENTRY_IDS[1]`, which starts with `Synthetic`), no
          nickname, no `LEAGUE_ID`, no entry ID from `ENTRY_IDS` and no fake-presser marker
          text.
        - `backend/tests/worker/test_cli.py::test_presser_disabled_logged_once_and_in_status`
          and `::test_invalid_presser_nicknames_stops_worker` (exit 1, the message names
          the variable, not the value).
      Automatic verification: `cd backend && uv run pytest -q tests/worker tests/presser/test_worker_run.py`

- [ ] 13. **Presser CLI (AC1 via `facts`, AC7, AC11 and AC13).**
      - Write `backend/app/presser/cli.py` and `__main__.py` (`python -m app.presser`), a
        typer app with injectable `PresserCliDeps` like `app/alerts/cli.py`. The deps are
        engine, settings, nicknames, `make_runtime(with_delivery: bool)`, clock and league
        IDs. An invalid `PRESSER_NICKNAMES` → `fail(...)` exits with 1.
      - The season is always the current one (`Season` max label).
      - The commands:
        - `facts --league ID --gameweek N`: prints the sheet JSON.
        - `preview --league ID --gameweek N`: generates (the row stays `generated`) and
          prints the text, then `chars=<n> cost=$… latency=…s`. No delivery is built.
        - `send --league ID --gameweek N`: calls `send_presser(...,
          skip_statuses=("sent",))` and prints `sent`, `failed` or `already_sent`.
          `failed` exits with 1.
        - `status`: prints `Presser: enabled (model=<m>)` or `Presser: disabled
          (<reason>)`, then one line per configured league with the latest presser
          (gameweek, status, time in Warsaw, model, cost) and the failures.
        - `preview` and `send` fail with the disabled reason when the key is missing.
          `send` also fails when delivery is disabled.
      - Test first: `backend/tests/presser/test_cli.py` (CliRunner, injected deps, `db`):
        - `test_facts_prints_sheet` checks the winners in the JSON.
        - `test_preview_neither_sends_nor_marks_sent` checks no `FakeChannel` call, the row
          status `generated` and `delivery_log` empty.
        - `test_send_older_gameweek_then_already_sent` covers GW3 while GW5 is the latest:
          the key `presser:<season>:gw3:league<id>` is sent, then a second `send` prints
          `already_sent` with no LLM call.
        - `test_status_says_why_disabled` and `test_invalid_nicknames_stops_cli`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_cli.py`

- [ ] 14. **No Polish literals in presser code (AC15 guard).**
      - `backend/tests/presser/test_no_polish_literals.py` parses every
        `backend/app/presser/**/*.py`. It asserts that no string constant contains a Polish
        diacritic (`ąćęłńóśźżĄĆĘŁŃÓŚŹŻ`). It also asserts that the files
        `content/prompts/presser_writer.md`, `content/prompts/presser_judge.md` (created in
        step 18, so add that assertion there), `content/presser_email.toml`,
        `content/presser_glossary.toml` and `content/presser_style_examples.md` exist.
      - Fix any literal the test finds by moving it to `app/content/`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_no_polish_literals.py`

- [ ] 15. **Evaluation case model (AC16 structure).**
      - Write `backend/app/presser/evaluation/__init__.py` and `cases.py`:
        - `PresserCase` (`extra="forbid"`) has `id`, `split: Literal["dev","test"]`,
          `source: Literal["real","synthetic"]`, `tags: list[str]`, `facts: FactSheet` and
          `previous: list[PreviousPresser]` (at most 2).
        - `EVALS_DIR = backend/evals/presser`. `DEFAULT_CASES_PATH = v1/cases.jsonl`,
          `HISTORY_PATH = v1/history.jsonl` and `PSEUDONYMS_PATH = v1/pseudonyms.toml`.
        - `load_cases`, `write_cases` (atomic, as in the corroboration `cases.py`), and
          `HistoryEntry(league, gameweek, text)` with load and write.
        - `composition_problems(cases)` reports:
          - a case count outside 14–18;
          - a count of `real` cases other than 10;
          - fewer than 5 `synthetic` cases;
          - duplicate IDs;
          - a split with no real or no synthetic case;
          - a test split with fewer than 8 cases;
          - a case whose `check_fact_sheet` is not empty;
          - a `previous` entry whose gameweek is not below the case's gameweek;
          - a missing edge-case tag, from `tie_win`, `low_captain`, `all_negative`,
            `chip_flop`, `first_gameweek` and `no_team`.
      - Test first: `backend/tests/presser/evaluation/__init__.py` and `test_cases.py`. It
        checks a round trip and that each problem is reported. The cases are built in the
        test from a tiny valid `FactSheet` factory in `tests/presser/evaluation/factories.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation/test_cases.py`

- [ ] 16. **`build-cases`: real cases from the database, pseudonymised (AC16 real part).**
      - Write `backend/evals/presser/v1/pseudonyms.toml` with `managers = [...]`, at least
        30 synthetic Polish first names or nicknames, and `leagues = [...]`, at least 4
        synthetic league names. It is test data, not product content.
      - Write `backend/app/presser/evaluation/building.py` with `build_real_cases(engine,
        season, gameweeks, pseudonyms, history, seed) -> BuiltCases`:
        - It takes every league in the `league` table for the season, ordered by `fpl_id`,
          with the alias `l1`, `l2`, …
        - It builds each sheet with `build_fact_sheet(..., nicknames=<entry_id → pseudonym>)`.
          The pseudonyms are assigned per league in entry ID order from the shuffled pool,
          with a fixed seed. It sets `league` to the league pseudonym.
        - Case IDs are `real-<alias>-gw<N>`.
        - It attaches `previous` from `history` for `(alias, gw)`, the newest two below N.
          It reports the missing history entries.
        - It splits the cases deterministically, 5 dev and 5 test, spread over both leagues
          and the gameweeks.
        - It ends with a leak check over every case, using the real `manager_name`,
          `team_name` and league names read from the DB. The sheet legitimately holds real
          player names (captains, transfers, auto-subs), and team names often contain player
          names ("Salah's Army"), so a plain word search over the whole JSON would raise on
          clean data. The check has two parts:
          - Full strings: no real `manager_name`, `team_name` or league name (whole, compared
            ignoring case) appears anywhere in the case JSON or the case ID.
          - Words: every name-bearing field (`league`, every `manager` field, and the
            `best`, `worst`, `climbers` and `fallers` lists) must be a pool entry, and no
            word of 3 or more letters of a real `manager_name` or league name that is not a
            pool entry appears as a whole word in the `previous` texts.
          On a hit it raises `PseudonymisationError`. The error names the case ID only.
      - Add `backend/app/presser/evaluation/cli.py` and `__main__.py`
        (`python -m app.presser.evaluation`) with the command `build-cases [--gameweeks
        1-5] [--output] [--force]`. It merges the real cases with the synthetic cases
        already in the output file and prints only the counts, the case IDs and the missing
        history entries, never a name.
      - Test first: `backend/tests/presser/evaluation/test_building.py::test_build_pseudonymises`
        seeds two leagues with distinctive names ("Zenobiusz Realny", team "Realni FC",
        league "Prawdziwa Liga"). It asserts that the output JSON contains none of them, that
        every manager in the sheets is from the pool, and that the IDs carry no league or
        entry ID. `::test_leak_raises` monkeypatches the pseudonymisation to keep a real
        name and expects `PseudonymisationError`.
        `::test_team_name_with_player_word_is_not_a_leak` gives a seeded manager the team
        "<captain's player name> Army" and asserts the build succeeds while the player
        name stays in the captaincy section.
      - Then run the builder on the local development database (`compose.yaml`: user,
        password and database `presser` on `localhost:5432`). If it is not reachable, end
        with `RESULT: ESCALATE`, `KIND: tooling`. Run these commands, from `backend/`:
        1. `env -u OPENROUTER_API_KEY DATABASE_URL=postgresql://presser:presser@localhost:5432/presser uv run alembic upgrade head`
        2. `env -u OPENROUTER_API_KEY DATABASE_URL=postgresql://presser:presser@localhost:5432/presser uv run python -m app.presser.evaluation build-cases --gameweeks 1-5`
      - Read the pseudonymised `cases.jsonl` and write `backend/evals/presser/v1/history.jsonl`
        from the cases of each league's GW1–4. Write one short Polish presser per
        (alias, gameweek): 400–900 characters, in the five sections, using only that
        case's facts and the glossary's slang. Then rerun `build-cases --force`. It must
        report no missing history entry.
      - Never run `python -m app.presser facts` against the development database: it would
        print real names.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation/test_building.py`

- [ ] 17. **Synthetic edge cases and the committed set (AC16).**
      - Add 6 synthetic cases to `backend/evals/presser/v1/cases.jsonl`, written as
        `FactSheet` JSON with pool names and a synthetic league name, each internally
        consistent:
        - `syn-tie-win` (`tie_win`): two winners on the same net score.
        - `syn-low-captain` (`low_captain`): a 1-point captain for most of the league.
        - `syn-all-negative` (`all_negative`): every manager negative after hits.
        - `syn-chip-flop` (`chip_flop`): a bench boost with 0–2 bench points and a triple
          captain blank.
        - `syn-first-gameweek` (`first_gameweek`): GW1, no movement, no previous.
        - `syn-no-team` (`no_team`): a league where one member had no team. The `managers`
          count is lower than the member count, and that member appears nowhere.
        - Each one with GW > 1 gets 1–2 previous pressers written for the project.
      - Tag the real GW1 cases with `first_gameweek` too.
      - The split is 8 dev and 8 test, with 5 real and 3 synthetic in each.
      - Test first: `backend/tests/presser/evaluation/test_eval_set.py`:
        - The committed file loads, and `composition_problems == []`. That covers every
          sheet validating and `check_fact_sheet` empty.
        - Every manager name in every case is in the pseudonym pool, and every league name
          is in the pool's leagues.
        - No case text contains a 6+ digit number, so no entry or league ID.
        - The history in `history.jsonl` matches the `previous` of the real cases.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation`

- [ ] 18. **Judge and `evaluate` (AC17 evaluate part).**
      - Write `backend/app/content/prompts/presser_judge.md` (`version: 1`, English). It
        asks the judge to read the fact sheet, the previous pressers and the presser, and
        to list every factual claim: numbers, who won or flopped, who captained whom and
        the points, transfers, chips, streaks, table positions and gaps. Each claim is
        labelled `supported` (stated by or computable from the sheet or a given previous
        presser) or `unsupported`. Banter and opinions are not claims.
      - Write `backend/app/presser/evaluation/judge.py`:
        - `JudgedClaim(claim: str, label: Literal["supported","unsupported"])` and
          `JudgeVerdict(claims: list[JudgedClaim])`.
        - `JUDGE_MODEL = "openai/gpt-6.1-sol"` and
          `PROMPT_VERSION = "presser_judge@<v>"`.
        - `PresserJudge` and `build_presser_judge(caller)` form a one-node graph.
      - Write `backend/app/presser/evaluation/runner.py`:
        - `run_evaluation(cases, writer, judge, split, model, judge_model, now) -> dict`.
          For each case of the split, it runs the writer on
          `WriterInput(case.facts, case.previous)` and measures the latency. Then it runs
          the judge on the presser.
        - The per-presser record has `case_id`, `text`, `length`, `within_limit`
          (`length <= 1500`), `input_tokens`, `output_tokens`, `cost_usd` (writer),
          `latency_seconds`, `claims` (each `{claim, label, owner_label: null}`),
          `faithfulness`, `judge_cost_usd`, `error_class` and `style: null`.
        - `faithfulness` is supported/all, or `1.0` when there are no claims. It is `null`
          when the judge errored.
        - A writer error records `error_class` and skips the judge.
        - The run-level fields are `model`, `judge_model`, `prompt_version`,
          `judge_prompt_version`, `split`, `date`, `pressers` and `totals`.
        - `default_result_path(split, model)` is `evals/presser/results/<split>-<model
          with / → ->.json`. `write_result` is atomic.
      - Add the command `evaluate --split dev|test --model M [--judge-model J] [--cases]
        [--output]` to the evaluation CLI. Both models are built with `single_model_config`,
        so a model outside the catalogue fails with a clear message. It prints the
        averages and the result path.
      - Add the judge prompt assertion to `test_no_polish_literals.py`.
      - Test first:
        - `backend/tests/presser/evaluation/test_runner.py` uses fake writer and judge
          models (`FakeChatModel`). It checks faithfulness 2/3 for a presser with 3 claims
          and 1 unsupported, the length flag at 1500/1501, the writer error path and the
          judge error path.
        - `backend/tests/presser/evaluation/test_cli.py::test_evaluate_writes_result` uses
          injected deps and `tmp_path` cases and output, and checks the file content.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation/test_runner.py tests/presser/evaluation/test_cli.py tests/presser/test_no_polish_literals.py`

- [ ] 19. **`review` (style) and `judge-review` (AC17 review part, AC18).**
      - Add the command `review --run PATH [--all]` to the evaluation CLI. It shows each
        presser without a style rating, or each one with `--all`: the case ID, the tags,
        a short facts summary (winners, flops, best captain) and the text. It asks
        `rating 1-5 (s skip, q quit)`, then `note (empty for none)`. It saves `style:
        {rating, note}` into the run file after each answer, atomically, and ends with
        `rated: k/n  average: x.xx`.
      - Add the command `judge-review --run PATH [--limit N]`. For each presser without
        owner labels (at most N pressers), it shows the presser and then each claim with
        the judge's label, and asks `[a]gree  [f]lip  [s]kip  [q]uit`. It saves
        `owner_label` per claim after each presser. At the end it prints `reviewed claims:
        n  agreement: x.xx` (the share of reviewed claims where `owner_label == label`) and
        `judge_agreement` into the run's `totals`.
      - Test first: in `backend/tests/presser/evaluation/test_cli.py`:
        - `test_review_records_rating_and_note`: CliRunner input `"4\nfajne\n2\n\n"`, and
          the file then has the ratings, the notes and the null note.
        - `test_review_rejects_out_of_range` asks again on `7`.
        - `test_judge_review_records_verdicts_and_agreement` takes a run recorded by
          `evaluate` with fake models (3 claims), inputs `a`, `f` and `a`, and expects the
          agreement `0.67` printed and stored.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation/test_cli.py`

- [ ] 20. **`summary` with the pass rule (AC17 summary, AC19).**
      - Write `backend/app/presser/evaluation/summary.py`:
        - `summarise(results_dir) -> list[RunSummary]` reads the test-split runs, one per
          model (the file is per model).
        - `RunSummary` has these fields:
          - `model`, `pressers`, `errored`;
          - `faithfulness` (the average over the judged pressers);
          - `style` (the average of the ratings) and `rated`;
          - `within_limit_share`, `avg_cost_usd`, `avg_latency_seconds`;
          - `judge_agreement`;
          - `passes`. It is true when `errored == 0`, every presser is judged and rated,
            `faithfulness >= 0.95` and `style >= 3.5`.
        - `choose(runs) -> str | None` returns the passing run with the lowest
          `avg_cost_usd`. A run with an unknown cost cannot be chosen. A tie goes to the
          higher faithfulness.
        - `FAITHFULNESS_THRESHOLD = 0.95` and `STYLE_THRESHOLD = 3.5`.
      - Add the command `summary [--results-dir]` to the evaluation CLI. It prints one line
        per model with every field, then `winner: <model>` or `no model passes`. On an empty
        directory it prints `no test runs`.
      - Test first:
        - `backend/tests/presser/evaluation/test_summary.py::test_cheapest_passing_model_wins`
          has three recorded runs: a cheap one failing on style 3.4, a mid one passing and
          an expensive one passing. The mid one wins.
        - `::test_unrated_or_errored_does_not_pass` and `::test_thresholds_inclusive`
          (0.95 and 3.5 pass).
        - `test_cli.py::test_summary_reports_and_applies_thresholds`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation`

- [ ] 21. **Documentation and roadmap (AC20).**
      - `backend/.env.example`: add a presser block with the empty placeholders
        `PRESSER_ENABLED=`, `PRESSER_MODEL=` and `PRESSER_NICKNAMES=`. Its comments give the
        default true, the default `openai/gpt-6-luna`, and the JSON format with a synthetic
        example. They say that nicknames never go into the repository.
      - `docs/DEPLOYMENT.md`: add step `13. **Presser (optional).**` It covers:
        - what triggers it (the league sync of the latest finished gameweek), and that the
          first-deploy catch-up (step 6) therefore sends the latest finished gameweek's
          presser once when the presser is enabled at the first start;
        - the three variables and the run conditions;
        - the `presser disabled: …` log line and the `Presser:` status line;
        - migration `0011`;
        - the commands `python -m app.presser facts|preview|send|status` (a manual `send`
          for a missed gameweek);
        - the evaluation commands `python -m app.presser.evaluation
          build-cases|evaluate|review|judge-review|summary`, with a pointer to BACKLOG #32.
      - `README.md` `## Development`: add the same commands and variables.
      - `docs/DECISIONS.md`: add rows dated 2026-10-09. They record the presser design
        (module `app/presser`, the fact sheet as a deterministic SQL/Python model with
        names only, the LLM writer only writing), the hook after a successful league sync
        for the latest finished gameweek with the key format, the presser table with a
        row per generation, the display-name rule with the collision suffix, and the
        evaluation (judge `openai/gpt-6.1-sol` claim by claim, owner style 1–5, the pass
        rule, the set v1 with hand-written history for the real cases).
      - `docs/ROADMAP.md`: tick Stage 3 item 1, noting that the model comparison follows in
        BACKLOG #32.
      - Test first:
        - `backend/tests/test_env_example.py` gets `_PRESSER_FIELD_TO_VARIABLE`, with
          `test_every_presser_setting_field_has_its_variable_covered` (over
          `PresserSettings.model_fields` minus the `LlmSettings` fields) and
          `test_every_presser_variable_is_an_empty_placeholder`.
        - `backend/tests/test_readme.py::test_presser_documented` checks the README
          Development section and `docs/DEPLOYMENT.md`. They must contain the three
          variables, `app.presser facts`, `app.presser preview`, `app.presser send`,
          `app.presser status` and `app.presser.evaluation`. DEPLOYMENT must also contain
          `0011` and `Presser:`.
      Automatic verification: `cd backend && uv run pytest -q tests/test_env_example.py tests/test_readme.py tests/test_docs.py`

## Risks and traps

- **Logs and privacy.**
  - League IDs, names, nicknames and the presser text never go into logs. The key contains
    the league ID, so it is never logged either. Log the league ordinal (`league=#1`).
  - The DB, Langfuse and the LLM provider may hold names (SPEC → privacy).
  - The step 12 caplog test is the guard.
- **Real data in the repository.** Only pseudonymised cases are committed. The builder
  replaces names before writing and checks the output against the real names. The
  implementer never prints real names: no `facts`, `preview` or `status` against the
  development database, and no SQL `SELECT` of names. If a check finds a real name, stop and
  fix the builder. Never commit the file.
- **The first-word fallback** can produce the same name for two managers. The collision rule
  (design choice 5) keeps the facts unambiguous.
- **Captain crediting.** The stored pick `multiplier` is not trusted after the gameweek (the
  vice rule, TC). The rules compute it from minutes and `active_chip`. Players with a blank
  gameweek have no result row: count 0 minutes and 0 points, never `None` arithmetic.
- **Stop and retries.** `with_retries` returns `stopped` on shutdown. That must not record
  `failed`, or the worker would never send that presser after a restart (`PresserStopped`).
  `FixedClock` in tests keeps the 2 s and 4 s back-off from sleeping.
- **The worker loop is blocked during generation.** It takes about 10–30 s per league.
  League syncs run days before the next deadline, so snapshots are not at risk. The hook
  runs after the job transaction is committed, outside `acquire_job_lock`.
- **Length.** The 1500-character limit is an instruction and a measured metric. Production
  sends an over-long presser unchanged, because truncation would break the text. The share
  within the limit is in the evaluation.
- **Migration tests.** `PRE_0010_TABLES` drives several older migration tests, so it must
  exclude `presser`, or those tests read a table that does not exist yet.
- **`model_settings.toml` rows** must come from the live public model list. A wrong
  `reasoning_effort` fails at request time, not in tests.
- **Time zones.** `status` shows Warsaw time through `app/core/local_time.format_local`. The
  stored times are UTC.
- **Temperature.** `build_chat_model` sends `temperature = 0` to every model that accepts
  it, so the writer is near-deterministic. This plan keeps the shared factory unchanged. If
  the pressers read repetitive, a writer temperature is a prompt-tuning decision for
  BACKLOG #32, not for this spec.
- **The structured output of a long Polish text.** Some models escape newlines oddly in tool
  arguments. The evaluation shows it. Production keeps the text as returned.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

1. `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`.
   The expected result is all green.
2. The migration on the local development database, from `backend/`, with
   `DB=postgresql://presser:presser@localhost:5432/presser`:
   ```
   env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run alembic upgrade head
   env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run alembic downgrade 0010
   env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run alembic upgrade head
   ```
   Each command exits with 0, and `alembic current` shows `0011 (head)`.
3. `uv run python -m app.presser --help` lists `facts`, `preview`, `send` and `status`.
   `uv run python -m app.presser.evaluation --help` lists `build-cases`, `evaluate`,
   `review`, `judge-review` and `summary`.
4. `env -u OPENROUTER_API_KEY DATABASE_URL=$DB PRESSER_NICKNAMES='not json' uv run python -m app.presser status`
   exits with 1. stderr contains `PRESSER_NICKNAMES` and not `not json`.
5. `env -u OPENROUTER_API_KEY uv run python -m app.presser.evaluation summary --results-dir
   "$(mktemp -d)"` prints `no test runs`.
6. `uv run pytest -q tests/presser/evaluation/test_eval_set.py` passes on the committed set,
   and `git grep -n -E '[0-9]{6,}' backend/evals/presser` returns nothing.
7. Record the results in the PLAN under the Definition of Done.

### Manual (performed by the owner)

- Read a real presser generated from the local data (costs about $0.001). Run
  `uv run python -m app.presser facts --league <your league> --gameweek 5`, then
  `uv run python -m app.presser preview --league <your league> --gameweek 5` in `backend/`
  with your `.env`.
  Pass when: the preview is Polish and at most 1500 characters, names the same manager(s) of
  the gameweek as `facts` → `winners`, and contains no fact absent from `facts`. Then
  `status` shows that league's GW5 row as `generated`.
- Receive the e-mail and use the button. Run
  `uv run python -m app.presser send --league <your league> --gameweek 5` with delivery
  configured.
  Pass when: the inbox has `Presser GW5 — <league name>`, the plain-text part is the
  presser, and the "📲 Wyślij na WhatsApp" button opens WhatsApp with the whole presser
  prefilled. A second `send` prints `already_sent`.
- Check the nicknames. Set `PRESSER_NICKNAMES='{"<your entry id>": "<nickname>"}'` in
  `backend/.env` and rerun `facts` for that league.
  Pass when: your manager appears under the nickname in every section of the JSON, and the
  others appear under their first names.

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
      fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, `docs/DEPLOYMENT.md`, `README.md`,
      `backend/.env.example` updated
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

## Review log

### 2026-10-09 — plan review

Findings:

- `major` — step 16: the leak check searched every word (3+ letters) of the real manager, team and league names in the whole case JSON, but the sheet legitimately holds real player names and team names often contain player names ("Salah's Army"), so the builder would raise `PseudonymisationError` on clean data and push the implementer to an escalation or a weakened check. Changed: full real strings are searched everywhere; name-bearing fields must be pool entries; real-name words are searched only in the `previous` texts; added `test_team_name_with_player_word_is_not_a_leak`.
- `minor` — step 12: `StructuredCaller.from_spec` gives the writer its own stop event and the plan passed no stop-aware clock, so on SIGTERM the writer's 2 s/4 s back-off ran on and `PresserStopped` was hard to reach. Changed: the worker passes `StopAwareClock(stop_event)` and sets `caller.stop_event`.
- `minor` — step 12: the log-privacy test listed `Synthetic Manager`/`XI`/`League` but the collision rule turns the second synthetic manager into a display name starting with `Synthetic`, and entry IDs were not checked. Changed: assert no `Synthetic` substring and no `ENTRY_IDS`.
- `minor` — Approach → Fact rules: "each list is already ranked" but the order of winners, flops, captain picks, chips, auto-subs, season rows, records, climbers and fallers was unspecified, which makes the sheet (and the frozen evaluation cases) non-deterministic. Changed: added the exact orders.
- `minor` — step 7: `assert not check_fact_sheet(sheet)` in production code is stripped by `python -O`. Changed: raise `FactSheetError` (re-exported in step 5).
- `minor` — step 8: the corroboration tracer pattern ends the generation right after creating it, so the trace held no latency, while the SPEC asks for tokens, cost and latency per writer call. Changed: the generation carries `metadata.latency_seconds`, the span wraps the call, and `test_trace_created` asserts it.
- `minor` — step 21: DEPLOYMENT did not say that the first-deploy catch-up sends the latest finished gameweek's presser when the presser is enabled. Changed: added to the step 13 text. Also added a Risks note that the shared factory runs the writer at temperature 0 (a BACKLOG #32 matter).

Checked and found correct:

- Coverage: every AC1–AC20 has steps and a named proving test; the matrix matches the steps; every AC-delivering step writes its test first and its `Automatic verification:` runs exact paths.
- Owner summary: no new dependency (LangGraph, Langfuse, typer already used); migration 0011 is accepted in SPEC → Owner decisions; the six catalogue rows are accepted there too.
- Migrations: `0010_off_list_posts.py` is the head, `PRE_0010_TABLES` drives the older migration tests and must exclude `presser` as step 2 says.
- Worker: `run_job` returns a `RunRecord` with `outcome`; `PlannedAction` for `league_sync` carries `season` and `gameweek`; `_priority_key` orders the catch-up by gameweek with results before league sync, so the hook drops GW1–4 and sends GW5 in `tests/worker/sim.py` (the existing catch-up test lists league syncs 1–5 at 2026-09-27); the hook runs after the job transaction, outside the job lock.
- Fact data: `player_gameweek_result` is one row per player and gameweek with `minutes` and `total_points` (double gameweeks summed), `manager_transfer` has `gameweek_fpl_id`, `manager_gameweek.total_points` is net of hits; the captain/vice rule (0 minutes → vice, both 0 → captain on 0) and the TC multiplier match FPL; the stored pick multiplier is rightly ignored.
- Delivery: `DeliveryService.send(key, kind, message)` returns `sent | already_sent | failed | disabled` with `log_id`; its log line carries no key or name; the kind `presser` exists.
- Settings: `LlmSettings` already has `env_ignore_empty=True`, so empty `.env.example` placeholders fall back to the defaults; `ConfigError` is a `CollectorError`, so `_deps_from_settings`'s `fail(...)` path catches an invalid `PRESSER_NICKNAMES`.
- Content: the glossary has exactly 70 `[[term]]` entries and the style examples 3 `## Example` headings, as the step 8 test asserts; `load_prompt` requires the `version: N` header.
- Conventions and decisions: module `app/presser` (2026-09-26/27), shared `app/llm` and retry (2026-09-30), fake LLM in tests and evaluations outside pytest, JSONL dev/test sets, the delivery key and log (2026-10-01), the WhatsApp `wa.me` button (2026-10-08), logs without names or league IDs (league ordinal only).
- E2E: automatic part is executable by the agent (verify command, migration up/down/up on the development DB, `--help`, invalid nicknames exit, empty `summary`, eval-set test and 6-digit grep); no UI scope; each manual item has a `Pass when:` line and needs the owner's real inbox, real names or his phone.
- Language: the plan is in English (`language: en`).

The plan is ready for implementation: the one major finding was fixable in the plan and is fixed, no blocker remains, and the only migration and the catalogue additions are accepted in SPEC → Owner decisions.

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_
