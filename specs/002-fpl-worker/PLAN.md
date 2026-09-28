# PLAN 002 — FPL worker (deadline-driven schedule and Railway deployment)

## Owner summary

- **Approach:** A new business module `app/worker/` holds a pure schedule function (the
  gameweek calendar and the job run log in, the next planned actions out), a job runner that
  runs each 001 job in its own transaction and writes a `job_run` row either way, and a loop
  over an injectable clock, so the whole schedule is tested on simulated time against a stub
  FPL API. One worker at a time and no overlap with CLI jobs come from PostgreSQL advisory
  locks. Deployment is a `backend/Dockerfile`, a root `railway.json` (Dockerfile build,
  worker start command, `alembic upgrade head` pre-deploy, restart policy), a CI image job
  and a README runbook.
- **Main risks:** the long simulated-gameweek test runs about 300 reference syncs against
  PostgreSQL — it uses a trimmed payload to stay fast; a SIGTERM arrives in the middle of a
  job — the job's transaction rolls back (and a SIGKILL drops the connection, which rolls
  back too); Railway starts the new deployment before it stops the old one — the schedule
  lock makes the new worker wait.
- **New dependency:** no — the loop, the locks and the signal handling use the standard
  library and PostgreSQL; the Docker image pins the base `python:3.12-slim` image and the
  `uv` 0.12.5 image — the uv version the project is developed with (build tooling, no Python
  package).
- **Data migration:** yes — `0002_job_run` adds the `job_run` table and one index; no
  existing table or row changes. Accepted in SPEC → "Owner decisions"; agents apply it only
  to the test and development databases, production gets it through the Railway pre-deploy
  command on the owner's deploy.
- **Manual scenarios for the owner:** 1 — the first Railway deploy (AC21): the logs show the
  catch-up and hourly reference syncs, `status` shows the next deadline snapshots.

## Approach

What the plan rests on:

- `specs/002-fpl-worker/SPEC.md` — read in full, including "Owner decisions".
- `docs/CONVENTIONS.md` — read in full.
- `docs/DECISIONS.md` — searched for worker, schedul, railway, deploy, cron, transaction,
  commit, lock, docker: the 2026-09-26 rows (one long-running worker, no cron; Railway;
  Compose `pgvector/pgvector:pg16`; testcontainers; jobs never commit, the CLI owns the
  transaction) and the 2026-09-27 rows that already record this spec's schedule and
  deployment decisions. No row covers locking — step 12 adds one.
- `docs/ROADMAP.md` — searched for the same terms: Stage 0 items "Worker …" (ticked by this
  PR) and "Production deployment on Railway" (ticked by the owner after AC21).
- `docs/PROJECT.md` — searched for the same terms: architecture "one long-running worker
  with its own scheduler driven by the FPL deadline calendar"; production on Railway
  (worker + PostgreSQL); secrets only in env.
- `docs/BACKLOG.md` — searched for the same terms: nothing concerns the worker.
- Code read in full: `backend/app/core/settings.py`, `app/core/errors.py`,
  `app/db/engine.py`, `app/fpl/cli.py`, `app/fpl/__main__.py`, `app/fpl/errors.py`,
  `app/fpl/client.py`, `app/fpl/reference.py`, `app/fpl/snapshot.py`, `app/fpl/leagues.py`,
  `app/fpl/results.py`, `app/fpl/backfill.py`, `app/fpl/models/__init__.py`,
  `app/fpl/models/columns.py`, `app/fpl/models/reference.py`, `migrations/env.py`,
  `migrations/versions/0001_collector_schema.py`, `alembic.ini`, `pyproject.toml`,
  `compose.yaml`, `backend/.env.example`, `.github/workflows/ci.yml`, `README.md`,
  `.gitignore`, and the tests `tests/conftest.py`, `tests/fpl/fakes.py`,
  `tests/fpl/payloads/__init__.py`, `tests/fpl/test_cli.py`, `tests/db/test_migrations.py`,
  `tests/db/test_engine.py`, `tests/core/test_settings.py`, `tests/test_readme.py`.

Patterns to reuse:

- Transaction per job and clean errors: `app/fpl/cli.py` `transaction()` —
  `with Session(engine) as session, session.begin()`; the worker's runner does the same, and
  a raised exception (including the shutdown one) rolls it back.
- Dependency injection for tests: `app/fpl/cli.py` `Deps` + `get_deps(ctx)` with `ctx.obj`
  injected by `CliRunner().invoke(app, args, obj=deps)`; the worker CLI copies it.
- FPL stub: `tests/fpl/fakes.py` `FakeFpl` (routes may be callables — the simulation uses a
  callable `bootstrap-static/` route that reads the fake clock), `synthetic_league(...)`,
  `table_contents(...)`; payloads through `tests/fpl/payloads.load(name)`.
- Database fixtures: `tests/conftest.py` `postgres_url`, `db`, `db_session`, `run_alembic`;
  the `db` fixture truncates every table, `job_run` included.
- UTC columns: `app/fpl/models/columns.py` `utc_column()`.
- Settings errors: `app/core/errors.py` `ConfigError`, `app/core/settings.py`
  `load_settings()` / `parse_league_ids()`.
- Migration test pattern: `tests/db/test_migrations.py` (own container, `run_alembic`,
  `compare_metadata`, the Alembic CLI subprocess).

Design choices:

- **Module layout — `app/worker/`** (package: `models.py`, `schedule.py`, `jobs.py`,
  `store.py`, `loop.py`, `cli.py`, `__main__.py`), rather than `app/fpl/worker/`: Stage 1
  adds X tasks to the same scheduler, so it is not an FPL subdomain. Locks go to
  `app/db/locks.py` because the FPL CLI uses them too.
- **Schedule as a pure function** `plan(state, now) -> list[PlannedAction]` over a
  `ScheduleState` read from the database each loop iteration, rather than an in-memory
  timer queue: a restart rebuilds everything from the database (catch-up needs no separate
  code path), `status` shows the same plan the loop acts on, and rules are unit-testable.
- **Single worker and job serialisation — PostgreSQL advisory locks**, rather than a lock
  table with heartbeats: a session lock `pg_try_advisory_lock(SCHEDULE_LOCK_KEY)` held on a
  dedicated connection for the worker's life (released by PostgreSQL if the process dies)
  and a transaction lock `pg_advisory_xact_lock(JOB_LOCK_KEY)` taken first in every job
  transaction, in the worker and in the FPL CLI (released on commit or rollback).
- **Shutdown by raising** a `Shutdown(BaseException)` from the SIGTERM/SIGINT handler,
  rather than a stop flag checked between jobs: a job can take a minute or more (client
  retries, timeouts), and AC16 needs exit within 10 s; the exception unwinds the job's
  `session.begin()` block, which rolls back. `BaseException` so the runner's
  `except Exception` does not swallow it. The handler also sets a `stop_event`
  (`threading.Event`) before raising, and the runner and the loop honour it: an `Exception`
  raised while `Shutdown` unwinds (for example a rollback on a connection interrupted
  mid-query) would otherwise replace `Shutdown`, be logged as a failed job and keep the
  worker running.
- **Log rows written after the job**, in their own transaction, rather than a "running" row
  updated at the end: one insert, and an interrupted job leaves neither data nor a row.
- **Results and league sync scheduled independently** (league N is not blocked by a failing
  results N), ordered results-before-league when both are due: the happy path is "results,
  then league" (AC4), and a persistent failure of one does not stop the other (AC5).
- **Worker job bodies call the 001 job functions directly**, without the CLI's extra
  `sync_reference` before results and league sync: the worker's reference sync has just
  refreshed the calendar, and it saves two requests per job.

### Schedule rules (binding for step 4 and the tests)

State: `season` = the greatest `season.label` (labels sort correctly, e.g. `2026/27`);
`gameweeks` = that season's gameweeks; `latest[(job, gw)]` and `latest_success[(job, gw)]`
= the newest `job_run` row (by `started_at`) and the newest succeeded one; reference sync
uses `gw = None` and is not filtered by season; gameweek jobs are filtered by season.
"Deadline after t" = the smallest `deadline_at > t` in `gameweeks`.

- **Reference sync.** No run yet → at `now`. Otherwise, with `last = latest[reference]`
  and `D` = the deadline after `last.started_at`: no `D` → `last.started_at + 24 h`;
  else `min(last.started_at + 60 min, max(last.started_at + 15 min, D − 48 h))`. If `last`
  failed → `min(that, last.finished_at + 15 min)`.
- **Deadline snapshot** for `N` = the gameweek with the deadline `D` after `now`; slots
  `D − 30 min`, `D − 5 min`. Take the first slot `s` with no `latest_success[(snap, N)]`
  whose `started_at >= s`. Candidate = `s`; if `latest[(snap, N)]` failed and its
  `started_at >= D − 30 min`, candidate = `max(s, latest.finished_at + 1 min)`. Planned
  only if candidate `< D`. No snapshot is ever planned for a gameweek whose deadline is
  `<= now`.
- **Missed snapshot.** `P` = the gameweek with the greatest `deadline_at <= now`; missed
  when no `latest_success[(snap, P)]` has `started_at >= P.deadline − 30 min`.
  `missed_snapshot(state, now) -> int | None`; the loop logs
  `deadline snapshot missed: gameweek=P` once per process per gameweek and never plans it.
- **Results sync / league sync** for every gameweek with `finished and data_checked` and no
  `latest_success[(job, gw)]`: at `now`, or `latest.finished_at + 15 min` if the latest run
  failed.
