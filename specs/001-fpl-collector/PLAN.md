# PLAN 001 — FPL data collector (database foundation and on-demand jobs)

## Owner summary

- **Approach:** A `backend/app` package gets settings (`core`), a database layer (`db`) and an
  `fpl` module: a throttled, retrying HTTP client with strict pydantic payload models, SQLModel
  tables keyed by `(season, FPL ID)` with composite natural primary keys, one Alembic migration,
  and four jobs plus a backfill run from `python -m app.fpl <command>`. Every job takes a
  database session and never commits; the CLI wraps each command in one transaction, which
  gives "writes nothing on failure" for free. Tests run on a throwaway `pgvector/pgvector:pg16`
  container (testcontainers) and on recorded public FPL payloads (bootstrap, fixtures, live
  GW1–3); all league and manager payloads are synthetic.
- **Main risks:** FPL cannot return past standings, so standings rows are stored under the
  latest gameweek whose deadline has passed at sync time (per-gameweek points history comes
  from each manager's picks endpoint, which is exact for every past gameweek); test runs need
  Docker; real payloads recorded once from the live API.
- **New dependency:** yes — `sqlmodel`, `alembic`, `psycopg[binary]`, `httpx`,
  `pydantic-settings`; dev: `testcontainers[postgres]`. All accepted in SPEC → "Owner
  decisions". `pydantic` is used through them (transitive of `sqlmodel` and
  `pydantic-settings`), not added as its own pin.
- **Data migration:** yes — the first Alembic revision creates the collector schema (new
  tables only, no existing data). Applied only to the local Compose and test databases, as
  accepted in SPEC → "Owner decisions"; production belongs to spec 002.
- **Manual scenarios for the owner:** 1 — a backfill against the live FPL API with the owner's
  real league IDs in a local `.env` (the agent has no real league IDs, by design).

## Approach

Read for this plan:

- `specs/001-fpl-collector/SPEC.md` — read in full.
- `docs/CONVENTIONS.md` — read in full.
- `docs/DECISIONS.md` — read in full (short; every row touches the stack or the collector).
- `docs/ROADMAP.md` — searched for "Stage 0", "FPL", "collector": the single Stage 0 item
  pointing at spec 001; the header rule "tick after green verification and a merged PR".
- `docs/PROJECT.md` — searched for "FR-0", "etiquette", "Privacy", "UTC", "Compose",
  "contract": FR-0.1–0.4, the API etiquette, privacy and UTC NFRs, Compose on
  `pgvector/pgvector:pg16`, contract tests on recorded payloads. Nothing beyond the SPEC's
  own "Read context".
- `docs/BACKLOG.md`, `docs/adr/` — not opened: the SPEC's read context found nothing relevant
  in the backlog, and ADR 0002 (facts through SQL) is already reflected in DECISIONS.
- Code: `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/tests/test_smoke.py`,
  `.github/workflows/ci.yml`, `README.md`, `.gitignore`, `.claude/workflow.json` — read in
  full. There is no existing code pattern to reuse; this spec sets the patterns.
- Live FPL API (2026-09-26, into the scratchpad only, nothing committed): shapes of
  `bootstrap-static` (38 events, 20 teams, 667 elements), `fixtures` (380),
  `event/1/live` (`elements[].stats.{starts,minutes,total_points}`, `elements[].explain[]`
  per fixture), `leagues-classic/{id}/standings/?page_standings=N` (`league.{id,name}`,
  `standings.{has_next,page,results[]}` with `entry, entry_name, player_name, rank,
  event_total, total`, 50 rows per page), `entry/{id}/event/{gw}/picks/` (`active_chip`,
  `automatic_subs[]`, `entry_history{points, total_points, event_transfers, event_transfers_cost,
  points_on_bench, bank, value, overall_rank}`, `picks[]{element, position, multiplier,
  is_captain, is_vice_captain}`; 404 when the entry has no team for the gameweek),
  `entry/{id}/history/` (`chips[]{name, time, event}`), `entry/{id}/transfers/`
  (`[]{element_in, element_in_cost, element_out, element_out_cost, event, time}`).
  PyPI latest versions for the pins below.

### Layout

```
compose.yaml                         db service, pgvector/pgvector:pg16
backend/.env.example                 DATABASE_URL, FPL_LEAGUE_IDS (empty)
backend/alembic.ini
backend/migrations/{env.py, script.py.mako, versions/0001_collector_schema.py}
backend/app/core/settings.py         Settings, parse_league_ids, ConfigError
backend/app/db/engine.py             make_engine(url)
backend/app/db/upsert.py             upsert(session, model, rows, conflict_cols)
backend/app/fpl/models.py            SQLModel tables
backend/app/fpl/schemas.py           pydantic payload models (used fields only)
backend/app/fpl/client.py            FplClient + errors
backend/app/fpl/reference.py         reference sync + flag change log
backend/app/fpl/snapshot.py          deadline snapshot
backend/app/fpl/leagues.py           league sync
backend/app/fpl/results.py           results sync
backend/app/fpl/backfill.py          backfill
backend/app/fpl/cli.py, __main__.py  argparse CLI
backend/tests/conftest.py            postgres container, engine, table truncation
backend/tests/fpl/fakes.py           FakeFpl (httpx.MockTransport router), synthetic leagues
backend/tests/fpl/payloads/*.json.gz recorded public payloads
```

### Key design choices

- **Primary keys: composite natural keys** `(season, fpl_id)` (and `(season, entry_id)`,
  `(season, league_fpl_id, …)`), with composite foreign keys declared through
  `__table_args__ = (ForeignKeyConstraint(...),)`. Rejected: surrogate `id` + unique
  `(season, fpl_id)` — every write would need an FPL-ID → surrogate lookup, while natural keys
  make `INSERT … ON CONFLICT (…) DO UPDATE` the whole idempotency story. `season` is the label
  string (`"2026/27"`), primary key of the `season` table.
- **Transactions: jobs never commit.** Every job function takes a `Session` and a `now:
  datetime` (injected clock, so deadline checks are testable); `cli.run_command` opens
  `with Session(engine) as s, s.begin():` around the whole command. Any exception rolls back
  everything (AC5, AC6, AC22). Rejected: fetch everything first, then write — more code, and
  rollback already gives the guarantee.
- **Fresh gameweek state.** `league-sync`, `results-sync` and `backfill` run the reference sync
  first inside the same transaction, then read gameweek flags from the database. The deadline
  snapshot reuses its own `bootstrap-static` response for the reference part (no second
  request). Rejected: jobs that read possibly stale gameweek rows and ask the owner to run
  `reference-sync` first.
