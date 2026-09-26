---
status: implemented
stage_history:
  - "spec-draft — 2026-09-26"
  - "spec-ready — 2026-09-26"
  - "plan-draft — 2026-09-26"
  - "plan-approved — 2026-09-26"
  - "implemented — 2026-09-26"
metrics:
  started_at: 2026-09-26T22:11
  escalations: 0
  plan_steps: 20
  plan_review_blockers: 0
  plan_review_majors: 3
  plan_changes: 11
  implement_steps: 20
  implement_iterations: 8
  converge_gaps: 0
  deviations_minor: 2
  deviations_major: 0
  final_review_blockers: 0
  final_review_worth_fixing: 12
  final_review_nits: 5
  findings_accepted: 12
  findings_rejected: 5
---

# SPEC 001 — FPL data collector (database foundation and on-demand jobs)

## Goal

Every later stage needs FPL facts in our own database: players to link leaks to, league
picks and points for the presser, ground truth for settling leaks, and the FPL flag
timeline to measure how far leaks run ahead of the official flags. This spec builds the
database foundation and the collection jobs, run on demand from a CLI, and backfills the
current season. It works when a backfill fills GW1 to the latest finished gameweek for the
configured leagues, a re-run changes nothing, and the verification suite passes with only
Docker available.

## Context

- First spec of the project. `backend/` is an empty skeleton: `app/__init__.py`,
  `tests/test_smoke.py`, `pyproject.toml` with no runtime dependencies (only pytest and ruff
  in `dev`). No database, configuration, Docker Compose or migrations exist yet.
- CI (`.github/workflows/ci.yml`) runs `uv sync --all-extras`, ruff and pytest in `backend/`
  on `ubuntu-latest`, which has Docker.
- Stage 0 of the roadmap is split into two specs: this one (foundation + jobs + backfill) and
  **002** (a long-running worker that runs these jobs on a deadline-driven schedule, and the
  Railway deployment). Stage 1 extends the 002 scheduler with the 20–30 s final window.
- State of the FPL API checked on 2026-09-26: GW5 is finished and `data_checked`; the GW6
  deadline is 2026-10-10 10:00 UTC (international break). `event/{gw}/live/` exposes
  `stats.starts`, `stats.minutes` and `stats.total_points` per player and a per-fixture
  `explain`; `bootstrap-static` exposes `status`, `news`, `news_added`,
  `chance_of_playing_this_round`, `chance_of_playing_next_round`, `selected_by_percent` and
  `now_cost` per player. Past gameweeks' picks, history and transfers can still be fetched;
  past flag states cannot.

## Read context

- `docs/ROADMAP.md` — read in full. Stage 0 is the first stage, and it is not started. One
  stage at a time; Stage 0 has no LLM calls, so the LLM definition of done (Langfuse, eval
  set) does not apply.
- `docs/PROJECT.md` — read in full. FR-0.1–FR-0.4. NFRs: FPL API etiquette (cache, back
  off, never poll faster than needed); privacy (league IDs and manager names only in env or
  local config, never in the repo, logs or tests; tests use synthetic managers and leagues);
  time stored in UTC. Architecture: one worker process (spec 002); local Docker Compose on
  `pgvector/pgvector:pg16`; database tests on a PostgreSQL container. Risks: the season
  resets in July, so we store everything we need; the API may change, so we use contract
  tests on recorded payloads.
- `docs/DECISIONS.md` — read in full. Stack (Python 3.12, SQLModel, Alembic,
  PostgreSQL 16, uv). FPL data comes only through the unofficial public API, cached, with
  back-off. Module layout: `app/` with `core`, `db`, `fpl`, …. Environments: local Compose
  and production on Railway, no staging. No cron. Alerts never come from the FPL flags (the
  flags are stored only as a timeline to compare against).
- `docs/BACKLOG.md` — read in full. No item concerns the FPL collector.
- `docs/adr/` — read in full (0001–0004 and the README). 0002 (facts through SQL only)
  applies. 0001, 0003 and 0004 do not concern this spec.
- `docs/CONVENTIONS.md` — read in full. Exact pins and a committed `uv.lock`. External APIs
  are tested on recorded payloads; payloads with managers or leagues are synthetic. Logs
  never carry manager names, league IDs, e-mail addresses or credentials. Datetimes are
  timezone-aware UTC. No docstrings.

## Scope

- Local development database: Docker Compose service on `pgvector/pgvector:pg16`; an
  `.env.example` with placeholder values; settings read from the environment
  (database URL, configured league IDs).
- Database foundation: SQLModel models and an Alembic migration history for the collector
  schema.