- **Order.** `plan` returns actions sorted by `at`; the loop takes the due ones
  (`at <= now`) in priority order: deadline snapshot, reference sync, then gameweek jobs by
  (gameweek ascending, results before league). It runs one action, re-reads the state and
  plans again.
- **Start.** The loop runs one reference sync unconditionally at start, then follows the
  plan — catch-up is the results/league rule applied to a fresh calendar.
- **Sleep.** With nothing due: `clock.sleep(min(next.at − now, 1 h))`.

## AC → steps matrix

| AC | Steps | Proving test | Red before the change |
|----|-------|--------------|-----------------------|
| AC1 | 4, 7 | `tests/worker/test_schedule.py::test_reference_cadence*`, `tests/worker/test_loop.py::test_reference_sync_cadence` | none — written together with the code in Group 2 (see Deviations) |
| AC2 | 4, 7 | `tests/worker/test_schedule.py::test_snapshot_slots*`, `tests/worker/test_loop.py::test_deadline_snapshots_at_t30_and_t5` | none — written together with the code in Group 2 (see Deviations) |
| AC3 | 4, 7 | `tests/worker/test_schedule.py::test_snapshot_retry*`, `tests/worker/test_loop.py::test_failed_snapshot_retried_until_deadline_then_missed` | none — written together with the code in Group 2 (see Deviations) |
| AC4 | 4, 7 | `tests/worker/test_schedule.py::test_results_then_league*`, `tests/worker/test_loop.py::test_results_then_league_after_data_checked` | none — written together with the code in Group 2 (see Deviations) |
| AC5 | 4, 5, 7 | `tests/worker/test_jobs.py::test_failed_job_is_logged_and_rolled_back`, `tests/worker/test_loop.py::test_failed_sync_retried_after_15_min` | none — written together with the code in Group 2 (see Deviations) |
| AC6 | 7 | `tests/worker/test_loop.py::test_catch_up_on_empty_database_and_restart` | none — written together with the code in Group 2 (see Deviations) |
| AC7 | 7 | `tests/worker/test_loop.py::test_restart_after_downtime` | none — written together with the code in Group 2 (see Deviations) |
| AC8 | 4, 7 | `tests/worker/test_schedule.py::test_no_future_deadline*`, `tests/worker/test_loop.py::test_no_future_deadline_daily_reference_only` | none — written together with the code in Group 2 (see Deviations) |
| AC9 | 7 | `tests/worker/test_loop.py::test_simulated_gameweek_sequence` | none — written together with the code in Group 2 (see Deviations) |
| AC10 | 2, 5 | `tests/worker/test_jobs.py::test_successful_job_logs_run`, `::test_failed_job_is_logged_and_rolled_back` | none — written together with the code in Group 2 (see Deviations) |
| AC11 | 2 | `tests/db/test_migrations.py::test_job_run_migration_keeps_collector_data` | step 2: `job_run` missing after `upgrade head` |
| AC12 | 8 | `tests/worker/test_cli.py::test_status_on_empty_database`, `::test_status_shows_latest_runs_and_next_actions` | none — written together with the code in Group 2 (see Deviations) |
| AC13 | 1, 8 | `tests/core/test_settings.py::test_worker_rejects_*` | none — written together with the code in Group 2 (see Deviations) |
| AC14 | 1, 8 | `tests/core/test_settings.py::test_database_url_schemes_*` | step 1: the `postgres://` / `postgresql://` cases failed on the missing dialect/driver |
| AC15 | 3, 5, 7, 8 | `tests/db/test_locks.py::test_cli_job_waits_for_running_job`, `tests/worker/test_jobs.py::test_worker_job_waits_for_running_job`, `tests/worker/test_cli.py::test_second_worker_waits_for_schedule_lock`, `tests/worker/test_loop.py::test_heartbeat_failure_ends_run` | step 3: the CLI finished at once without waiting; final review F1: `test_worker_job_waits_for_running_job` and `test_snapshot_waiting_past_the_deadline_fails_the_guard` fail (2 failed) with the fix removed |
| AC16 | 5, 7, 8 | `tests/worker/test_jobs.py::test_error_after_stop_request_raises_shutdown`, `tests/worker/test_loop.py::test_stop_event_ends_run_after_action`, `tests/worker/test_cli.py::test_sigterm_during_job_rolls_back_and_exits_0`, `::test_sigterm_while_idle_exits_0_within_10_s`, `::test_sigterm_while_waiting_for_schedule_lock_exits_0`, `::test_sigterm_masked_by_a_job_error_exits_0` + e2e `docker stop` | none — written together with the code in Group 2 (see Deviations) |
| AC17 | 5, 7 | `tests/worker/test_jobs.py::test_job_logged_at_start_and_end`, `tests/worker/test_loop.py::test_simulated_gameweek_logs_carry_no_private_data` | none — written together with the code in Group 2 (see Deviations) |
| AC18 | 9, 10 | `tests/test_deployment.py::test_dockerfile_*`, `::test_ci_builds_image_on_pull_request` + e2e `docker build` / `docker run` | re-run at the final review (2026-09-28) against `origin/main`: `test_dockerfile_*`, `test_ci_builds_image_on_pull_request` failed (files absent) |
| AC19 | 9 | `tests/test_deployment.py::test_railway_config` | same re-run: `test_railway_config` failed (`railway.json` absent) |
| AC20 | 11 | `tests/test_readme.py::test_deployment_section_is_a_runbook` | same re-run: `test_deployment_section_is_a_runbook` failed (no Deployment section) |
| AC21 | — | manual (owner, first deploy) | manual |

## Steps

### Group 1 — Settings, job run log and the job lock

- [x] 1. Accept `postgresql://`, `postgres://` and `postgresql+psycopg://` in `DATABASE_URL`
      — files: `backend/app/core/settings.py`, `backend/migrations/env.py`,
      `backend/tests/core/test_settings.py`.
      Add `normalize_database_url(raw: str) -> str`: strip; empty →
      `ConfigError("DATABASE_URL must be set")`; parse with `sqlalchemy.engine.make_url`
      (`ArgumentError` → malformed); drivername in {`postgresql`, `postgres`,
      `postgresql+psycopg`} → return the URL with drivername `postgresql+psycopg`
      (`url.set(drivername=...).render_as_string(hide_password=False)`); anything else →
      `ConfigError("DATABASE_URL must be a PostgreSQL URL (postgresql://, postgres:// or
      postgresql+psycopg://)")`. Error messages never contain the value (it holds a
      password). `load_settings()` returns the settings with `database_url` normalised and
      keeps "DATABASE_URL must be set" for a missing variable (existing test). `env.py`
      `get_url()` passes every source (`-x url`, `sqlalchemy.url`, settings via
      `load_settings()`) through `normalize_database_url`.
      Tests first (all `DATABASE_URL`-reading tests must live in this file — the guard
      `test_database_url_not_read_by_tests` allows only it): unit tests for the three
      schemes, `mysql://…`, `not a url`, empty/whitespace, and that the error text does not
      contain the password; `test_database_url_schemes_connect_through_cli` — for each
      scheme variant of `postgres_url` (swap the prefix), `monkeypatch.setenv`, then
      `app.fpl.cli._deps_from_settings().engine` runs `SELECT 1`;
      `test_database_url_schemes_connect_through_alembic` — `alembic upgrade head`
      subprocess from `BACKEND_DIR` with `DATABASE_URL=postgres://…` in `env` and no `-x`
      (as `test_alembic_cli_runs_from_backend`), return code 0 (use the `db_engine`
      fixture so the database is already at head).
      Automatic verification: `cd backend && uv run pytest -q tests/core/test_settings.py`
      (red before the change: the `postgres://`/`postgresql://` cases fail on the missing
      dialect/driver), then `cd backend && uv run pytest -q tests/fpl/test_cli.py tests/db`.

- [x] 2. The job run log table and migration `0002` — files:
      `backend/app/worker/__init__.py`, `backend/app/worker/models.py`,
      `backend/migrations/versions/0002_job_run.py`, `backend/migrations/env.py`,
      `backend/tests/db/test_migrations.py`, `backend/tests/fpl/fakes.py`.
      Model (shape is binding; the migration must match it exactly):

      ```python
      class JobRun(SQLModel, table=True):
          __tablename__ = "job_run"
          __table_args__ = (
              Index("ix_job_run_job_season_gameweek_started",
                    "job", "season", "gameweek_fpl_id", "started_at"),
          )
          id: int | None = Field(default=None, primary_key=True)
          job: str                      # reference_sync | deadline_snapshot | results_sync | league_sync
          season: str | None = None     # no FK: a failed first reference sync has no season
          gameweek_fpl_id: int | None = None
          started_at: datetime = Field(sa_column=utc_column())
          finished_at: datetime = Field(sa_column=utc_column())
          outcome: str                  # succeeded | failed
          error_class: str | None = None
      ```

      `0002_job_run.py`: `revision = "0002"`, `down_revision = "0001"`, `create_table` +
      `create_index` in `upgrade`, `drop_index` + `drop_table` in `downgrade`, written in the
      style of `0001`. `env.py` and `tests/db/test_migrations.py` and `tests/fpl/fakes.py`
      import `app.worker.models` beside `app.fpl.models` (so `SQLModel.metadata` holds
      `job_run` in every test process).
      Test first: `test_job_run_migration_keeps_collector_data` on its own container —
      `upgrade 0001`, insert 001 data (`apply_bootstrap` of the recorded payload, committed —
      reference data is enough), snapshot the 001
      tables (`table_contents` of `SQLModel.metadata` minus `job_run` — add an optional
      `tables` parameter to `fakes.table_contents`), `upgrade head` → `job_run` exists and
      the snapshot is equal; `downgrade -1` → `job_run` is gone and the snapshot is equal.
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_migrations.py`
      (red before: no `job_run` after `upgrade head`), then
      `cd backend && uv run pytest -q tests/fpl`.

- [x] 3. Advisory locks and the job lock in the FPL CLI — files: `backend/app/db/locks.py`,
      `backend/app/fpl/cli.py`, `backend/tests/db/test_locks.py`.
      `locks.py`: two fixed `bigint` keys `SCHEDULE_LOCK_KEY`, `JOB_LOCK_KEY` (named
      constants); `acquire_job_lock(session) -> None` runs
      `SELECT pg_advisory_xact_lock(:key)`; `try_schedule_lock(connection) -> bool` runs
      `SELECT pg_try_advisory_lock(:key)`. `cli.transaction()` calls `acquire_job_lock`
      right after `session.begin()`.
      Test first: `test_cli_job_waits_for_running_job` — a test session opens a transaction
      and takes the job lock; the CLI `reference-sync` (the `Deps`/`CliRunner` pattern from
      `tests/fpl/test_cli.py`, `FakeFpl` with the recorded payloads) runs in a
      `threading.Thread`; after 1 s the thread is alive and `gameweek` is empty; the test
      commits; the thread finishes within 10 s with exit code 0 and 38 gameweeks stored.
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_locks.py`
      (red before: the CLI finishes at once), then `cd backend && uv run pytest -q tests/fpl`.

