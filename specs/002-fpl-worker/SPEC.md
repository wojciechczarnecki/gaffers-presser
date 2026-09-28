---
status: done
stage_history:
  - "spec-draft — 2026-09-27"
  - "spec-ready — 2026-09-27"
  - "plan-draft — 2026-09-27"
  - "plan-approved — 2026-09-27"
  - "implemented — 2026-09-28"
  - "done — 2026-09-28"
metrics:
  started_at: 2026-09-27T21:15
  escalations: 0
  plan_steps: 12
  plan_review_blockers: 0
  plan_review_majors: 1
  plan_changes: 7
  implement_steps: 12
  implement_iterations: 7
  implement_chunks: 3
  converge_gaps: 0
  deviations_minor: 3
  deviations_major: 0
  final_review_blockers: 0
  final_review_worth_fixing: 10
  final_review_nits: 5
  findings_accepted: 10
  findings_rejected: 5
  finished_at: 2026-09-28T08:51
---

# SPEC 002 — FPL worker (deadline-driven schedule and Railway deployment)

## Goal

Spec 001 gave us collection jobs that someone has to run by hand. This spec adds a
long-running worker that runs them on its own, driven by the FPL deadline calendar, and
deploys it to Railway. The owner then gets, with no manual step, a flag timeline, a
snapshot right before every deadline, and final league and results data after every
gameweek. It works when the worker on Railway takes the GW snapshot before the deadline
and stores the gameweek's results and league data after `data_checked` without anyone
touching it, and when a restart or a missed window heals itself on the next start.

## Context

- Builds on spec 001 (`done`). Jobs live in `backend/app/fpl/` (`reference.py`,
  `snapshot.py`, `leagues.py`, `results.py`, `backfill.py`). Jobs never commit. The Typer
  CLI (`backend/app/fpl/cli.py`, `python -m app.fpl`) opens one transaction per command and
  stays as the manual operations tool.
- The CLI fixes `now` once per process (`Deps.now`), which is fine for a one-shot command
  but not for a process that runs for weeks.
- `backend/app/core/settings.py` reads `DATABASE_URL` and `FPL_LEAGUE_IDS`.
  `make_engine` uses psycopg v3, so the URL must use the `postgresql+psycopg://` scheme.
  Railway exposes `postgresql://…`.
- The jobs' guards already exist: a snapshot at or after the deadline fails, a league sync
  before the deadline fails, and a results sync before `finished` + `data_checked` fails.
  A re-run of any job is idempotent (001 AC21).
- There is no Dockerfile, no Railway configuration and no process entry point other than
  the CLI. CI (`.github/workflows/ci.yml`) runs only `verify.command`.
- Stage 1 extends this scheduler with a final window that polls X every 20–30 s before a
  deadline. The scheduler must be able to take that task on, but this spec does not build
  it.

## Read context

- `docs/ROADMAP.md` — read in full. Stage 0, second half: "002 — worker, schedule,
  deployment". Stage 0 makes no LLM calls, so the LLM definition of done does not apply.
  One stage at a time: Stage 0 is finished by this spec.
- `docs/PROJECT.md` — read in full. FR-0.1–FR-0.4 (the jobs 002 schedules). Architecture:
  one long-running worker with its own scheduler driven by the FPL deadline calendar;
  production on Railway (worker + PostgreSQL), no staging. NFRs: FPL API etiquette (never
  poll faster than needed); privacy (league IDs only in env, never in logs); time in UTC.
  Risk: the season resets in July.
- `docs/DECISIONS.md` — read in full. One long-running worker with its own deadline-driven
  scheduler, no cron. Railway hosting. Local Compose on `pgvector/pgvector:pg16`, no
  staging. Jobs never commit; the CLI owns the transaction and stays for manual operations.
  League standings are stored under the latest gameweek whose deadline has passed. Entities
  keyed by (season, FPL ID). Code organised by business module under `app/`.
- `docs/BACKLOG.md` — read in full. No item concerns the worker or deployment.
- `docs/adr/` — read in full (README, 0001–0004). None of them concerns scheduling or
  deployment. 0002 (one PostgreSQL with pgvector) is why the production database needs
  pgvector.
- `docs/CONVENTIONS.md` — read in full. Exact pins; tests on recorded payloads; database
  tests on their own container; logs never carry league IDs, manager names or credentials;
  timezone-aware UTC; a business module per directory.

## Scope