- **League sync takes a list of gameweeks.** Standings, `history` (chips) and `transfers` are
  season-wide, so they are fetched once per run; only `picks` is per gameweek. The backfill
  passes every past gameweek in one call (≈ members × (2 + gameweeks) requests instead of
  members × 3 × gameweeks). `league-sync --gameweek N` passes `[N]`.
- **Standings.** The FPL standings endpoint only returns the current table. Rows go to
  `league_standing` keyed `(season, league_fpl_id, gameweek, entry_id)`, where `gameweek` is
  the latest gameweek whose deadline is `<= now` at sync time. Live syncs from GW6 on build a
  per-gameweek standings history; the backfill yields standings for the latest gameweek only.
  Exact per-gameweek points, totals and overall rank for every past gameweek come from
  `picks.entry_history` into `manager_gameweek` (AC14); `total_points` is stored beside the
  AC14 fields because it is the only exact per-gameweek season total for past gameweeks,
  which the Stage 3 season race needs.
- **Endpoint names in errors and logs are templates** (`leagues-classic/{league_id}/standings`,
  `entry/{entry_id}/event/{gw}/picks`), never concrete paths — a concrete path carries a
  league ID (AC23, privacy NFR). The client also sets the `httpx` and `httpcore` loggers to
  `WARNING` at import, because `httpx` logs every request URL at INFO.
- **Strict parsing:** pydantic models declare only the fields we use. Nullable fields are
  declared `X | None` **without a default**, so a missing key fails while `null` passes.
  `model_config = ConfigDict(extra="ignore")`. Datetimes are `AwareDatetime`. A
  `ValidationError` becomes `PayloadError(endpoint, field)` where `field` is the first error's
  `loc` joined with `.` (e.g. `elements.0.status`).
- **Season label** = year of `events[0].deadline_time` → `f"{y}/{(y + 1) % 100:02d}"`.
- **Recorded payloads:** real public `bootstrap-static`, `fixtures`, `event/{1,2,3}/live`,
  gzipped, with `events[].highest_scoring_entry` set to `null` (it is a real manager's entry
  ID). Tests mutate copies (e.g. set GW4+ to unfinished) instead of keeping many files.
  League, standings, picks, history and transfers payloads are generated by
  `tests/fpl/fakes.py` with synthetic IDs (league IDs `987654301`, `987654302`; entry IDs from
  `880000001`) and names (`Synthetic Manager 001`, `Synthetic XI 001`).

### Schema (`app/fpl/models.py`)

All datetimes `DateTime(timezone=True)` (set via `sa_type`/`sa_column`); `season` is
`str` FK → `season.label` on every table.

| table | primary key | columns |
|---|---|---|
| `season` | `label` | — |
| `gameweek` | `season, fpl_id` | `name, deadline_at, finished, data_checked` |
| `team` | `season, fpl_id` | `name, short_name` |
| `player` | `season, fpl_id` | `web_name, first_name, second_name, team_fpl_id` (FK team), `position` (int 1–4 = `element_type`) |
| `fixture` | `season, fpl_id` | `gameweek_fpl_id` (nullable, postponed), `kickoff_at` (nullable), `team_h_fpl_id, team_a_fpl_id` (FK team), `team_h_score, team_a_score` (nullable), `finished` |
| `player_flag_change` | `id` (serial) | `season, player_fpl_id` (FK player), `status, news, news_added` (nullable), `chance_of_playing_this_round, chance_of_playing_next_round` (nullable), `observed_at`; index `(season, player_fpl_id, observed_at)` |
| `deadline_snapshot_player` | `season, gameweek_fpl_id, player_fpl_id` | `status, news, chance_of_playing_this_round, chance_of_playing_next_round, selected_by_percent` (Numeric(5,1)), `now_cost, captured_at` |
| `raw_payload` | `id` (serial) | `season, endpoint` (`bootstrap-static` / `fixtures` / `event/{gw}/live`), `gameweek_fpl_id, fetched_at, payload` (JSONB) |
| `league` | `season, fpl_id` | `name` |
| `manager` | `season, entry_id` | `team_name, manager_name` |
| `league_membership` | `season, league_fpl_id, entry_id` | — |
| `league_standing` | `season, league_fpl_id, gameweek_fpl_id, entry_id` | `rank, event_total, total` |
| `manager_gameweek` | `season, entry_id, gameweek_fpl_id` | `has_team`; nullable when no team: `active_chip, points, total_points, event_transfers, event_transfers_cost, points_on_bench, bank, value, overall_rank` |
| `manager_pick` | `season, entry_id, gameweek_fpl_id, position` | `player_fpl_id, multiplier, is_captain, is_vice_captain` |
| `manager_auto_sub` | `season, entry_id, gameweek_fpl_id, player_out_fpl_id` | `player_in_fpl_id` |
| `manager_transfer` | `season, entry_id, made_at, player_in_fpl_id` | `gameweek_fpl_id, player_out_fpl_id, player_in_cost, player_out_cost` |
| `manager_chip` | `season, entry_id, name, gameweek_fpl_id` | `played_at` |
| `player_gameweek_result` | `season, player_fpl_id, gameweek_fpl_id` | `starts, minutes, total_points, explain` (JSONB) |

Foreign keys: player refs (`player_fpl_id`, picks, subs, transfers, results) → `player`;
gameweek refs → `gameweek`; manager tables → `manager`; league tables → `league`. The
reference sync always runs before the jobs that write them, in the same transaction.

Bookkeeping columns that AC21 exempts from the "content unchanged" check:
`player_flag_change.observed_at`, `raw_payload.fetched_at`, `deadline_snapshot_player.captured_at`.

### Job signatures

```python
def sync_reference(session: Session, client: FplClient, now: datetime) -> str  # returns season
def apply_bootstrap(session: Session, payload: Bootstrap, now: datetime) -> str
def take_deadline_snapshot(session: Session, client: FplClient, gameweek: int, now: datetime) -> None
def sync_leagues(session: Session, client: FplClient, league_ids: list[int], gameweeks: list[int], now: datetime) -> None
def sync_results(session: Session, client: FplClient, gameweek: int, now: datetime) -> None
def backfill(session: Session, client: FplClient, league_ids: list[int], now: datetime) -> None
# cli.py
def run_command(argv: list[str], *, engine: Engine, client: FplClient, league_ids_raw: str, now: datetime) -> int
def main(argv: list[str] | None = None) -> int   # builds Settings, engine, client; calls run_command
```

A precondition failure raises `JobError(reason)`; `run_command` prints `error: <reason>` to
stderr and returns `1` for every `CollectorError` — the common base (in
`app/core/errors.py`, created in step 2) of `JobError`, `ConfigError`, `FplUnavailableError`,
`FplNotFoundError` and `PayloadError`. An uncaught `FplNotFoundError` (a wrong league ID →
404 on the standings) would otherwise end in a traceback instead of the one-line error.