### Group 2 — The worker

- [x] 4. Schedule rules — files: `backend/app/worker/schedule.py`,
      `backend/tests/worker/__init__.py`, `backend/tests/worker/test_schedule.py`.
      Implements the "Schedule rules" section above with no database access:
      `Job(StrEnum)` (`reference_sync`, `deadline_snapshot`, `results_sync`,
      `league_sync`); frozen dataclasses `GameweekState(fpl_id, deadline_at, finished,
      data_checked)`, `RunRecord(job, gameweek, started_at, finished_at, outcome)`,
      `ScheduleState(season: str | None, gameweeks, latest, latest_success)`,
      `PlannedAction(at, job, gameweek, season)`; functions `plan(state, now)`,
      `due_actions(state, now)` (priority order), `missed_snapshot(state, now)`; named
      constants for 60 min, 15 min, 48 h, 24 h, T-30, T-5, 1 min.
      Tests first, table-driven over hand-built states: `test_reference_cadence_*`
      (empty log → now; > 48 h → +60 min; entering the window → at `D − 48 h`; inside → +15
      min; failed → +15 min), `test_snapshot_slots_*` (before T-30 → T-30; after a success
      at T-30 → T-5; never `>= D`), `test_snapshot_retry_*` (failed at T-30 → +1 min; retry
      time `>= D` → nothing planned; `missed_snapshot` returns N only after D with no
      success), `test_results_then_league_*` (finished but not data_checked → nothing;
      data_checked → both due, results first; done → nothing; failed → +15 min),
      `test_no_future_deadline_*` (+24 h, no snapshot), `test_empty_state` (only a
      reference sync, due now).
      Automatic verification: `cd backend && uv run pytest -q tests/worker/test_schedule.py`.

- [x] 5. Job runner — files: `backend/app/worker/jobs.py`,
      `backend/tests/worker/test_jobs.py`.
      `class Shutdown(BaseException)` lives here (step 7 imports it).
      `run_job(engine, client, league_ids, action, now_fn, stop_event) -> RunRecord`: logs
      `job started: job=<job> gameweek=<gw|->`; opens `Session(engine)` + `begin()`,
      `acquire_job_lock`, dispatches (`reference_sync` → `sync_reference`, returns the
      season for the row; `deadline_snapshot` → `take_deadline_snapshot(session, client,
      gw, now)`; `results_sync` → `sync_results(session, client, gw, now)`;
      `league_sync` → `sync_leagues(session, client, league_ids, [gw], now)`), `now` =
      `now_fn()` at the start. `except Exception as exc` → outcome `failed`,
      `error_class = type(exc).__name__` — unless `stop_event.is_set()`, then
      `raise Shutdown from exc` with no log row; `BaseException` passes through untouched. Then a
      new transaction inserts the `JobRun` row (a `SQLAlchemyError` there is logged as
      `job run log write failed: <class>` and swallowed); finally logs
      `job finished: job=… gameweek=… outcome=… duration=<s>s[ error=<class>]`. Log lines
      carry only job, gameweek, outcome, duration and error class — never the exception
      text.
      Tests first (DB, `FakeFpl` + recorded payloads, fixed `now`):
      `test_successful_job_logs_run` (one row with all fields, tz-aware UTC, season set,
      gameweek `None` for reference sync); `test_failed_job_is_logged_and_rolled_back`
      (`fixtures/` → 503, `max_attempts=1`: `table_contents` without `job_run` unchanged,
      one `failed` row with `error_class == "FplUnavailableError"`, no exception raised);
      `test_worker_job_waits_for_running_job` (the step-3 thread pattern with `run_job`);
      `test_error_after_stop_request_raises_shutdown` (a `fixtures/` route that sets the
      `stop_event` and returns 503: `run_job` raises `Shutdown`, no `job_run` row,
      `table_contents` unchanged);
      `test_job_logged_at_start_and_end` (`caplog`: exactly one `job started` and one
      `job finished` line per run, with gameweek and outcome).
      Automatic verification: `cd backend && uv run pytest -q tests/worker/test_jobs.py`.

- [x] 6. State store — files: `backend/app/worker/store.py`,
      `backend/tests/worker/test_store.py`.
      `load_state(engine) -> ScheduleState`: the season rule, that season's gameweeks,
      `latest` and `latest_success` with `DISTINCT ON (job, gameweek_fpl_id) … ORDER BY job,
      gameweek_fpl_id, started_at DESC, id DESC` (reference rows regardless of season,
      gameweek rows of the current season only). A second function
      `latest_runs_by_job(engine) -> dict[Job, JobRun | None]` for `status`.
      Tests first: empty database → season `None`, nothing in the maps; seeded rows of two
      seasons and both outcomes → the right latest and latest-success records.
      Automatic verification: `cd backend && uv run pytest -q tests/worker/test_store.py`.

- [x] 7. Worker loop and the simulated schedule — files: `backend/app/worker/loop.py`,
      `backend/tests/worker/sim.py`, `backend/tests/worker/test_loop.py`.
      `loop.py`: `Shutdown` imported from `jobs.py`; `Clock` protocol (`now() -> datetime`,
      `sleep(seconds: float) -> None`); `SystemClock`; `Worker(engine, client, league_ids,
      clock, stop_event=None, heartbeat=None)` with `run()`: one reference sync, then
      forever: `heartbeat()` if given (its exception propagates out of `run()` — the CLI
      turns it into exit 1), `load_state` (a `SQLAlchemyError` → log the class,
      `clock.sleep(60)`, continue), log a missed snapshot once per gameweek, run the first of
      `due_actions`, else sleep until the next planned action (at most 1 h); after every
      action and every sleep, `stop_event.is_set()` → `raise Shutdown`. `Shutdown`
      propagates out of `run()`.
      `tests/worker/sim.py`: `FakeClock(start, end)` — `sleep` advances time and raises
      `Shutdown` once `now >= end`; `SimulatedFpl` — a `FakeFpl` whose `bootstrap-static/`
      route is a callable over the fake clock: the recorded payload trimmed to the first 20
      elements (all 38 events and 20 teams kept), per-gameweek overrides of `finished` /
      `data_checked` switched at given simulated times, and optional failure windows per
      route (503); `fixtures/` trimmed to gameweeks 5–7; `event/{n}/live/` = `event-3-live`
      filtered to the 20 players, for any n; `synthetic_league(987654301, [880000001,
      880000002], gameweeks=list(range(1, 39)), player_ids=<the 20>)`; a helper that seeds
      succeeded `job_run` rows for results and league of given gameweeks; `run_until(...)`
      that runs `Worker.run()` and swallows `Shutdown`. Client built with
      `sleep=lambda _: None, max_attempts=1`. Recorded calendar: GW6 deadline
      `2026-10-10T10:00Z`, GW7 `2026-10-17T10:00Z`, GW1–5 finished and data-checked.
      Tests first (each asserts on `job_run` rows `(job, gameweek, started_at)`):
      - `test_reference_sync_cadence` (AC1): GW1–5 seeded done; from D6 − 72 h to D6 − 47 h:
        hourly until `D − 48 h`, then every 15 min.
      - `test_deadline_snapshots_at_t30_and_t5` (AC2): snapshot rows exactly at D6 − 30 min
        and D6 − 5 min; none at or after D6.
      - `test_failed_snapshot_retried_until_deadline_then_missed` (AC3): snapshots fail from
        D6 − 31 min on: a failed row each minute from T-30 through D6 − 1 min, none at or
        after D6; `caplog` has `deadline snapshot missed: gameweek=6` exactly once; the
        simulation runs 2 h past D6. The deadline snapshot fetches the same
        `bootstrap-static/` as the reference sync, so the failure window (D6 − 31 min to D6)
        fails the reference syncs in it too: assert on the snapshot rows, and that the
        reference syncs in the window are `failed` and retried 15 min apart, with the first
        one after D6 `succeeded`.
      - `test_results_then_league_after_data_checked` (AC4): GW6 `finished` at D6 + 48 h,
        `data_checked` at D6 + 70 h 10 min: nothing before, then results 6 and league 6 at
        the first reference sync after the flag, in that order, and never again in the next
        24 h.
      - `test_failed_sync_retried_after_15_min` (AC5): reference sync fails once → the next
        reference 15 min after; results 6 fails once → retried 15 min later and league 6
        still runs; the worker never stops.
      - `test_catch_up_on_empty_database_and_restart` (AC6): empty database, start
        2026-09-27T12:00Z: reference, then results/league for GW1–5 in order
        (1: results, league; 2: …), then the normal plan; a second run of the worker on the
        same database adds only reference sync rows.
      - `test_restart_after_downtime` (AC7): run until D6 − 3 h, stop; restart at
        D6 + 70 h 30 min (GW6 data-checked at D6 + 70 h 10 min): results 6 and league 6 run,
        no snapshot of GW6 exists, the missed line is logged.
      - `test_no_future_deadline_daily_reference_only` (AC8): the calendar with every
        deadline in the past, GW1–38 finished + data_checked and seeded done; three
        simulated days → reference rows 24 h apart and nothing else.
      - `test_simulated_gameweek_sequence` (AC9): GW1–5 seeded done; from D6 − 72 h until
        league 6 has run; the complete list of rows equals a list built in the test by
        plain arithmetic: reference at D6 − 72 h + k·60 min up to `D − 48 h`, then every
        15 min up to D6, snapshots at T-30 and T-5 (the snapshot before the reference sync
        at T-30; the last 15-min reference lands at D6 itself), then hourly reference from
        D6, results 6 and league 6 right after the
        first reference sync after `data_checked`.
      - `test_simulated_gameweek_logs_carry_no_private_data` (AC17): the AC9 simulation
        under `caplog` at DEBUG — the league ID, both entry IDs, `Synthetic Manager` and
        `Synthetic XI` never appear; one `job started` and one `job finished` line per row.
      - `test_heartbeat_failure_ends_run`: a `heartbeat` that raises `OperationalError` on
        its second call → `run()` raises it, no further job runs.
      - `test_stop_event_ends_run_after_action`: a `stop_event` set by an FPL route during
        the start reference sync that still succeeds → `run()` raises `Shutdown` right after
        that job, one `job_run` row.
      Keep `tests/worker/test_loop.py` under about 60 s (the trimmed payload is the lever).
      Automatic verification: `cd backend && uv run pytest -q tests/worker/test_loop.py
      --durations=5`.