- FPL API client: a descriptive User-Agent, request throttling, retry with exponential
  back-off, strict parsing of the fields we use, and tolerance of unknown fields.
- Collection jobs, each run from a CLI command:
  - **reference sync**: season, gameweeks, teams, players and fixtures (FR-0.1), plus the
    player flag change log (FR-0.3);
  - **deadline snapshot**: the state of every player for a gameweek, captured before its
    deadline (FR-0.3);
  - **league sync**: for every configured classic league, the standings, managers, and for
    each manager the picks, gameweek history, transfers and chips (FR-0.2);
  - **results sync**: ground truth after a finished gameweek: starts, minutes and points
    per player, and fixture scores (FR-0.4);
  - **backfill**: the league and results syncs for every past gameweek of the current season.
- A raw payload archive (jsonb) at key moments: `bootstrap-static` at each deadline
  snapshot; `fixtures` and `event/{gw}/live` at each results sync.
- Every stored FPL entity is keyed by season as well as by its FPL ID, because FPL IDs reset
  every July.
- README "Development" section: how to start the database, apply migrations and run the jobs.

## Out of scope

- Long-running worker, deadline-driven scheduling, polling cadence, deployment to Railway →
  spec 002.
- Enabling and using pgvector, and any text or retrieval tables → Stage 2.
- Derived metrics (net points after hits, manager of the gameweek, season race) → Stage 3.
  This spec stores the raw inputs they need.
- Head-to-head leagues, cups, a manager's previous seasons (`entry/{id}/history/` → `past`),
  per-player history (`element-summary`) → not needed by any roadmap item. Add them if a
  future spec needs them.
- An HTTP API → appears only when something needs one (Wrapped).

## Requirements and acceptance criteria

Infrastructure

- [ ] AC1: `docker compose up -d` starts PostgreSQL 16 from `pgvector/pgvector:pg16`;
  `alembic upgrade head` against it creates the collector schema, and `alembic downgrade base`
  removes it without errors.
- [ ] AC2: `verify.command` passes on a machine with Docker and no running database. Tests
  that need PostgreSQL start their own throwaway container, and no test reads the database
  URL from the environment.
- [ ] AC3: The configured leagues come only from an environment variable holding a
  comma-separated list of league IDs. An empty or malformed value makes the league sync and
  the backfill exit non-zero with an error that names the variable. The repository contains
  no real league ID.

FPL client

- [ ] AC4: Every request carries a descriptive User-Agent, and consecutive requests are at
  least 500 ms apart.
- [ ] AC5: HTTP 429, 5xx, timeouts and the "The game is being updated." response are retried
  with exponential back-off, at most 5 attempts in total. When the attempts run
  out, the job exits non-zero and writes nothing.
- [ ] AC6: A payload that lacks a field we use fails the job with an error naming the endpoint
  and the field, and writes nothing. Unknown extra fields are ignored.

Reference sync (FR-0.1, FR-0.3)

- [ ] AC7: On recorded `bootstrap-static` and `fixtures` payloads, the sync stores the season,
  38 gameweeks (deadline, finished, `data_checked`), 20 teams, every player (name, team,
  position) and every fixture (gameweek, kickoff, teams, score once played). Counts match the
  payload.
- [ ] AC8: The season label is derived from the payload (e.g. `2026/27`), and every stored FPL
  entity is keyed by season and FPL ID. A payload from another season adds new rows and
  leaves the previous season's rows unchanged.
- [ ] AC9: Flag change log. The first sync of a season writes one baseline row per player
  with `status`, `news`, `news_added`, both `chance_of_playing_*` values and `observed_at`
 . Each later sync writes a row only for players
  whose `status`, `news` or a `chance_of_playing_*` value changed. An unchanged payload writes
  no rows.
- [ ] AC10: A player who appears mid-season is inserted, a player whose team changes is
  updated, and a player who disappears from the payload keeps his rows.

Deadline snapshot (FR-0.3)

- [ ] AC11: A snapshot for gameweek N taken before N's deadline stores, for every player,
  `status`, `news`, both `chance_of_playing_*`, `selected_by_percent`, `now_cost` and
  `captured_at`. It also archives the raw `bootstrap-static` payload.
- [ ] AC12: Re-running the snapshot for N before the deadline replaces the previous snapshot
  of N, so the latest one before the deadline wins. A run at or after N's deadline exits
  non-zero, names the reason and writes nothing.

League sync (FR-0.2)

- [ ] AC13: For gameweek N whose deadline has passed, the sync stores, for each configured
  league: the league, its standings rows (rank, gameweek total, overall total) and every
  member manager (entry ID, team name, manager name). It reads every standings page, so a
  synthetic league with more than 50 members spanning 2 pages yields every member.