- A worker process (its own entry point in the `app` package) that runs until stopped and
  schedules the 001 jobs from the gameweek calendar in the database:
  - **reference sync** every 60 min, and every 15 min in the 48 h before a deadline;
  - **deadline snapshot** of gameweek N at T-30 min and T-5 min before N's deadline;
  - **results sync, then league sync** for gameweek N, once the worker sees N as
    `finished` and `data_checked`;
  - **catch-up on start**: the results and league sync for every `finished` +
    `data_checked` gameweek of the current season that has no successful run of that job.
- A job run log: a new table with one row per job run (job, gameweek, start, end, outcome,
  error class), written whether the job succeeds or fails. The schedule reads it to decide
  what is done.
- A `status` command that prints the latest run of each job and the next planned actions.
- Settings: accept the `postgresql://` and `postgres://` schemes of `DATABASE_URL` as well
  as `postgresql+psycopg://`, in the worker, the CLI and Alembic.
- Deployment: a container image of `backend/`, Railway config-as-code (build from the
  Dockerfile, the worker start command, migrations in the pre-deploy command, a restart
  policy), and a README "Deployment" runbook that the owner follows.
- A CI job that builds the image.

## Out of scope

- The X final window (polling every 20–30 s) and any tweet task → Stage 1, which extends
  this scheduler.
- Failure alerting (a silent source, a failed job, a missed deadline window) → Stage 5,
  roadmap item "Failure alerting". The job run log is its input.
- A league sync right after the deadline → rejected (see Decisions). Add it when a feature
  needs picks before `data_checked`.
- Triggering the presser after the league sync → Stage 3.
- An HTTP endpoint or health check server → none needed; add it when something needs one.
- Performing the deployment and running migrations in production → the owner, following
  the runbook. Agents never touch production.

## Requirements and acceptance criteria

Schedule — all on a simulated clock, with the FPL API stubbed

- [ ] AC1: With the next deadline more than 48 h away, the worker runs a reference sync
  every 60 min. In the 48 h before a deadline it runs one every 15 min.
- [ ] AC2: For gameweek N, the worker runs a deadline snapshot at T-30 min and at T-5 min
  before N's deadline, and never at or after the deadline.
- [ ] AC3: A failed deadline snapshot is retried every 1 min until N's deadline. When the
  deadline passes with no successful snapshot of N, the worker logs that the snapshot of N
  was missed and does not try again.
- [ ] AC4: When a reference sync shows gameweek N as `finished` and `data_checked` and the
  job run log has no successful results sync for N, the worker runs the results sync for N
  and then the league sync for N. It does not run them again once both have succeeded.
- [ ] AC5: A failed reference, results or league sync is retried 15 min later. It does not
  stop the worker, and the other scheduled jobs keep running.
- [ ] AC6: A worker started with an empty database runs the reference sync, then the
  results sync and league sync for every `finished` + `data_checked` gameweek of the
  season, then follows the normal schedule. Started again, it runs neither sync for those
  gameweeks.
- [ ] AC7: A worker started after a downtime runs the results and league sync for every
  gameweek that became `finished` + `data_checked` while it was down. A deadline snapshot
  missed during the downtime is not taken afterwards.
- [ ] AC8: With no future deadline in the season (after GW38, or before the new season's
  data appears in July), the worker runs a reference sync every 24 h and nothing else.
- [ ] AC9: A simulated gameweek, from 72 h before N's deadline until N is `data_checked`,
  produces exactly the expected sequence of job runs from AC1, AC2 and AC4.

Job run log and status

- [ ] AC10: Every job run the worker performs adds one row to the job run log with the job,
  the gameweek (none for a reference sync), start and end times (timezone-aware UTC), the
  outcome (`succeeded` / `failed`) and, on failure, the error class. A failed job leaves no
  data from its own transaction, but its log row is kept.
- [ ] AC11: The migration that adds the job run log applies with `alembic upgrade head` and
  is reverted with `alembic downgrade -1` on a database holding 001 data, without changing
  that data.
- [ ] AC12: The `status` command prints, for each job, the latest run (time, gameweek,
  outcome) and the next planned actions with their UTC times. It exits 0 on an empty
  database.

Process behaviour

- [ ] AC13: With `FPL_LEAGUE_IDS` or `DATABASE_URL` empty or malformed, the worker does not
  start: it exits non-zero with an error that names the variable.
- [ ] AC14: `DATABASE_URL` values with the `postgresql://`, `postgres://` and
  `postgresql+psycopg://` schemes all connect through psycopg v3, in the worker, the CLI
  and Alembic.
- [ ] AC15: Only one worker holds the schedule at a time: a second worker started against
  the same database waits and runs no job until the first one stops. A CLI job and a
  worker job never run at the same time; the later one waits for the earlier one to finish.
- [ ] AC16: On SIGTERM or SIGINT the worker exits with code 0 within 10 s. A job still
  running at that moment leaves the database as it was before the job.