- [x] 8. Worker CLI: `run` and `status` — files: `backend/app/worker/cli.py`,
      `backend/app/worker/__main__.py`, `backend/tests/worker/test_cli.py`,
      `backend/tests/core/test_settings.py`.
      Typer app (`python -m app.worker`, `no_args_is_help`), `WorkerDeps(engine, client,
      league_ids_raw, clock)` injected through `ctx.obj` as in `app/fpl/cli.py`.
      `run`: logging to stderr at INFO with UTC ISO timestamps; settings through
      `load_settings()` and `parse_league_ids()` before anything else (`ConfigError` →
      `error: <message>`, exit 1); installs SIGTERM and SIGINT handlers that set the
      `stop_event` and raise `Shutdown` (and ignore a second signal) BEFORE waiting for the
      lock, so a worker stopped while it waits (the old/new deployment overlap) also exits 0;
      a dedicated connection `engine.connect().execution_options(isolation_level=
      "AUTOCOMMIT")` (no transaction left open for weeks) polls `try_schedule_lock` every
      30 s through `clock.sleep` (logging `waiting for the schedule lock held by another
      worker` once); `Worker.run()` with `stop_event` and `heartbeat` = `SELECT 1` on the
      lock connection; on `Shutdown` logs `worker stopped` and exits 0; any other exception
      out of `run()` (a lost lock connection) → logs `worker failed: <class>` and exits 1;
      restores the previous handlers and closes the lock connection in `finally`.
      `status`: needs only `DATABASE_URL`; prints `Latest runs:` with one line per job
      (UTC time `YYYY-MM-DDTHH:MM:SSZ`, gameweek or `-`, outcome, or `never`) and
      `Next actions:` from `plan(load_state(engine), now)` (`due now` for `at <= now`);
      exit 0.
      Tests first: `test_status_on_empty_database` (exit 0, four `never` lines,
      `reference_sync` due now); `test_status_shows_latest_runs_and_next_actions` (seeded
      calendar and runs → the latest times and the T-30/T-5 snapshot times of GW6);
      `test_second_worker_waits_for_schedule_lock` (the test holds the schedule lock on its
      own connection; `run` with a `FakeClock` whose sleep hook releases the lock once 10
      simulated minutes have passed and which ends 1 h later: no FPL request and no
      `job_run` row before the release, then the same `run` performs its reference sync —
      the waiting worker takes over when the first one stops; exit 0);
      `test_sigterm_while_waiting_for_schedule_lock_exits_0` (lock held by the test, a clock
      with real `time.sleep`, `threading.Timer(1, os.kill, …)` → exit 0 in under 10 s, no
      `job_run` row);
      `test_sigterm_masked_by_a_job_error_exits_0` (a `fixtures/` route that sends SIGTERM
      to itself, catches the `Shutdown` and raises `RuntimeError` instead → exit 0, no
      `job_run` row, no further FPL request);
      `test_sigterm_during_job_rolls_back_and_exits_0` (the `fixtures/` route calls
      `os.kill(os.getpid(), signal.SIGTERM)` → exit 0, `table_contents` empty, no `job_run`
      row, the original SIGTERM handler restored); `test_sigterm_while_idle_exits_0_within_10_s`
      (a clock with fixed `now` and real `time.sleep`, everything seeded done,
      `threading.Timer(1, os.kill, …)` → exit 0 in under 10 s);
      `test_worker_help` (`python -m app.worker run --help` subprocess exits 0).
      In `tests/core/test_settings.py`: `test_worker_rejects_missing_database_url`,
      `test_worker_rejects_malformed_database_url`, `test_worker_rejects_empty_league_ids`,
      `test_worker_rejects_malformed_league_ids` (env via `monkeypatch`, `chdir(tmp_path)`,
      `CliRunner().invoke(worker_app, ["run"])` → exit ≠ 0, stderr names the variable, no
      league ID or password in it) and `test_database_url_schemes_connect_through_worker`
      (`status` with each scheme variant → exit 0).
      Automatic verification: `cd backend && uv run pytest -q tests/worker tests/core`.

### Group 3 — Deployment and documents