- [ ] AC14: For every member manager and gameweek N, it stores: all 15 picks (player,
  position, multiplier, captain, vice-captain), the active chip, the automatic
  substitutions, and the gameweek history (points, transfer count, transfer cost, points on
  bench, bank, team value, overall rank). It also stores all of the manager's transfers
  (gameweek, player in, player out, prices, time) and the chips used this season.
- [ ] AC15: A manager in two configured leagues is stored once and linked to both.
- [ ] AC16: When a manager has no picks for gameweek N (joined after N; the endpoint returns
  404), the sync records that the manager has no team for N and continues with the others.
- [ ] AC17: A league sync for a gameweek whose deadline has not passed exits non-zero, names
  the reason and writes nothing.

Results sync (FR-0.4)

- [ ] AC18: For gameweek N that is `finished` and `data_checked`, the sync stores per player
  `starts`, `minutes` and `total_points` for the gameweek. In a double gameweek these are the
  sums over both fixtures, and the per-fixture breakdown (`explain`) is stored as well. It
  updates the fixture scores and archives the raw `event/{N}/live` and `fixtures` payloads.
- [ ] AC19: A results sync for a gameweek that is not both `finished` and `data_checked` exits
  non-zero, names the reason and writes nothing.

Backfill, idempotency, privacy

- [ ] AC20: Backfill runs the reference sync, then the league sync for every gameweek whose
  deadline has passed, then the results sync for every gameweek that is `finished` and
  `data_checked`. On recorded payloads for GW1–3, all three gameweeks are populated.
  Deadline snapshots are not backfilled.
- [ ] AC21: Running any job twice on the same payloads leaves every table's row count and
  content unchanged, apart from `observed_at` / `fetched_at` bookkeeping (archive rows are
  added only by a snapshot or a results sync).
- [ ] AC22: Each job runs in one transaction. A failure at any point, including one on the
  last manager of a league, leaves the database as it was before the job.
- [ ] AC23: Log output captured during a league sync on synthetic data contains none of the
  synthetic manager names, team names or league IDs.
- [ ] AC24: All stored datetimes are timezone-aware UTC.
- [ ] AC25: The README "Development" section gives the commands to start the database,
  apply migrations and run each job and the backfill.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Stage 0 is split: 001 covers the foundation, the jobs and the backfill; 002 covers the worker, the schedule and deployment | one spec for the whole stage; three specs | the scheduler is its own concern, and Stage 1 extends it; smaller specs are safer to implement autonomously |
| Backfill the current season from GW1 | collect from GW6 only | the presser (season race) and source scoring need the whole season; about 300 requests once |
| Flag change log (a row on change, a baseline on first sight) plus a full snapshot per deadline | fixed full snapshots at T-24h / T-1h / deadline only | the change log is an exact timeline of FPL flags, so Stage 4 can measure how far leaks run ahead of them; the per-deadline snapshot gives ownership for "widely owned" alerts |
| Raw payload archive at key moments only (`bootstrap-static` at a deadline snapshot; `fixtures` and `live` at a results sync) | every response; none | survives the July reset and lets us derive fields we do not model today (e.g. xG for the presser) for about 40 × 2 MB per season; archiving every hourly poll would take several GB per season |
| Database tests on testcontainers | Compose for tests + a service container in CI | `verify.command` stays self-contained for autonomous agents and parallel worktrees; one definition of the test database; no risk of hitting the dev database. Compose remains the development database |
| Entities keyed by (season, FPL ID) | FPL ID alone | FPL IDs reset every season |
| HTTP stubbed with `httpx.MockTransport`; CLI on `argparse` | respx; Typer | no extra dependencies for what the standard tools already do |
| Ground truth only after `data_checked` | provisional data after `finished` | bonus points are final only after the check; settling leaks and the presser need final numbers |

## Owner decisions

- Split Stage 0 into specs 001 and 002 — accepted (2026-09-26).
- Backfill of the current season — accepted.
- Flag change log + per-deadline snapshot — accepted.
- Raw payload archive at key moments — accepted.
- New dependencies accepted up front: `sqlmodel`, `alembic`, `psycopg[binary]` (v3),
  `httpx`, `pydantic-settings`; dev: `testcontainers[postgres]`.
- Migrations: only on the local development and test databases. Applying them in production
  is the owner's job, and it belongs to spec 002.

## Open questions (non-blocking)

- Whether the standings' `event_total` already subtracts transfer costs. Irrelevant here:
  both the standings total and the history's points and transfer cost are stored raw, and
  Stage 3 defines net points.