- [ ] AC17: Each job run is logged once at its start and once at its end, with the job,
  the gameweek, the duration and the outcome. Log output captured during a simulated
  gameweek contains no league ID, manager name or team name.

Deployment

- [ ] AC18: The image builds from the repository with `docker build`, runs as a non-root
  user, and `docker run <image> <worker command> --help` exits 0. A CI job builds the
  image on every pull request.
- [ ] AC19: The Railway config-as-code file builds from the Dockerfile, starts the worker,
  runs `alembic upgrade head` as the pre-deploy command and restarts the worker on failure.
- [ ] AC20: The README "Deployment" section is a runbook: a PostgreSQL service from
  `pgvector/pgvector:pg16` with a volume, the worker service, the environment variables
  (`DATABASE_URL`, `FPL_LEAGUE_IDS`), auto-deploy from `main` with "Wait for CI", how to
  read the logs and run `status` and the CLI jobs in production, and the first-deploy
  catch-up to expect.
- [ ] AC21 (manual, the owner): after the first deploy, the Railway logs show the catch-up
  results and league syncs for the finished gameweeks and hourly reference syncs; `status`
  run through Railway shows the next deadline snapshots of the upcoming gameweek.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| League sync once per gameweek, after `data_checked`, right after the results sync | also a sync about 1 h after the deadline | every roadmap consumer (presser, settling leaks, league ownership for the next alerts) needs final data; picks do not change after the deadline; saves about 225 requests per gameweek (owner, 2026-09-27) |
| Reference sync every 60 min, every 15 min in the 48 h before a deadline | every 30 min; every 60 min | the flag timeline is as precise as the polling; flags move mostly after the press conferences 1–2 days before a deadline, so dense polling only there (owner, 2026-09-27) |
| Deadline snapshots at T-30 and T-5, a failed one retried every minute until the deadline | one at T-10; T-60/T-15/T-2 | the latest snapshot before the deadline wins (001), T-30 is a fallback if FPL errors near the deadline, T-5 leaves room for retries (owner, 2026-09-27) |
| A job run log table read by the scheduler | deriving the state from the collected data | a restart knows what already ran; failures have a history; Stage 5 alerting needs it (owner, 2026-09-27) |
| Catch-up on start instead of a separate backfill step | only future events; a manual backfill | the first deploy and every downtime heal themselves; the CLI `backfill` stays as a tool (owner, 2026-09-27) |
| Migrations in the Railway pre-deploy command | the owner runs them by hand before a deploy | a deploy the owner triggers migrates first; a failed migration blocks the deploy; new code never runs on an old schema; agents never run it (owner, 2026-09-27) |
| Auto-deploy from `main` with "Wait for CI" | a manual `railway up` | merging a PR (the owner's gate 3) is the deploy; red CI blocks it (owner, 2026-09-27) |
| Production PostgreSQL from `pgvector/pgvector:pg16` with a volume | the Railway Postgres template | the same image as Compose and the tests; Stage 2 needs pgvector, and switching databases later would mean migrating production data (owner, 2026-09-27) |
| Our own scheduling loop, no new dependency | APScheduler; Railway cron | the schedule is a function of the deadline calendar and the job run log, not a fixed cron table; a plain loop over a clock can be tested on simulated time; cron is already rejected in DECISIONS |
| No HTTP health check | a small HTTP server for Railway health checks | Railway health checks only gate web deployments; a worker needs the restart policy, and the job run log shows it is alive |

## Owner decisions

- League sync once, after `data_checked` — accepted (2026-09-27).
- Reference sync cadence 60 min / 15 min in the 48 h before a deadline — accepted.
- Deadline snapshots at T-30 and T-5 — accepted.
- Catch-up on start — accepted.
- New table (job run log) and its Alembic migration — accepted up front; agents apply it
  only to the local development and test databases.
- Migrations in production through the Railway pre-deploy command, on deploys the owner
  triggers — accepted.
- Auto-deploy from `main` with "Wait for CI" — accepted.
- Deployment deferred to about 2026-10-04 (Railway plan). The PR is merged without a
  deploy. The roadmap item "Worker …" is ticked when the PR is done; the item "Production
  deployment on Railway" is ticked by the owner after AC21 passes on the first deploy.
- Production database from `pgvector/pgvector:pg16` with a volume — accepted.
- New dependencies: none — accepted.

## Open questions (non-blocking)

- Whether the Railway pre-deploy command sees the private network address of the database
  service. If it does not, the runbook tells the owner to set `DATABASE_URL` to the
  public URL for that step. The owner checks this on the first deploy.