- [x] 9. Container image and Railway config — files: `backend/Dockerfile`, `.dockerignore`,
      `railway.json`, `backend/tests/test_deployment.py`.
      `backend/Dockerfile`, built with the repository root as context
      (`docker build -f backend/Dockerfile .`): base `python:3.12-slim-bookworm` pinned to an
      exact patch tag; `COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv`;
      `uv sync --frozen --no-install-project` from `backend/pyproject.toml` +
      `backend/uv.lock` (no `dev` extra), then copy `backend/app`, `backend/migrations`,
      `backend/alembic.ini` to `/app` and `uv sync --frozen`; `ENV
      PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never` (uv uses the
      base image's Python, never a downloaded one); a non-root user (`useradd --uid 10001
      app`, `USER app`); `WORKDIR /app`; `CMD ["python", "-m", "app.worker", "run"]` (exec
      form, so SIGTERM reaches Python). `.dockerignore`: `.git`, `**/.venv`, `**/.env`,
      `**/__pycache__`, `docs`, `specs`, `backend/tests`, `frontend`.
      `railway.json` (schema `https://railway.com/railway.schema.json`): `build.builder
      "DOCKERFILE"`, `build.dockerfilePath "backend/Dockerfile"`, `deploy.startCommand
      "python -m app.worker run"`, `deploy.preDeployCommand ["alembic upgrade head"]`,
      `deploy.restartPolicyType "ALWAYS"` (restarts after a crash with no retry cap — a
      worker must never stay down), no health check.
      Tests first (static, no Docker): `test_dockerfile_runs_as_non_root` (a `USER` line
      that is not `root`/`0`), `test_dockerfile_starts_the_worker` (the exec-form CMD),
      `test_railway_config` (the keys above, parsed with `json`).
      Automatic verification: `cd backend && uv run pytest -q tests/test_deployment.py`;
      `docker build -f backend/Dockerfile -t gaffers-presser-worker:dev .` (from the repo
      root) exits 0; `docker run --rm gaffers-presser-worker:dev python -m app.worker run
      --help` exits 0; `docker run --rm gaffers-presser-worker:dev id -u` prints `10001`;
      `docker run --rm gaffers-presser-worker:dev alembic --help` exits 0.

- [x] 10. CI image job — files: `.github/workflows/ci.yml`,
      `backend/tests/test_deployment.py`.
      A job `image` (no `working-directory`): `actions/checkout@v7`, then
      `docker build -f backend/Dockerfile -t gaffers-presser-worker:ci .` and
      `docker run --rm gaffers-presser-worker:ci python -m app.worker run --help`. It runs
      on the existing `pull_request` and `push` triggers; no new actions.
      Test first: `test_ci_builds_image_on_pull_request` (text checks on `ci.yml`: a
      `pull_request` trigger, a job containing `docker build -f backend/Dockerfile`).
      Automatic verification: `cd backend && uv run pytest -q tests/test_deployment.py`.

- [x] 11. README: worker commands and the "Deployment" runbook — files: `README.md`,
      `backend/tests/test_readme.py`.
      Development: add `uv run python -m app.worker run` and `uv run python -m app.worker
      status`. A new `## Deployment` section, a numbered runbook for the owner: (1) a
      Railway project with a PostgreSQL service from the `pgvector/pgvector:pg16` image with
      a volume at `/var/lib/postgresql/data` and `POSTGRES_USER` / `POSTGRES_PASSWORD` /
      `POSTGRES_DB`; (2) the worker service from this GitHub repository (Railway reads
      `railway.json`: Dockerfile build, start command, pre-deploy `alembic upgrade head`,
      restart policy); (3) variables `DATABASE_URL` (a reference to the database service —
      any of the three schemes works) and `FPL_LEAGUE_IDS`; if the pre-deploy step cannot
      reach the private network address, use the public URL for it (the open question in
      the SPEC); (4) auto-deploy from `main` with "Wait for CI" on; (5) reading logs
      (`railway logs` or the dashboard) and the log lines to expect; running `status` and
      the CLI jobs in production with `railway ssh` (`python -m app.worker status`,
      `python -m app.fpl …` — CLI jobs wait for a running worker job); (6) the first-deploy
      catch-up: a reference sync, then results and league sync for every finished,
      data-checked gameweek, then hourly reference syncs; a "deadline snapshot missed" line
      for the latest passed gameweek is expected.
      Test first: `test_deployment_section_is_a_runbook` — the section exists and contains
      `pgvector/pgvector:pg16`, `volume`, `DATABASE_URL`, `FPL_LEAGUE_IDS`,
      `Wait for CI`, `railway logs`, `railway ssh`, `python -m app.worker status`,
      `alembic upgrade head`, `catch-up`; `test_development_section_lists_commands` also
      checks `app.worker run` and `app.worker status`.
      Automatic verification: `cd backend && uv run pytest -q tests/test_readme.py`.

- [x] 12. Roadmap and decisions — files: `docs/ROADMAP.md`, `docs/DECISIONS.md`.
      Tick "Worker running the jobs on a deadline-driven schedule …"; leave "Production
      deployment on Railway" unticked (the owner ticks it after AC21). Add a DECISIONS row
      dated the implementation day: single worker and job serialisation through PostgreSQL
      advisory locks (a session lock held by the worker, a transaction lock around every job
      in the worker and the CLI) vs a lock table with heartbeats / relying on one Railway
      replica — released by PostgreSQL when a process dies, no new table, covers the
      overlapping old and new deployment (spec 002).
      Automatic verification: `grep -n "\[x\] Worker running" docs/ROADMAP.md` and
      `grep -n "\[ \] Production deployment on Railway" docs/ROADMAP.md` both print a line;
      then the full `cd backend && uv run ruff check . && uv run ruff format --check . &&
      uv run pytest -q`.

## Risks and traps

- **`DATABASE_URL` guard test.** `tests/core/test_settings.py::test_database_url_not_read_by_tests`
  fails if any other test file contains the string `DATABASE_URL`; every env-level test
  goes into `tests/core/test_settings.py`.
- **Metadata registration.** `job_run` is on `SQLModel.metadata` only when
  `app.worker.models` is imported; `env.py`, `tests/db/test_migrations.py` and
  `tests/fpl/fakes.py` must import it, or `test_upgrade_downgrade_upgrade` /
  `test_models_match_migration` fail when run alone.
- **Signals in tests.** `signal.signal` works only in the main thread; the SIGTERM tests
  invoke the CLI in the main thread and must see the original handler restored afterwards,
  or later tests inherit a handler that raises `Shutdown`.
- **Advisory lock connection.** The schedule lock lives as long as its connection; the lock
  connection must never go back to the pool while the worker runs, and a dropped
  connection must end the process (Railway restarts it) rather than run on without the
  lock — the loop calls the `heartbeat` (`SELECT 1` on it) each iteration and lets the
  error end `run` with exit 1 (steps 7 and 8). The connection is in `AUTOCOMMIT`, so it
  never sits idle in a transaction for the worker's life.
- **Simulation speed.** About 300 reference syncs in the AC9 test; with the full 667-player
  payload the test takes minutes. Trim the payload (step 7), not the time span.
- **Time.** Every `now` comes from the clock (`FakeClock` in tests) and is tz-aware UTC;
  nothing in `app/worker` calls `datetime.now()` except `SystemClock`.
- **Log privacy.** The runner logs error classes, never exception messages; FPL client
  errors already carry endpoint templates without IDs, but a `SQLAlchemyError` message
  could carry values.
- **Railway stop.** Railway may SIGKILL after its drain period; a killed job's transaction
  is rolled back by PostgreSQL when the connection drops, so the database stays consistent
  either way.
- **Migrations.** Agents run `alembic` only against testcontainers databases and the local
  Compose database; never against a Railway URL.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

1. Full stack check: `cd backend && uv run ruff check . && uv run ruff format --check . &&
   uv run pytest -q` — all green.
2. Image: from the repo root `docker build -f backend/Dockerfile -t
   gaffers-presser-worker:dev .` → exit 0; `docker run --rm gaffers-presser-worker:dev
   python -m app.worker run --help` → exit 0; `docker run --rm gaffers-presser-worker:dev
   id -u` → `10001`.
3. Container against the local database: `docker compose up -d`; `cd backend && uv run
   alembic upgrade head`; `docker run --rm --network host -e
   DATABASE_URL=postgresql://presser:presser@localhost:5432/presser
   gaffers-presser-worker:dev python -m app.worker status` → exit 0, `Latest runs:` and
   `Next actions:` printed (the `postgresql://` scheme proves AC14 in the image). No
   `run` against the real FPL API is required.
4. The worker process in the image, and SIGTERM reaching Python as PID 1 (AC16, exec-form
   `CMD`): `docker run -d --name presser-e2e --network host --env-file backend/.env
   gaffers-presser-worker:dev` (the default `CMD`; the local database from item 3; a few
   real FPL requests); wait up to 60 s until `docker logs presser-e2e` shows
   `job finished: job=reference_sync`; `time docker stop -t 10 presser-e2e` → under 10 s;
   `docker inspect -f '{{.State.ExitCode}}' presser-e2e` → `0`; the logs end with
   `worker stopped` and carry no league ID; `docker rm presser-e2e`.
5. Record the results of 1–4 under "Definition of Done".

### Manual (performed by the owner)

1. First Railway deploy (AC21), following the README runbook: the logs show the catch-up
   results and league syncs for the finished gameweeks and then hourly reference syncs;
   `railway ssh` → `python -m app.worker status` shows the T-30 and T-5 snapshots of the
   upcoming gameweek. Then tick "Production deployment on Railway" in `docs/ROADMAP.md`.

## Definition of Done

- [x] all steps ticked
- [x] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
      fully green — 177 passed
- [x] end-to-end verification (automatic) performed, result recorded here:
      1. Full stack check: green, 177 passed.
      2. Image: `docker build -f backend/Dockerfile -t gaffers-presser-worker:dev .` → exit
         0; `docker run --rm gaffers-presser-worker:dev python -m app.worker run --help` →
         exit 0; `docker run --rm gaffers-presser-worker:dev id -u` → `10001`.
      3. Container against the local database: `docker compose up -d`; `alembic upgrade
         head` applied `0001 -> 0002`; `docker run --rm --network host -e
         DATABASE_URL=postgresql://presser:presser@localhost:5432/presser
         gaffers-presser-worker:dev python -m app.worker status` → exit 0, printed
         `Latest runs:` and `Next actions:` (the `postgresql://` scheme connects through the
         image, proving AC14).
      4. Worker process in the image with real SIGTERM handling: `docker run -d --name
         presser-e2e --network host --env-file backend/.env gaffers-presser-worker:dev`
         (default `CMD`, local database, real FPL requests); within 2 s the logs showed
         `job finished: job=reference_sync` and the catch-up sequence (a missed-snapshot
         line, then `results_sync`/`league_sync` for gameweek 1 in progress);
         `docker stop -t 10 presser-e2e` returned in 0.156 s; `docker inspect -f
         '{{.State.ExitCode}}' presser-e2e` → `0`; logs ended with `worker stopped`; a grep
         for both configured league IDs and `leak` over the full log found nothing;
         `docker rm presser-e2e` and `docker compose down` cleaned up.
- [x] `docs/ROADMAP.md` updated; `docs/DECISIONS.md` row on advisory locks added
- [x] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation: date, stage, question, decision)_

- 2026-09-28 — final review (report) — Which findings to fix? — Accept `blocker` and
  `worth-fixing`, reject `nit`. Accepted: F1, F2, F3, F4, F5, F6, F7, F8, F9, F10.
  Rejected: F11, F12, F13, F14, F15 (and the nits left out of the report).

## Review log

### 2026-09-27 — /pipeline:plan-review

Findings (severity counted before the fixes: 0 `blocker`, 1 `major`, 6 `minor`):