## AC → steps matrix

| AC | Steps | Proving test | Red before the change |
|----|-------|--------------|-----------------------|
| AC1 | 2, 5 | `tests/db/test_migrations.py::test_upgrade_downgrade_upgrade`; compose part in end-to-end (automatic) | |
| AC2 | 3 | `tests/db/test_engine.py::test_container_database_is_postgres_16`, `tests/core/test_settings.py::test_database_url_not_read_by_tests` (conftest never reads `DATABASE_URL`) | |
| AC3 | 2, 17, 19 | `tests/core/test_settings.py::test_parse_league_ids_*`, `tests/fpl/test_cli.py::test_league_sync_rejects_bad_league_ids`, `tests/fpl/test_cli.py::test_backfill_rejects_bad_league_ids` | |
| AC4 | 6 | `tests/fpl/test_client.py::test_user_agent`, `::test_requests_are_throttled` | |
| AC5 | 7, 14 | `tests/fpl/test_client.py::test_retries_*`, `::test_gives_up_after_five_attempts`; `tests/fpl/test_cli.py::test_unavailable_api_writes_nothing` | |
| AC6 | 9, 14 | `tests/fpl/test_schemas.py::test_missing_field_names_endpoint_and_field`, `::test_unknown_fields_ignored`; `tests/fpl/test_cli.py::test_payload_error_writes_nothing` | |
| AC7 | 11 | `tests/fpl/test_reference_sync.py::test_counts_match_payload` | |
| AC8 | 11 | `tests/fpl/test_reference_sync.py::test_season_label`, `::test_new_season_keeps_previous_rows` | |
| AC9 | 12 | `tests/fpl/test_reference_sync.py::test_flag_baseline`, `::test_flag_change_rows`, `::test_unchanged_payload_writes_no_flag_rows` | |
| AC10 | 11 | `tests/fpl/test_reference_sync.py::test_player_added_moved_and_removed` | |
| AC11 | 13 | `tests/fpl/test_deadline_snapshot.py::test_snapshot_stores_every_player_and_archives` | |
| AC12 | 13 | `tests/fpl/test_deadline_snapshot.py::test_rerun_replaces_snapshot`, `::test_at_or_after_deadline_fails_and_writes_nothing` | |
| AC13 | 15 | `tests/fpl/test_league_sync.py::test_two_page_league_stores_every_member` | |
| AC14 | 16 | `tests/fpl/test_league_sync.py::test_manager_gameweek_data`, `::test_transfers_and_chips` | |
| AC15 | 15 | `tests/fpl/test_league_sync.py::test_manager_in_two_leagues_stored_once` | |
| AC16 | 16 | `tests/fpl/test_league_sync.py::test_manager_without_team_for_gameweek` | |
| AC17 | 17 | `tests/fpl/test_league_sync.py::test_before_deadline_fails`; `tests/fpl/test_cli.py::test_league_sync_before_deadline_writes_nothing` | |
| AC18 | 18 | `tests/fpl/test_results_sync.py::test_results_stored_and_archived`, `::test_double_gameweek` | |
| AC19 | 18 | `tests/fpl/test_results_sync.py::test_unchecked_gameweek_fails` (parametrized: not finished / finished but not checked) | |
| AC20 | 19 | `tests/fpl/test_backfill.py::test_backfill_gw1_to_3` | |
| AC21 | 11, 12, 13, 16, 18, 19 | `::test_rerun_is_idempotent` in `test_reference_sync.py`, `test_deadline_snapshot.py`, `test_league_sync.py`, `test_results_sync.py`, `test_backfill.py` (helper `table_contents()` in `tests/fpl/fakes.py`) | |
| AC22 | 14, 17 | `tests/fpl/test_cli.py::test_unavailable_api_writes_nothing`, `::test_failure_on_last_manager_rolls_back` | |
| AC23 | 17 | `tests/fpl/test_league_sync.py::test_logs_carry_no_private_data` | |
| AC24 | 4, 11 | `tests/fpl/test_models.py::test_all_datetime_columns_are_timezone_aware`; `tests/fpl/test_reference_sync.py::test_stored_datetimes_are_utc` | |
| AC25 | 20 | `tests/test_readme.py::test_development_section_lists_commands` | |

## Steps