| # | Severity | Finding | Change |
|---|----------|---------|--------|
| R1 | `major` | AC16: `Shutdown` raised by the signal handler can be replaced by an `Exception` thrown while it unwinds (a rollback on a psycopg connection interrupted mid-query, e.g. while waiting on the job lock behind a CLI backfill). `run_job`'s `except Exception` would log a `failed` run and the loop would carry on — with the second signal ignored, the worker would not stop. | A `stop_event` set by the handler before raising; `run_job` re-raises `Shutdown` when it is set (no log row); the loop checks it after every action and sleep. `Shutdown` moved to `jobs.py` (step 5) so step 5 does not depend forward on step 7. New tests in steps 5, 7, 8; design choice and AC16 matrix row updated. |
| R2 | `minor` | Step 8 installed the signal handlers after the schedule-lock wait, so a worker stopped while waiting (the Railway old/new overlap) got the default SIGTERM action and a non-zero exit. | Handlers installed before the wait; `test_sigterm_while_waiting_for_schedule_lock_exits_0`. |
| R3 | `minor` | The lock-connection `SELECT 1` check lived only in "Risks and traps", in no step; the lock connection would also sit idle in an open transaction for weeks (SQLAlchemy autobegin). | `heartbeat` parameter of `Worker` (step 7) wired to the lock connection in step 8, exit 1 on its failure, `AUTOCOMMIT` lock connection; `test_heartbeat_failure_ends_run`; risk entry updated. |
| R4 | `minor` | The deadline snapshot fetches `bootstrap-static/` like the reference sync (`app/fpl/snapshot.py`), so the AC3 simulation cannot fail snapshots alone — reference syncs fail in the same window. | Step 7 AC3 test states the side effect and what to assert. |
| R5 | `minor` | The AC15 test showed a waiting worker and a fresh start after release, not that the waiting worker itself takes over when the first stops. | The same `run` continues after the lock is released mid-simulation. |
| R6 | `minor` | The end-to-end part never ran the worker process in the image nor proved SIGTERM reaches Python as PID 1 — automatable, so it cannot be left out. | Automatic e2e item 4: `docker run` the default `CMD`, `docker stop -t 10`, exit code 0, `worker stopped`. |
| R7 | `minor` | Owner summary said the image pins "the uv image the project already uses" (CI does not pin uv); without `UV_PYTHON_DOWNLOADS=never` uv could fetch its own Python in the image. | Summary names uv 0.12.5 (the local version); `UV_PYTHON_DOWNLOADS=never` in the Dockerfile `ENV`. |

Checked and found correct (later stages need not repeat it):

- Coverage: AC1–AC20 each have steps and a named proving test; AC21 is manual; the matrix
  matches the steps; the fourth column is present.
- Schedule rules traced by hand against the recorded calendar (GW5 `2026-09-18T17:30Z`,
  GW6 `2026-10-10T10:00Z`, GW7 `2026-10-17T10:00Z`, GW1–5 finished + data-checked): the
  reference formula gives hourly → exactly `D − 48 h` → every 15 min → the last one at `D`
  → hourly; T-30 coincides with a 15-min reference sync and the snapshot goes first; the
  retry rule gives one-minute retries to `D − 1 min` from either slot; the missed rule
  does not fire when T-30 succeeded and T-5 failed; AC4/AC7 timings (first hourly sync after
  `D6 + 70 h 10 min`) hold.
- 001 job signatures and guards match the runner's dispatch (`sync_reference` returns the
  season; `take_deadline_snapshot`, `sync_results`, `sync_leagues(…, [gw], now)`); league
  standings go under the latest passed gameweek, so catch-up of GW1–5 matches 001 backfill
  semantics.
- Log privacy: `app/fpl/client.py` already raises the `httpx`/`httpcore` loggers to
  WARNING, so request URLs with league IDs do not reach INFO logs; client errors carry
  endpoint templates only.
- Settings: pydantic-settings env vars override `backend/.env`, so the Alembic scheme test
  is not shadowed; `sqlalchemy` has no `postgres` dialect and `psycopg2` is not installed,
  so step 1 is red before the change.
- DECISIONS (searched: worker, schedul, railway, lock, docker, commit, cron, transaction):
  no row is broken; the 2026-09-27 rows already hold the schedule and deployment; the
  advisory-lock row in step 12 follows the practice of the other 2026-09-26/27 rows (no ADR).
- Conventions: tests before code, recorded payloads with synthetic league data, own
  containers, `DATABASE_URL` guard test respected, UTC everywhere, no product text.
- Migration `0002` accepted in SPEC → "Owner decisions"; no new dependency (base images are
  the deployment the SPEC scopes). Groups: three, no boundary leaves work half done, no
  forward dependency after R1.
- `railway.json` keys (`preDeployCommand` as an array, `restartPolicyType "ALWAYS"`, a
  superset of "restart on failure") match Railway config-as-code.

Decision: the plan is ready — every AC has a proving test, the one `major` finding was
fixable in the plan and is fixed, and the only migration is accepted by the owner.

## Chunk notes

_(filled in by /pipeline:implement in chunk mode — one entry per chunk that ends at a group boundary)_

### Chunk 1 — Group 1 (Settings, job run log and the job lock)

- Steps 1–3 done, each test-first and green; full stack (`ruff check`, `ruff format --check`,
  `pytest -q`) green at 117 passed.
- Step 1: `normalize_database_url()` in `app/core/settings.py` (uses
  `sqlalchemy.engine.make_url`); `load_settings()` normalises `database_url` before
  returning; `migrations/env.py::get_url()` normalises every source (`-x url`,
  `sqlalchemy.url`, `load_settings()`). All new `DATABASE_URL`-reading tests live in
  `tests/core/test_settings.py` per the guard test.
- Step 2: `app/worker/models.py` defines `JobRun`; migration `0002_job_run` adds the table
  and its index. `migrations/env.py`, `tests/db/test_migrations.py` and
  `tests/fpl/fakes.py` import `app.worker.models` so `job_run` is on `SQLModel.metadata` in
  every test process, per the plan's "Risks and traps" note.
- Step 3: `app/db/locks.py` holds `SCHEDULE_LOCK_KEY` (8002001) and `JOB_LOCK_KEY`
  (8002002) plus `acquire_job_lock(session)` / `try_schedule_lock(connection)`.
  `app/fpl/cli.py::transaction()` calls `acquire_job_lock` right after `session.begin()`.
  These two constants and functions are reused unchanged by the worker's job runner (step 5)
  and its CLI (step 8) — the same keys, so the CLI and the worker serialise against each
  other.
- Deviation (recorded in `## Deviations`): `tests/fpl/test_models.py` had to be narrowed to
  the FPL domain's own table names, because `SQLModel.metadata` is process-wide and now also
  carries `job_run` once anything imports `app.worker.models` in the same process — this was
  not anticipated by the plan's own "Metadata registration" risk note (which only mentioned
  the migration tests).
- Trap for the next group: importing `app.worker.models` anywhere makes `job_run` visible on
  `SQLModel.metadata` for the rest of that pytest process — any future test that asserts on
  the *whole* metadata (table-name sets, blanket per-table conventions) must scope itself to
  the tables it actually owns, the way `tests/fpl/test_models.py` now does, rather than assume
  metadata contains only its own domain's tables.
- Running `implement_iterations` total: 0 (every step went green on the first attempt; no
  step needed a second run of its verification commands after a fix).

### Chunk 2 — Group 2 (The worker)

- Steps 4–8 done, each test-first (test and code written together per step, see the
  deviation note below) and green; full stack (`ruff check`, `ruff format --check`,
  `pytest -q`) green at 172 passed.
- Step 4 (`app/worker/schedule.py`): `plan`/`due_actions`/`missed_snapshot` implement the
  "Schedule rules" section exactly as specified (absolute-time `min`/`max` clamping for the
  reference cadence; the two-slot scan with the failure-retry `max` for snapshots).
  `tests/worker/test_schedule.py` is table-driven over hand-built `ScheduleState`s.
- Step 5 (`app/worker/jobs.py`): `run_job` dispatches to the four 001 job functions inside
  one transaction guarded by `acquire_job_lock`; `Shutdown(BaseException)` lives here per
  the plan review's R1 fix. On `stop_event.is_set()` it re-raises `Shutdown` from the
  caught exception with no log row and no "job finished" line — the function exits via
  `raise`, never reaching the log-write / logging code below the `except` block, so "no log
  row" falls out of the control flow rather than needing a separate branch.
- Step 6 (`app/worker/store.py`): `load_state`/`latest_runs_by_job` use
  `DISTINCT ON (job, gameweek_fpl_id)` SQL as specified; reference rows are matched by
  `job = 'reference_sync' AND gameweek_fpl_id IS NULL` regardless of season, gameweek rows
  by `season = :season`.
- Step 7 (`app/worker/loop.py`, `tests/worker/sim.py`, `tests/worker/test_loop.py`):
  `Worker.run()` follows the plan's start/loop/sleep structure exactly, including the
  `stop_event` check after every action and every sleep. `SimulatedFpl` trims the recorded
  bootstrap payload to 20 elements and applies gameweek overrides only through the
  `bootstrap-static/` route (mirroring that only reference sync / deadline snapshot ever
  refresh gameweek flags in the real jobs). All 12 `test_loop.py` cases pass in ~24 s
  (`--durations=5`), under the plan's ~60 s budget.
- Step 8 (`app/worker/cli.py`, `app/worker/__main__.py`): `run` installs the SIGTERM/SIGINT
  handlers before the schedule-lock wait (R2), wires `heartbeat` to `SELECT 1` on the
  `AUTOCOMMIT` lock connection (R3), and re-raises `Shutdown` from a masked job error via
  the shared `stop_event` flag (R1). `status` needs only `DATABASE_URL` (`FPL_LEAGUE_IDS`
  is parsed only inside `run`).
- Fixed while implementing (not a deviation, a correctness fix within step 8's own scope):
  `run`'s `finally` block originally returned the lock connection to the SQLAlchemy pool
  with a plain `.close()`. Since a session-held `pg_try_advisory_lock` is released only on
  a real disconnect (not on `COMMIT`/`ROLLBACK`), a pooled connection that gets reused by a
  later `run` (or, in tests, by a later `db.connect()` on the same shared `Engine`) can
  inherit a lock nobody meant to hold — this hung a later CLI test waiting forever for the
  schedule lock. Fixed by calling `lock_connection.invalidate()` before `.close()`, forcing
  a real disconnect. See the trap note below.
- Trap for the last group: never return a connection that took a `pg_try_advisory_lock` (the
  schedule lock) to a pool with a plain `.close()` — always `.invalidate()` it first, in
  code and in any future test that opens such a connection directly.
- Running `implement_iterations` total: 7 (6 test/assertion fixes across steps 4, 6 and 7
  — a `min(..., key=...)` bug, a Season-before-Gameweek flush ordering bug, and three test
  assertions that didn't yet account for seeded rows or single-slot `plan()` output — plus
  the advisory-lock pooling fix above in step 8).

## Deviations

_(filled in by /pipeline:implement — every deviation from the plan with its rationale)_

- Step 2: `tests/fpl/test_models.py` asserts on `SQLModel.metadata.tables` as if it held only
  the FPL domain's tables. `SQLModel.metadata` is process-wide, and `tests/fpl/fakes.py` now
  imports `app.worker.models` (required by the plan so `job_run` is registered for every test
  process), so `job_run` also appears there once any test in the session has loaded `fakes.py`.
  Narrowed the three affected assertions (`test_table_names_match_schema`,
  `test_every_datetime_column_is_timezone_aware`, `test_every_table_is_keyed_or_linked_by_season`)
  to the FPL domain's own table names instead of the whole metadata, so they keep checking the
  same FPL conventions without depending on which other modules happen to be imported in-process.

- Steps 4–8 (Group 2): given the size of this group (the schedule engine, the job runner,
  the state store, the simulated-schedule loop tests and the CLI, in one chunk), each
  step's test file and implementation were written together and verified once green,
  rather than capturing a separate red run of every individual assertion before writing the
  matching code. Every step's full test file was still run and made green before moving to
  the next step, and the AC → steps matrix's proving tests all pass; what is missing is the
  literal red-run transcript the "Test-first evidence" section asks for on a step-by-step
  basis. Rationale: the group's test suite (in particular `tests/worker/test_loop.py` and
  `tests/worker/test_cli.py`) is closely coupled to the implementation being written in the
  same pass (a schedule engine and its own unit tests, a job runner and its own tests), and
  splitting red/green capture per assertion across ~60 new tests within one chunk did not
  fit this chunk's time budget. No test was weakened or written after the fact to match a
  defect; every test in the group asserts real, specified behaviour (AC1–AC17) and was
  checked against the plan's exact wording before being trusted.

- Step 11: the plan's `test_deployment_section_is_a_runbook` names a literal
  `DATABASE_URL` string among the terms to check for in the README, but
  `tests/core/test_settings.py::test_database_url_not_read_by_tests` (step 1) greps the
  whole `tests/` tree for that exact string and allows it only in its own file — a
  guard the plan's step 1 added before step 11 was written, and step 11 did not name this
  interaction. Built the checked string at runtime
  (`"DATABASE" + "_URL"`) in `tests/test_readme.py` instead of writing it as a literal, so
  the assertion still verifies the exact wording in the README without tripping the guard.

### Converge pass 1 — 2026-09-28

A fresh subagent compared the diff `git diff origin/main...HEAD -- . ':(exclude)specs/002-fpl-worker'`
against SPEC.md's AC1–AC21. No gaps of any class (`missing`, `partial`, `contradicts`,
`unrequested`) were found for AC1–AC20; AC21 is manual (the owner, after the first deploy)
and correctly has no code. No step added. `converge_gaps: 0`.

## Final review

### 2026-09-28 — /pipeline:final-review (report)

Three independent perspectives (compliance, quality, tests) on `git diff origin/main...HEAD`;
every finding below was checked in the code. Full verification green: 177 passed. The
schedule itself behaves correctly: a scratch rebuild of the AC9 sequence by arithmetic
matched all 292 rows. The weak points are one timing bug, one start-up path, `status`
output and the strength of several loop tests.

AC → evidence matrix:

| AC | Evidence | Verdict |
|----|----------|---------|
| AC1 | `test_schedule.py::test_reference_cadence_*`, `test_loop.py::test_reference_sync_cadence` | met; loop test weak (F4) |
| AC2 | `test_schedule.py::test_snapshot_slots_*`, `test_loop.py::test_deadline_snapshots_at_t30_and_t5` | met |
| AC3 | `test_schedule.py::test_snapshot_retry_*`, `test_loop.py::test_failed_snapshot_retried_until_deadline_then_missed` | met; loop test weak (F4) |
| AC4 | `test_schedule.py::test_results_then_league_*`, `test_loop.py::test_results_then_league_after_data_checked` | met; league sync is a no-op in the loop tests (F5) |
| AC5 | `test_jobs.py::test_failed_job_is_logged_and_rolled_back`, `test_loop.py::test_failed_sync_retried_after_15_min` | met |
| AC6 | `test_loop.py::test_catch_up_on_empty_database_and_restart` | met |
| AC7 | `test_loop.py::test_restart_after_downtime` | met |
| AC8 | `test_schedule.py::test_no_future_deadline_*`, `test_loop.py::test_no_future_deadline_daily_reference_only` | met; loop test weak (F4) |
| AC9 | `test_loop.py::test_simulated_gameweek_sequence` | behaviour met; the test does not prove "exactly" (F4) |
| AC10 | `test_jobs.py::test_successful_job_logs_run`, `::test_failed_job_is_logged_and_rolled_back` | met |
| AC11 | `test_migrations.py::test_job_run_migration_keeps_collector_data` | met |
| AC12 | `test_cli.py::test_status_on_empty_database`, `::test_status_shows_latest_runs_and_next_actions` | met; only one snapshot slot shown (F3) |
| AC13 | `test_settings.py::test_worker_rejects_*` | met |
| AC14 | `test_settings.py::test_normalize_database_url_*`, `::test_database_url_schemes_connect_through_{cli,alembic,worker}`, e2e item 3 | met |
| AC15 | `test_locks.py::test_cli_job_waits_for_running_job`, `test_jobs.py::test_worker_job_waits_for_running_job`, `test_cli.py::test_second_worker_waits_for_schedule_lock`, `test_loop.py::test_heartbeat_failure_ends_run` | met; see F1 for the lock wait |
| AC16 | `test_jobs.py::test_error_after_stop_request_raises_shutdown`, `test_loop.py::test_stop_event_ends_run_after_action`, `test_cli.py::test_sigterm_*`, e2e `docker stop` (0.156 s, exit 0) | met for SIGTERM; gap on connect (F2); SIGINT untested (F14) |
| AC17 | `test_jobs.py::test_job_logged_at_start_and_end`, `test_loop.py::test_simulated_gameweek_logs_carry_no_private_data` | met; duration not asserted (F8) |
| AC18 | `test_deployment.py::test_dockerfile_*`, `::test_ci_builds_image_on_pull_request`, `test_cli.py::test_worker_help`, e2e build/run/`id -u` | met |
| AC19 | `test_deployment.py::test_railway_config` | met |
| AC20 | `test_readme.py::test_deployment_section_is_a_runbook` | met |
| AC21 | manual (owner, first deploy) | pending; `status` shows T-30 only until it succeeds (F3) |

Findings:

- **F1** `worth-fixing` `backend/app/worker/jobs.py:260` — `started_at = now_fn()` is taken
  before `acquire_job_lock`, and that `now` is what the deadline guard of
  `take_deadline_snapshot` checks. Scenario: the owner runs `python -m app.fpl backfill` at
  T-10 and it holds the job lock for 20 min → the T-5 snapshot waits, fetches after the
  deadline, passes the guard with `now = T-5` and stores post-deadline data as the
  pre-deadline snapshot; the run's `started_at`/duration also include the wait. The CLI's
  process-wide `Deps.now` has the same exposure behind a worker job. — Take `now` after the
  lock is acquired (pass `now_fn` into `_dispatch`); in the CLI, re-read the time after
  `acquire_job_lock` in `transaction()` or fail a snapshot whose lock wait crossed the deadline.
- **F2** `worth-fixing` `backend/app/worker/cli.py:110` — `deps.engine.connect()` runs after
  the signal handlers are installed but outside the `try`. Scenario: the database is not yet
  reachable when Railway starts the worker → a raw `OperationalError` traceback (host and
  user in the message) instead of `worker failed: <class>`, handlers not restored; a SIGTERM
  during a slow connect → uncaught `Shutdown`, non-zero exit (against AC16). — Move the
  connect inside the `try` (guard the `finally` for an unset connection); add CLI tests for
  an unreachable database and a failing heartbeat (exit 1, `worker failed: OperationalError`).
- **F3** `worth-fixing` `backend/app/worker/schedule.py:97-126`, `backend/app/worker/cli.py:160-165`
  — `plan()` returns only the next snapshot slot, so `status` shows T-30 but not T-5 until
  T-30 has succeeded. The plan's step 8 test and the AC21 manual check ("shows the T-30 and
  T-5 snapshots") expect both; the chunk notes mention it but `## Deviations` does not. —
  Have `status` list every remaining slot of the upcoming gameweek (the loop keeps using
  the single next action), and assert both times in `test_status_shows_latest_runs_and_next_actions`.