Common rules for every step: write the step's tests first and run them red; then the
change; then the step's verification and `cd backend && uv run ruff check . && uv run ruff
format --check .`. Tests that need the database take the `db_session`/`db_engine` fixtures
from step 3 (`db`, `db_session`). FPL clients in tests are built with `sleep=lambda _: None` unless the test is
about timing.

### Group 1 — Foundation: dependencies, settings, database, schema

- [x] 1. Dependencies — files: `backend/pyproject.toml`, `backend/uv.lock`.
      Runtime: `sqlmodel==0.0.47`, `alembic==1.20.0`, `psycopg[binary]==3.3.6`,
      `httpx==0.28.1`, `pydantic-settings==2.15.0`; dev: `testcontainers[postgres]==4.15.0`
      (the `postgres` extra exists in 4.15.0 and pulls no extra package — checked on PyPI at
      review).
      Run `cd backend && uv lock && uv sync --all-extras`.
      Automatic verification: `cd backend && uv run python -c "import sqlmodel, alembic, psycopg, httpx, pydantic_settings; from testcontainers.postgres import PostgresContainer" && uv run pytest -q`
- [x] 2. Settings, league IDs, local database — files: `backend/app/core/__init__.py`,
      `backend/app/core/errors.py` (`class CollectorError(Exception)`; `ConfigError`
      subclasses it), `backend/app/core/settings.py`, `backend/.env.example`, `compose.yaml`,
      `backend/tests/core/__init__.py`, `backend/tests/core/test_settings.py`.
      `Settings(BaseSettings)`: `database_url: str`, `fpl_league_ids: str = ""`,
      `model_config = SettingsConfigDict(env_file=".env", extra="ignore")`.
      `parse_league_ids(raw: str) -> list[int]`: split on `,`, strip, every part a positive
      int, at least one; else `ConfigError("FPL_LEAGUE_IDS must be a comma-separated list of
      league IDs")` — the message names the variable and never echoes the value.
      Tests: valid (`"1, 2"` → `[1, 2]`), empty, whitespace, `"1,,2"`, `"1,abc"`, `"-3"`;
      `Settings(_env_file=None)` with `monkeypatch.setenv` reads both variables.
      `compose.yaml` (repo root): service `db`, image `pgvector/pgvector:pg16`, env
      `POSTGRES_USER/PASSWORD/DB=presser`, port `${POSTGRES_PORT:-5432}:5432`, named volume, healthcheck
      `pg_isready`. `.env.example`: `DATABASE_URL=postgresql+psycopg://presser:presser@localhost:5432/presser`
      and `FPL_LEAGUE_IDS=` (empty, with a comment: comma-separated classic league IDs;
      never commit real ones).
      Automatic verification: `cd backend && uv run pytest -q tests/core/test_settings.py && docker compose -f ../compose.yaml config -q`
- [ ] 3. Test database fixture and engine — files: `backend/app/db/__init__.py`,
      `backend/app/db/engine.py`, `backend/tests/__init__.py`, `backend/tests/conftest.py`,
      `backend/tests/db/__init__.py`, `backend/tests/db/test_engine.py`.
      `make_engine(url: str) -> Engine` (SQLModel `create_engine`, `pool_pre_ping=True`,
      `connect_args={"options": "-c timezone=UTC"}` so `timestamptz` values read back in UTC
      whatever the server's `TimeZone`). `backend/tests/__init__.py` makes `tests` a package, so
      pytest imports `tests.fpl.fakes` once under one name (without it the rootdir-based
      import names would be `fpl.*`, `db.*`, `core.*`).
      `conftest.py`: session-scoped `postgres_url` fixture starting
      `PostgresContainer("pgvector/pgvector:pg16", driver="psycopg")` and returning
      `get_connection_url()`; session-scoped `db_engine = make_engine(postgres_url)` (step 5
      adds `alembic upgrade head` to it); function-scoped `db` fixture returning `db_engine`
      and, after the test, truncating every table that exists in the database
      (`SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <>
      'alembic_version'`) with `TRUNCATE … RESTART IDENTITY CASCADE` (no-op while none exist).
      Do not derive the list from `SQLModel.metadata`: after step 4 the models are imported at
      collection while the tables only appear with the migration in step 5;
      function-scoped `db_session` built on `db`, yielding `Session(engine)`. Database tests
      use `db` or `db_session`, never `db_engine` directly. The container starts lazily, so
      tests without database fixtures never need Docker. Nothing in `tests/` reads
      `DATABASE_URL`.
      Tests: `SELECT version()` starts with `PostgreSQL 16`; `SHOW timezone` on an engine
      connection returns `UTC`; a grep-style test asserting no
      file under `tests/` contains `DATABASE_URL` except `tests/core/test_settings.py`
      (`test_database_url_not_read_by_tests`, in `tests/core/test_settings.py`).
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_engine.py tests/core/test_settings.py`
- [ ] 4. Collector models — files: `backend/app/fpl/__init__.py`, `backend/app/fpl/models.py`,
      `backend/tests/fpl/__init__.py`, `backend/tests/fpl/test_models.py`.
      Every table from the Approach schema table, composite PKs and FKs as specified.
      Tests (no database): every `DateTime` column in `SQLModel.metadata` has
      `timezone=True`; every table except `season` has a `season` column in its primary key
      or (`player_flag_change`, `raw_payload`) an FK to `season.label`; the set of table
      names equals the 18 names of the schema table.
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_models.py`
- [ ] 5. Alembic and the first migration (data migration step, local and test databases
      only) — files: `backend/alembic.ini` (`script_location = %(here)s/migrations`, so it
      resolves from any working directory),
      `backend/migrations/env.py`, `backend/migrations/script.py.mako` (with
      `import sqlmodel`), `backend/migrations/versions/0001_collector_schema.py`,
      `backend/tests/db/test_migrations.py`.
      `env.py`: `fileConfig(config.config_file_name, disable_existing_loggers=False)` — the
      default `True` would disable every logger created before the in-process
      `alembic upgrade` in `conftest.py` (`app.*`, `httpx`) and make the log-privacy test of
      step 17 pass vacuously; `target_metadata = SQLModel.metadata` after
      `import app.fpl.models`; URL from
      `config.get_main_option("sqlalchemy.url")` when set, otherwise `Settings().database_url`;
      online mode only. Generate the revision with autogenerate against a throwaway container
      (`docker run --rm -d -p 55432:5432 -e POSTGRES_PASSWORD=x pgvector/pgvector:pg16`, then
      `uv run alembic -x … revision --autogenerate` with the URL set via
      `sqlalchemy.url`, or temporarily via `DATABASE_URL` in the shell), review it by hand
      (`revision = "0001"`, a complete `downgrade`), stop the container. In `conftest.py`,
      `db_engine` now runs `run_alembic(postgres_url, "upgrade", "head")` (helper building
      `alembic.config.Config(str(BACKEND_DIR / "alembic.ini"))`, `BACKEND_DIR =
      Path(__file__).resolve().parents[1]`, with `set_main_option("sqlalchemy.url", url)`)
      before returning the engine.
      Tests on a **separate** container fixture (module-scoped in this file, so it does not
      disturb `db_engine`): `test_upgrade_downgrade_upgrade` (after `downgrade base` only
      `alembic_version` remains, after `upgrade head` all 18 tables exist);
      `test_models_match_migration` (`alembic.autogenerate.compare_metadata` on a migrated
      database returns `[]`).
      Automatic verification: `cd backend && uv run pytest -q tests/db && uv run pytest -q`

### Group 2 — FPL client and payload contracts

- [ ] 6. Client basics and the fake FPL — files: `backend/app/fpl/client.py`,
      `backend/app/fpl/errors.py` (`FplUnavailableError`, `FplNotFoundError`,
      `PayloadError`, all subclassing `CollectorError`; `JobError` is added in step 13),
      `backend/tests/fpl/fakes.py`, `backend/tests/fpl/test_client.py`.
      `FplClient(transport: httpx.BaseTransport | None = None, base_url="https://fantasy.premierleague.com/api/",
      min_interval=0.5, max_attempts=5, backoff_base=1.0, timeout=20.0, sleep=time.sleep,
      monotonic=time.monotonic)`; header `User-Agent: gaffers-presser/0.1 (+https://github.com/wojciechczarnecki/gaffers-presser)`;
      private `_get_json(endpoint_template: str, path: str, params=None)` throttling so that
      consecutive request starts are ≥ `min_interval` apart (sleep for the remainder measured
      on `monotonic`). Module sets `logging.getLogger("httpx")` and `("httpcore")` to
      `WARNING`. `fakes.FakeFpl`: `routes: dict[str, object]` keyed by path (e.g.
      `"bootstrap-static/"`, `"leagues-classic/987654301/standings/?page_standings=2"`), a value
      is a JSON payload, an `httpx.Response`, or a callable `(request) -> httpx.Response`;
      unknown path → 404; records `requests`; `.client(**kw) -> FplClient`.
      Tests: User-Agent present and descriptive; with a fake clock, three requests produce
      sleeps so starts are ≥ 0.5 s apart, and no sleep when the gap is already larger.
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_client.py`
- [ ] 7. Retry and back-off — files: `backend/app/fpl/client.py`,
      `backend/tests/fpl/test_client.py`.
      Retry on 429, 5xx, `httpx.TimeoutException`, and a body whose JSON (or text) is
      `"The game is being updated."` whatever the status; delays `backoff_base * 2**(n-1)`
      (1, 2, 4, 8 s), at most `max_attempts = 5` attempts in total; after the last one raise
      `FplUnavailableError(endpoint_template)`. 404 → `FplNotFoundError(endpoint_template)`,
      not retried; other 4xx → `FplUnavailableError` without retry.
      Tests: 429 then 200 succeeds with one 1 s sleep; 503 ×5 raises after exactly 5 requests
      and sleeps `[1, 2, 4, 8]`; timeout retried; "game is being updated" (200 and 503)
      retried; 404 raises `FplNotFoundError` after one request.
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_client.py`
- [ ] 8. Recorded payloads — files: `backend/tests/fpl/payloads/bootstrap-static.json.gz`,
      `fixtures.json.gz`, `event-1-live.json.gz`, `event-2-live.json.gz`,
      `event-3-live.json.gz`, `backend/tests/fpl/payloads/__init__.py` (`load(name) -> Any`
      reading the gzip, returning a fresh deep copy each call).
      Record once from the live API with ≥ 1 s between requests and the client's User-Agent
      (`curl -s -A "<UA>" https://fantasy.premierleague.com/api/bootstrap-static/` etc.); in
      `bootstrap-static` set every `events[].highest_scoring_entry` to `null`; write with
      `gzip`. Commit no league, entry or standings payload.
      Automatic verification: `cd backend && uv run python -c "from tests.fpl.payloads import load; b = load('bootstrap-static'); assert len(b['events']) == 38 and len(b['teams']) == 20 and all(e['highest_scoring_entry'] is None for e in b['events']); assert len(load('fixtures')) == 380; assert load('event-1-live')['elements']"`
- [ ] 9. Payload schemas and strict parsing — files: `backend/app/fpl/schemas.py`,
      `backend/app/fpl/client.py`, `backend/tests/fpl/test_schemas.py`.
      Models (fields from the Approach, `extra="ignore"`): `Bootstrap(events, teams,
      elements)`, `Fixture`, `Live(elements[id, stats{starts, minutes, total_points},
      explain: list[dict]])`, `StandingsPage(league{id, name}, standings{has_next, page,
      results[]})`, `Picks(active_chip, automatic_subs, entry_history, picks)`,
      `History(chips[])`, `Transfer`. Client methods returning them: `bootstrap()`,
      `fixtures()`, `live(gw)`, `league_standings(league_id, page)`, `entry_picks(entry_id,
      gw)`, `entry_history(entry_id)`, `entry_transfers(entry_id)`. The three archived
      endpoints (`bootstrap()`, `fixtures()`, `live(gw)`) return `Fetched[T]` — a frozen
      dataclass `(data: T, raw: Any)` holding the parsed model and the JSON as received — so
      steps 13 and 18 can archive the raw payload without a second request; the others return
      the model. Each maps
      `ValidationError` → `PayloadError(endpoint_template, field)`, message
      `"<endpoint>: missing or invalid field <field>"`.
      Tests: every recorded payload parses; deleting `elements[0]["status"]` raises
      `PayloadError` with `endpoint == "bootstrap-static"` and `field == "elements.0.status"`;
      deleting `chance_of_playing_this_round` (a nullable field) also fails; adding
      `{"brand_new": 1}` to an element parses; a synthetic picks payload without
      `entry_history.bank` names `entry/{entry_id}/event/{gw}/picks` and
      `entry_history.bank`, and the message contains no entry ID.
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_schemas.py tests/fpl/test_client.py`

### Group 3 — Reference sync, deadline snapshot, CLI

- [ ] 10. Upsert helper — files: `backend/app/db/upsert.py`, `backend/tests/db/test_upsert.py`.
      `upsert(session, model, rows: list[dict], conflict_cols: list[str]) -> None` using
      `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(index_elements=…,
      set_={non-key columns})`, or `on_conflict_do_nothing(index_elements=…)` when the table
      has no non-key column (`season`, `league_membership` — an empty `set_` is invalid), no-op
      on empty `rows`, chunks of 1 000 rows. Tests on the `team` table: insert, update of a
      non-key column, second identical call leaves content unchanged; on the `season` table:
      the same row twice leaves one row.
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_upsert.py`
- [ ] 11. Reference sync: season, gameweeks, teams, players, fixtures — files:
      `backend/app/fpl/reference.py`, `backend/tests/fpl/test_reference_sync.py`, helper
      `table_contents(session, exclude={"observed_at", "fetched_at", "captured_at"})` in
      `backend/tests/fpl/fakes.py` (every table's rows as sorted tuples).
      `apply_bootstrap(session, payload, now) -> season` (season, gameweeks, teams, players —
      upsert; players missing from the payload are left untouched) and `apply_fixtures(session,
      season, fixtures)`; `sync_reference(session, client, now)` fetches both and applies.
      Tests: counts equal the payload (1 season, 38 gameweeks with deadline/finished/
      data_checked, 20 teams, 667 players with name/team/position, 380 fixtures, scores set
      only where the payload has them) — assert against `len(payload[...])`, not literals;
      season label `2026/27`; a payload with every deadline shifted one year back
      (`2025/26`) adds rows and leaves `2026/27` rows unchanged; mid-season player added,
      team change updated, removed player keeps his rows; datetimes read back are aware with
      `utcoffset() == timedelta(0)` (psycopg returns `ZoneInfo("UTC")`, which does not compare
      equal to `datetime.UTC`, so do not assert `tzinfo == UTC`); `test_rerun_is_idempotent`.
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_reference_sync.py`
- [ ] 12. Flag change log — files: `backend/app/fpl/reference.py`,
      `backend/tests/fpl/test_reference_sync.py`.
      In `apply_bootstrap`: load the latest `player_flag_change` per player of the season
      (`DISTINCT ON (player_fpl_id) … ORDER BY player_fpl_id, observed_at DESC, id DESC`);
      insert a row with `observed_at = now` for a player without one (baseline) or whose
      `status`, `news`, `chance_of_playing_this_round` or `chance_of_playing_next_round`
      differs (`news_added` stored but not compared).
      Tests: first sync writes one row per player; changing two players' `status`/`news`/
      chance values writes exactly two rows; an unchanged payload writes none; a change of
      only `news_added` writes none.
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_reference_sync.py`
- [ ] 13. Deadline snapshot — files: `backend/app/fpl/snapshot.py`, `backend/app/fpl/errors.py`
      (`JobError`), `backend/tests/fpl/test_deadline_snapshot.py`.
      `take_deadline_snapshot(session, client, gameweek, now)`: fetch bootstrap,
      `apply_bootstrap`, raise `JobError("gameweek N deadline has passed")` when
      `now >= deadline`, or `JobError("gameweek N does not exist")`; delete the season's rows
      for `N` in `deadline_snapshot_player`, insert one per player with `captured_at = now`;
      insert a `raw_payload` row (`endpoint="bootstrap-static"`, `gameweek_fpl_id=N`,
      `fetched_at=now`, raw JSON as received — keep the raw dict next to the parsed model).
      Tests: every player stored with the listed fields, one archive row; a second run with
      changed ownership replaces the values (row count unchanged, new values); `now` equal to
      and after the deadline raise `JobError` (the database check for "writes nothing" is in
      step 14); `test_rerun_is_idempotent` (contents equal except bookkeeping, `raw_payload`
      count +1 as AC21 allows).
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_deadline_snapshot.py`
- [ ] 14. CLI with one transaction per command — files: `backend/app/fpl/cli.py`,
      `backend/app/fpl/__main__.py`, `backend/tests/fpl/test_cli.py`.
      argparse subcommands `reference-sync`, `deadline-snapshot --gameweek N` (the others
      arrive in steps 17–19); `run_command` as in Approach, one `session.begin()` around the
      job, `logging.basicConfig(level=INFO)` in `main` only, logs of counts and gameweek
      numbers only (e.g. `"reference sync: season=%s players=%d fixtures=%d"`).
      Tests (via `run_command` with `db_engine` and `FakeFpl`): `reference-sync` returns 0
      and stores data; `fixtures/` answering 503 forever (after `bootstrap-static/` succeeded)
      → returns 1, stderr names `fixtures`, every table empty
      (`test_unavailable_api_writes_nothing`); bootstrap missing a used field → returns 1,
      stderr names endpoint and field, tables empty (`test_payload_error_writes_nothing`);
      `deadline-snapshot` at the deadline → returns 1, stderr names the reason, tables empty;
      `bootstrap-static/` answering 404 → returns 1 with a one-line `error:` on stderr, no
      traceback (`test_not_found_is_a_clean_error`).
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_cli.py && uv run python -m app.fpl --help`

### Group 4 — League sync

- [ ] 15. Standings, leagues, managers, memberships — files: `backend/app/fpl/leagues.py`,
      `backend/tests/fpl/fakes.py` (`synthetic_league(league_id, entry_ids, *, gameweeks,
      player_ids) -> dict[str, object]` producing standings pages of 50 and per-entry
      `picks`/`history`/`transfers` routes with synthetic names), `backend/tests/fpl/test_league_sync.py`.
      `sync_leagues(session, client, league_ids, gameweeks, now)`: requires every gameweek in
      the database (else `JobError`); for each league read pages `1..` while `has_next`;
      upsert `league`, `manager` (team name = `entry_name`, manager name = `player_name`),
      `league_membership`, `league_standing` under the latest gameweek with
      `deadline_at <= now`. Test setup: `apply_bootstrap` with the recorded payload first.
      Tests: 51-member league over two pages → 51 managers, memberships and standing rows
      with rank/event_total/total; an entry in both synthetic leagues → one `manager` row,
      two memberships.
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_league_sync.py`
- [ ] 16. Picks, gameweek history, transfers, chips — files: `backend/app/fpl/leagues.py`,
      `backend/tests/fpl/fakes.py`, `backend/tests/fpl/test_league_sync.py`.
      Per unique entry (once, even if in two leagues): `entry_transfers` → `manager_transfer`
      (upsert on PK), `entry_history` → `manager_chip`; per gameweek: `entry_picks` →
      `manager_gameweek` (`has_team=True`, `active_chip` + `entry_history` fields),
      replace that gameweek's `manager_pick` (15 rows) and `manager_auto_sub` rows;
      `FplNotFoundError` → `manager_gameweek(has_team=False)` with nulls, delete any picks/subs
      for it, continue.
      Tests: 15 picks with position/multiplier/captain/vice; active chip and auto subs stored;
      history fields equal the payload; all transfers and chips stored; a manager whose picks
      return 404 for GW2 gets `has_team=False` for GW2 while other managers are complete;
      `test_rerun_is_idempotent`.
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_league_sync.py`
- [ ] 17. League preconditions, CLI and privacy — files: `backend/app/fpl/leagues.py`,
      `backend/app/fpl/cli.py`, `backend/tests/fpl/test_league_sync.py`,
      `backend/tests/fpl/test_cli.py`.
      `sync_leagues` raises `JobError("gameweek N deadline has not passed")` when any
      requested gameweek has `deadline_at > now`. CLI `league-sync --gameweek N`:
      `parse_league_ids(league_ids_raw)` first (before any request), then `sync_reference`,
      then `sync_leagues(..., [N], now)`.
      Tests: before-deadline raises (`test_before_deadline_fails`) and via CLI returns 1 with
      tables empty; `FPL_LEAGUE_IDS` empty / `"1,abc"` → returns 1, stderr contains
      `FPL_LEAGUE_IDS`, no request made; the last manager's `transfers/` answering 503 forever
      → returns 1, every table empty (`test_failure_on_last_manager_rolls_back`);
      `caplog.set_level(logging.DEBUG)` during a successful CLI league sync: `caplog.text`
      contains the job's summary line (positive control — the capture is live) and none of the
      synthetic manager names, team names, league IDs, entry IDs
      (`test_logs_carry_no_private_data`). Run it once with the `httpx` silencing removed to
      see it red (httpx logs every request URL at INFO, `MockTransport` included).
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_league_sync.py tests/fpl/test_cli.py`

### Group 5 — Results, backfill, documentation

- [ ] 18. Results sync — files: `backend/app/fpl/results.py`, `backend/app/fpl/cli.py`,
      `backend/tests/fpl/test_results_sync.py`, `backend/tests/fpl/test_cli.py`.
      `sync_results(session, client, gameweek, now)`: the gameweek row must be `finished`
      and `data_checked`, else `JobError("gameweek N is not finished and data-checked")`;
      fetch `live(N)` and `fixtures()`; upsert `player_gameweek_result` (stats as given — FPL
      already sums a double gameweek — and `explain` as JSONB); `apply_fixtures` for scores;
      two `raw_payload` rows (`event/{gw}/live` with `gameweek_fpl_id=N`, `fixtures` with
      `gameweek_fpl_id=N`). CLI `results-sync --gameweek N`: `sync_reference` then
      `sync_results`.
      Tests (recorded GW1 live): every live element stored with starts/minutes/points equal
      to the payload, two archive rows, fixture scores present; a double gameweek (a copy of a
      live element given two `explain` entries and summed stats, with two GW1 fixtures for its
      team in a modified fixtures payload) stores the summed stats and both explain entries;
      a gameweek not finished, and finished but not checked, raise `JobError`; CLI
      `results-sync` for such a gameweek returns 1 with tables empty;
      `test_rerun_is_idempotent` (archive +2 allowed).
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_results_sync.py tests/fpl/test_cli.py`
- [ ] 19. Backfill — files: `backend/app/fpl/backfill.py`, `backend/app/fpl/cli.py`,
      `backend/tests/fpl/test_backfill.py`, `backend/tests/fpl/test_cli.py`.
      `backfill(session, client, league_ids, now)`: `sync_reference`; `sync_leagues` for all
      gameweeks with `deadline_at <= now` in one call; `sync_results` for each gameweek that
      is `finished` and `data_checked`; no deadline snapshot. CLI `backfill`: parse league
      IDs first.
      Test setup: recorded bootstrap with GW4–38 set to `finished=False, data_checked=False`,
      `now` between the GW3 and GW4 deadlines, live GW1–3, a synthetic league of 3 managers.
      Tests: `manager_gameweek`, `manager_pick` and `player_gameweek_result` hold rows for
      gameweeks 1, 2 and 3 only; `deadline_snapshot_player` empty; `raw_payload` has 6 rows;
      bad `FPL_LEAGUE_IDS` → returns 1 naming the variable; `test_rerun_is_idempotent`
      (archive +6 allowed).
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_backfill.py tests/fpl/test_cli.py`
- [ ] 20. Documentation — files: `README.md`, `docs/DECISIONS.md`, `docs/ROADMAP.md`,
      `backend/tests/test_readme.py`.
      README "Development": copy `backend/.env.example` to `backend/.env` and fill
      `FPL_LEAGUE_IDS`; `docker compose up -d`; `cd backend && uv run alembic upgrade head`;
      each job: `uv run python -m app.fpl reference-sync`, `deadline-snapshot --gameweek N`,
      `league-sync --gameweek N`, `results-sync --gameweek N`, `backfill`; the verify command
      (Docker needed for tests). Test: the section between `## Development` and the next
      `## ` contains `docker compose up -d`, `alembic upgrade head` and the five command
      names. DECISIONS (append rows): composite natural primary keys `(season, fpl_id)` over
      surrogate keys; collector jobs never commit, the CLI owns one transaction per command;
      standings stored under the latest passed gameweek at sync time (FPL has no historical
      standings). ROADMAP: the Stage 0 item also needs spec 002 (scheduled snapshots around
      each deadline), so leave its box unticked and change its trailer to
      `(specs: [001](../specs/001-fpl-collector/SPEC.md) — foundation, jobs and backfill;
      002 — worker, schedule, deployment)`.
      Automatic verification: `cd backend && uv run pytest -q tests/test_readme.py && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`

## Risks and traps

- **Docker is required for the test suite.** The container starts only for tests that request
  database fixtures; the first run pulls `pgvector/pgvector:pg16`. Ryuk (testcontainers'
  reaper) must be allowed; do not disable it.
- **Truncation between tests:** `TRUNCATE … CASCADE` of every table after each database test;
  tests must not rely on data from another test. The migration test uses its own container so
  `downgrade base` does not break the shared engine.
- **SQLModel composite foreign keys** must go in `__table_args__` as
  `ForeignKeyConstraint`; per-field `foreign_key=` only supports single columns. Autogenerate
  renders SQLModel string columns as `sqlmodel.sql.sqltypes.AutoString` — hence
  `import sqlmodel` in `script.py.mako`.
- **Nullable ≠ optional** in payload models: a default of `None` would hide a missing field
  and break AC6.
- **`selected_by_percent` is a string** in the payload (`"12.3"`) → `Decimal`.
- **Fixtures with `event: null`** (postponed) and `kickoff_time: null` exist mid-season.
- **FK to `player`:** picks, transfers or live elements may reference a player the current
  bootstrap no longer lists only if FPL deleted him; players are never deleted by us, and the
  reference sync runs first in the same transaction. A violation fails the job loudly rather
  than storing orphan rows.
- **Privacy in logs:** never log paths, URLs, league IDs, entry IDs or names; `httpx` INFO
  logging is silenced at client import. Exceptions carry endpoint templates only.
- **Time:** every `now` is injected (`datetime.now(UTC)` only in `cli.main`); comparing aware
  and naive datetimes raises — the schema test and `AwareDatetime` catch it early.
- **Idempotency of replaced sets:** picks and auto subs are replaced per
  `(entry, gameweek)`, the snapshot per gameweek; transfers and chips are upserted on natural
  keys. Serial `id` columns exist only on `player_flag_change` and `raw_payload`, which are
  append-only by design, so `table_contents` compares without `id` there.
- **Port 5432 on the development machine** may already be taken by a local PostgreSQL:
  publish the Compose port as `${POSTGRES_PORT:-5432}:5432` and say in `.env.example` that
  `DATABASE_URL` must use the same port. Tests are unaffected (testcontainers maps a random
  port).
- **API etiquette during the implementation:** recording payloads and the end-to-end run
  touch the live API a few dozen times at most, ≥ 1 s apart.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

From the repository root, against the local Compose database (never production):

1. `docker compose up -d && docker compose ps` — `db` is healthy;
   `docker compose exec db psql -U presser -c "select version()"` → PostgreSQL 16.
2. `cd backend && cp .env.example .env` (leave `FPL_LEAGUE_IDS` empty) and
   `uv run alembic upgrade head` → 18 tables (`\dt` count via psql); then
   `uv run alembic downgrade base` → only `alembic_version`; `uv run alembic upgrade head`
   again (AC1).
3. `uv run python -m app.fpl reference-sync` → exit 0; psql: 38 gameweeks, 20 teams, players
   and 380 fixtures for `2026/27`; one `player_flag_change` row per player. Running it again
   → the flag row count is unchanged (or grows only by genuine FPL flag changes).
4. `uv run python -m app.fpl deadline-snapshot --gameweek 6` (valid until
   2026-10-10 10:00 UTC; otherwise use the next gameweek) → exit 0, one snapshot row per
   player, one `raw_payload` row. `--gameweek 1` → exit 1 naming the passed deadline.
5. `uv run python -m app.fpl results-sync --gameweek 1` → exit 0; `player_gameweek_result`
   rows for GW1, two new `raw_payload` rows. `--gameweek 30` → exit 1 naming the reason.
6. `uv run python -m app.fpl league-sync --gameweek 1` with the empty `FPL_LEAGUE_IDS` →
   exit 1, message names `FPL_LEAGUE_IDS`; same for `backfill`.
7. `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` —
   green.
8. Clean up: `uv run alembic downgrade base`, `docker compose down`; delete `backend/.env`.
Record the outputs (counts, exit codes) in `## Deviations` only if they differ from the
above, and a one-line result under Definition of Done.

### Manual (performed by the owner)

1. With real league IDs in `backend/.env`: `docker compose up -d`,
   `uv run alembic upgrade head`, `uv run python -m app.fpl backfill` → exit 0; check in psql
   that every member of the leagues has `manager_gameweek` rows for GW1–5, picks look like
   the FPL site for one known manager, and the command output shows no names or league IDs.

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md` rows added (step 20)
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation: date, stage, question, decision)_

## Review log

### 2026-09-26 — /pipeline:plan-review

Anti-anchoring notes (from the SPEC alone, before the plan): natural keys + `ON CONFLICT`
upserts for idempotency; injected clock and sleep for deadline and throttle tests; one
transaction per command so every failure writes nothing; recorded public payloads plus
synthetic league data; a positive-control log capture for the privacy AC. The plan matches
on every point; the differences found are below.

Findings (severity counted before the fixes):

| id | severity | finding | change |
|---|---|---|---|
| R1 | `major` | Step 3 truncated every table of `SQLModel.metadata`; from step 4 the models are imported at collection while the tables exist only after step 5's migration, so the full suite is red between steps 4 and 5 (a forward dependency). | Step 3 truncates the tables listed in `pg_tables` (minus `alembic_version`). |
| R2 | `major` | AC24 test asserted `tzinfo == UTC`; psycopg returns `ZoneInfo("UTC")`, which never equals `datetime.UTC` (checked), and the read-back zone follows the server's `TimeZone`. | `make_engine` sets `-c timezone=UTC`; step 3 tests `SHOW timezone`; step 11 asserts `utcoffset() == timedelta(0)`. |
| R3 | `major` | AC23 privacy test could pass vacuously: Alembic's `fileConfig` defaults to `disable_existing_loggers=True`, and `conftest.py` runs `alembic upgrade` in-process, silencing `app.*`/`httpx` loggers before the test. | `env.py` uses `disable_existing_loggers=False`; the AC23 test asserts the job's summary line is captured (positive control) and is checked red once with the `httpx` silencing removed. |
| R4 | `minor` | `run_command` caught four error types but not `FplNotFoundError` (e.g. a wrong league ID → 404 on standings) — a traceback instead of the one-line error. | Common base `CollectorError` in `app/core/errors.py` (step 2); `run_command` catches it; step 14 adds `test_not_found_is_a_clean_error`. |
| R5 | `minor` | Upsert with `on_conflict_do_update` on key-only tables (`season`, `league_membership`) would get an empty `set_`. | Step 10 uses `on_conflict_do_nothing` there and tests the `season` table. |
| R6 | `minor` | Steps 13 and 18 archive raw payloads, but step 9's client methods returned only parsed models. | `bootstrap()`, `fixtures()`, `live(gw)` return `Fetched[T](data, raw)`. |
| R7 | `minor` | No `backend/tests/__init__.py`: pytest's rootdir-based import would name test packages `fpl`, `db`, `core` and load `tests.fpl.fakes` twice. | Added to step 3. |
| R8 | `minor` | `alembic.ini`/`Config("alembic.ini")` resolved relative to the working directory. | `script_location = %(here)s/migrations`; `run_alembic` uses a path relative to `conftest.py`. |
| R9 | `minor` | FPL has no past standings, so the backfill leaves no exact per-gameweek season total for past gameweeks; `entry_history.total_points` gives it for free. | `manager_gameweek.total_points` added (schema, payload fields, Approach). |
| R10 | `minor` | Compose publishes host port 5432, which a local PostgreSQL may hold, breaking the automatic end-to-end run. | Port `${POSTGRES_PORT:-5432}:5432`; risk recorded. |
| R11 | `minor` | Step 1 hedged on the `testcontainers[postgres]` extra not existing. | Checked on PyPI: every pin is the latest release and the extra exists in 4.15.0; hedge removed. |

Checked and found correct (later stages need not repeat):

- **Coverage:** every AC1–AC25 has steps and a named proving test; the matrix matches the
  steps; the fourth column exists (empty, filled by `/pipeline:implement`).
- **Standings interpretation (AC13):** FPL returns only the current table, so standings are
  stored under the latest passed gameweek and exact per-gameweek data come from
  `entry_history`. AC13 asks for the standings rows, not a table as of N, so this satisfies
  the SPEC; it is recorded in the owner summary's risks and as a DECISIONS row in step 20. Not
  a SPEC gap.
- **Compliance:** CONVENTIONS (exact pins, committed lock, recorded/synthetic payloads,
  container-only database tests, no names/IDs in logs, aware UTC, no docstrings) and
  DECISIONS (testcontainers for tests and Compose for development, `(season, FPL ID)` keys,
  flag change log + deadline snapshot + archive at key moments, public API with back-off,
  `app/` module layout, no cron) — the plan breaks none. No Polish product content in scope.
- **Minimality:** no reusable code exists; `httpx.MockTransport` and `argparse` avoid extra
  dependencies; the league sync fetches season-wide endpoints once per run.
- **Feasibility:** migration step accounted for and limited to local/test databases; injected
  `now` for deadlines; one transaction per command gives AC5/AC6/AC22; FK violations fail
  loudly; nullable-but-required payload fields handled for AC6.
- **E2E:** automatic part runs on Compose and the live public API with exact commands and
  expected counts; the manual part is only what needs the owner's real league IDs. No UI.
- **Testability:** every step has `Automatic verification:` with exact test paths.
- **Groups:** 5 groups, each step in exactly one, no boundary leaves work half done.
- **Summary:** new dependencies and the migration flagged, both accepted in SPEC → Owner
  decisions.
- **Language:** English throughout, matching `language: "en"`.

Decision: the plan is ready — no blocker, the three majors are fixed in place, and the
dependencies and the migration are accepted in SPEC → Owner decisions. Status →
`plan-approved`.

## Chunk notes

_(filled in by /pipeline:implement in chunk mode — one entry per chunk that ends at a group boundary)_

## Deviations

_(filled in by /pipeline:implement — every deviation from the plan with its rationale)_

## Final review

_(filled in by /pipeline:final-review)_