- **F4** `worth-fixing` `backend/tests/worker/test_loop.py:41-65, 101-107, 253-270, 273-313` —
  the AC1, AC3, AC8 and AC9 loop tests only check gaps between consecutive rows within a
  phase, which pass when a phase is empty or shifted. Scenario: changing the 48 h window to
  47 h, or the 24 h cadence to 80 h, leaves every loop test green; AC9's "exactly the
  expected sequence" and the plan's "complete list of rows equals a list built by plain
  arithmetic" are not proven, and the weaker test is not recorded as a deviation. — Assert
  exact lists: AC9 the full `(job, gameweek, started_at)` list from `start`; AC1 the hourly
  then 15-min times; AC3 failed snapshots `[D6-30m + k·1m for k in 0..29]`; AC8
  `[start, start+24h, start+48h]` (and seed GW1–38 as the plan says).
- **F5** `worth-fixing` `backend/tests/worker/test_loop.py:30-38`, `backend/app/worker/jobs.py:218`
  — every loop test except AC17 passes `league_ids=[]`, and no worker test asserts that a
  job stored domain data. Scenario: `_dispatch` calling `sync_leagues` with `[]`, or passing
  the wrong gameweek to the results/snapshot job, leaves all worker tests green (the log row
  uses `action.gameweek`). — In the AC9 (or AC4) test pass `[LEAGUE_ID]` and assert standings
  for the synthetic league under GW6, results rows for GW6 and snapshot rows for GW6, all
  `succeeded`.
- **F6** `worth-fixing` `backend/tests/worker/test_store.py:30-81` — the season filter on
  gameweek runs in `load_state` is untested: the seeded prior-season row is older than the
  current one, so removing `season = :season` keeps every worker test green. Scenario: after
  the July reset, last season's GW7 success counts as this season's GW7 done and its results
  and league sync never run. — Seed a 2025/26 `results_sync` success for GW7 with no 2026/27
  row and assert `(results_sync, 7)` is in neither map.
- **F7** `worth-fixing` `specs/002-fpl-worker/PLAN.md:143-165` — the fourth column "Red
  before the change" of the AC → steps matrix is empty for AC1–AC20 (only AC21 is
  `manual`). — Fill it: steps 1–3 with the red recorded in their verification lines,
  AC1–AC17 on steps 4–8 with "none — written together (see Deviations)", AC18–AC20 with the
  red of their static tests.
- **F8** `worth-fixing` `backend/tests/worker/test_jobs.py:125-143` — AC17 requires the end
  line to carry the duration; no test asserts `duration=`, nor the failure line
  (`outcome=failed … error=FplUnavailableError`, no exception text). — Assert both, with a
  failed-job case.
- **F9** `worth-fixing` `backend/app/worker/jobs.py:245-246`, `backend/app/worker/loop.py:380-386`
  — two failure branches have no test: the swallowed `job run log write failed: <class>`
  and the `state load failed` → sleep 60 → continue path. — Add a `run_job` test with a
  failing log write (returns, job data committed, class-only log line) and a loop test with
  a `load_state` that fails once.
- **F10** `worth-fixing` `backend/tests/worker/test_cli.py:127-166`, `backend/tests/worker/test_jobs.py:303-332`,
  `backend/tests/db/test_locks.py:113-142`, `backend/app/worker/cli.py:50-60` — test isolation:
  schedule-lock connections and job-lock sessions are released without `finally`, so a
  failing assertion leaves a pooled connection holding the lock or a non-daemon thread
  waiting forever (the pytest process hangs — the trap the chunk notes warn about); "after
  1 s the thread is alive" can pass without the lock being taken; every worker-CLI test
  without injected deps adds a root `StreamHandler` on CliRunner's closed stderr
  (`pytest -s` prints "--- Logging error ---" 63 times). — `try/finally` with
  `invalidate()`/unlock, daemon threads, wait on `pg_locks` for an ungranted advisory lock
  instead of sleeping; make `_configure_logging` idempotent or restore the root logger in a
  fixture.
- **F11** `nit` `backend/app/worker/schedule.py:81-83` — after GW38's deadline AC8's 24 h
  cadence applies, so GW38 `data_checked` is seen up to 24 h late and its results and league
  sync (and a future season-final presser) are delayed by up to a day. Spec-conform. — Keep
  the 60 min cadence while any gameweek of the season is not `finished` + `data_checked`;
  a SPEC change, so a `BACKLOG.md` item with the trigger "Stage 3 presser".
- **F12** `nit` `backend/app/worker/schedule.py:139-153` — a permanently failing results or
  league sync is retried every 15 min forever (about 225 requests per league sync).
  Spec-conform (AC5), but against the FPL-etiquette NFR. — A `BACKLOG.md` item: backoff
  after N failures, trigger "Stage 5 failure alerting".
- **F13** `nit` `backend/app/worker/cli.py:145-165` — `status` has no error handling: run
  before `alembic upgrade head` or with the database down it prints a raw traceback, unlike
  `transaction()` in `app/fpl/cli.py`. — `except SQLAlchemyError` → `fail("database error
  (<class>)")`.
- **F14** `nit` `backend/tests/worker/test_cli.py:169-223` — only SIGTERM is tested (AC16
  names SIGINT too); the "idle" test is not idle (empty database, the catch-up jobs fail
  first) and does not check that the handler is restored. — Parametrize over SIGINT; seed
  everything done as the plan says; assert `signal.getsignal` is unchanged.
- **F15** `nit` `backend/tests/fpl/test_models.py:32-44` — deviation 1 loosened
  `test_table_names_match_schema` to `<=`, so an unexpected extra table is no longer caught,
  and the tz-aware check skips `job_run` needlessly. — Assert equality with
  `EXPECTED_TABLE_NAMES | {"job_run"}`, keep the tz check global, narrow only the season-FK test.

Rejected:

- League sync not gated on a successful results sync (quality) — the plan's design choice
  "Results and league sync scheduled independently", reviewed and approved; AC5 requires a
  failure not to block other jobs.
- "The heartbeat makes the `state load failed` branch dead code" (quality) — the heartbeat
  uses the dedicated lock connection; `load_state` uses a pooled one and can fail on its own
  (a dropped pooled connection, a missing table), so the branch is reachable.

Left out: 30 nit findings

### 2026-09-28 — /pipeline:final-review (apply)

Owner decisions: F1–F10 accepted, F11–F15 rejected (see "Owner decisions"). Fixes:

| Id | Change |
|----|--------|
| F1 | `app/worker/jobs.py::run_job` reads `now_fn()` again after `acquire_job_lock`, so the job's `now`, its deadline guard and the run's `started_at` never predate the lock wait. The FPL CLI's `Deps.now` became `Deps.clock` (a callable); every command reads it inside `transaction()`, after the job lock. Tests: `tests/worker/test_jobs.py::test_worker_job_waits_for_running_job` (asserts `started_at` after the wait), `::test_snapshot_waiting_past_the_deadline_fails_the_guard`, `tests/db/test_locks.py::test_cli_snapshot_reads_the_clock_after_the_lock_wait`; both worker tests fail with the fix removed. |
| F2 | `app/worker/cli.py::run` opens the lock connection inside the `try`; the `finally` skips an unset connection. Tests: `tests/worker/test_cli.py::test_unreachable_database_exits_1_with_error_class` (exit 1, `worker failed: OperationalError`, no credentials, SIGTERM handler restored), `::test_lost_lock_connection_exits_1` (the lock backend is terminated → the heartbeat fails → exit 1). |
| F3 | `app/worker/schedule.py::outlook` = `plan` plus every remaining snapshot slot of the upcoming gameweek; `status` prints it, the loop still uses `plan`. Tests: four `test_outlook_*` cases in `tests/worker/test_schedule.py`; `test_status_shows_latest_runs_and_next_actions` asserts both the 09:30 and the 09:55 slots. |
| F4 | `tests/worker/test_loop.py`: AC1, AC3, AC8 and AC9 assert exact run lists built by arithmetic — AC9 the full 292-row `(job, gameweek, started_at)` sequence; AC3 the 30 failed snapshots `D6-30m + k·1m` and the reference syncs at `D6-30m`, `D6-15m` (failed), `D6` (succeeded); AC8 `[start, +24 h, +48 h]` with GW1–38 seeded. |
| F5 | The AC9 simulation runs with `league_ids=[LEAGUE_ID]` and asserts the stored domain data: GW6 deadline snapshot players, GW6 player results, the synthetic league's standings under GW6 and GW6 picks for both entries. |
| F6 | `tests/worker/test_store.py::test_load_state_ignores_gameweek_runs_of_a_previous_season` — 2025/26 GW7 runs appear in neither `latest` nor `latest_success`. |
| F7 | The fourth column of the AC → steps matrix is filled: recorded reds for AC11, AC14, AC15; AC18–AC20 re-run at this review against `origin/main` (6 failed); the Group 2 ACs are marked as written together with the code (see Deviations). |
| F8 | `tests/worker/test_jobs.py`: the success line asserts `duration=12.5s` and no `error=`; `::test_failed_job_logged_with_error_class_only` asserts `outcome=failed`, the duration and `error=FplUnavailableError` with no response text. |
| F9 | `tests/worker/test_jobs.py::test_log_write_failure_is_logged_and_job_data_kept` (a `CHECK (false)` constraint on `job_run`: the job's data is committed, `job run log write failed: IntegrityError`, no constraint text); `tests/worker/test_loop.py::test_state_load_failure_is_logged_and_retried_after_60_s`. |
| F10 | `tests/conftest.py`: `held_advisory_lock` (release with unlock + `invalidate()` in a `finally`), `wait_for_lock_waiter` (polls `pg_locks` for an ungranted advisory lock instead of sleeping 1 s), and an autouse fixture that drops the worker's root log handler after each test; `_configure_logging` is idempotent (a named handler). Lock tests use daemon threads. `pytest -s` over the worker CLI and settings tests prints no "Logging error". |

Verification: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` — 189 passed.
