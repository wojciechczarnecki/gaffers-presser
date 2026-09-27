# The Gaffer's Presser

[![CI](https://github.com/wojciechczarnecki/gaffers-presser/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/wojciechczarnecki/gaffers-presser/actions/workflows/ci.yml)

Team-news alerts and a post-gameweek press conference for Polish Fantasy Premier League
mini-leagues — built as a retrieval-augmented LLM system with evaluation and tracing.

> Status: early development. The roadmap below says what exists and what is planned.

## What it does

- **Team-news alerts.** Line-up leaks and injury news appear on X minutes before the FPL
  deadline, scattered across many accounts, while the official FPL flags lag behind. The
  system ingests posts from watched accounts, extracts who is out, doubtful, benched or
  starting, corroborates each leak against earlier posts, and e-mails an alert with cited
  sources — targeting 60 s from post to inbox.
- **Source credibility.** After each gameweek every leak is settled against who actually
  played, so each account gets a measured accuracy score.
- **League presser.** After each gameweek, a banter-filled summary of the mini-league in
  Polish FPL slang: the manager of the gameweek, the disasters and the season race.

## How it works

- **FPL collector** — players, fixtures, leagues, picks and post-gameweek ground truth from
  the public FPL API into PostgreSQL.
- **LangGraph flow** — extract (typed output) → link to FPL players → retrieve → corroborate
  → decide → notify.
- **Hybrid retrieval** — PostgreSQL full-text search and pgvector, merged with reciprocal
  rank fusion. Facts are queried with SQL, narrative is retrieved.
- **Evaluation and tracing** — every LLM step has an evaluation set; every call is traced in
  Langfuse.

Stack: Python 3.12, FastAPI, SQLModel, Alembic, PostgreSQL 16 + pgvector, LangGraph,
Langfuse, uv; hosted on Railway.

## Documentation

| Document | Contents |
|---|---|
| [docs/PROJECT.md](docs/PROJECT.md) | problem, requirements, architecture, risks |
| [docs/ROADMAP.md](docs/ROADMAP.md) | stages and their status |
| [docs/DECISIONS.md](docs/DECISIONS.md) and [docs/adr/](docs/adr/) | design decisions and their reasoning |
| [docs/CONVENTIONS.md](docs/CONVENTIONS.md) | code, tests, git |

## Development

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```

Tests that need PostgreSQL start their own throwaway container (testcontainers) — Docker is
required, but no running database is.

Running the FPL collector locally:

```bash
cp backend/.env.example backend/.env   # fill FPL_LEAGUE_IDS with your classic league IDs
docker compose up -d                   # PostgreSQL 16 with pgvector
cd backend && uv run alembic upgrade head

uv run python -m app.fpl --help                 # commands and options
uv run python -m app.fpl reference-sync
uv run python -m app.fpl deadline-snapshot --gameweek N
uv run python -m app.fpl league-sync --gameweek N
uv run python -m app.fpl results-sync --gameweek N
uv run python -m app.fpl backfill
```

Running the worker locally — schedules the jobs above from the gameweek calendar instead of
running them by hand:

```bash
uv run python -m app.worker run       # runs until stopped (Ctrl-C / SIGTERM)
uv run python -m app.worker status    # latest run of each job and the next planned actions
```

`FPL_LEAGUE_IDS` never holds a real league ID in the repository, logs or tests — see
`backend/.env.example`.

## Deployment

The owner performs every step below; agents never touch production.

1. Create a Railway project with a **PostgreSQL service** from the `pgvector/pgvector:pg16`
   image, with a **volume** mounted at `/var/lib/postgresql/data` and the environment
   variables `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` set (the same image as
   local Compose, so no data migration is needed later for pgvector).
2. Add a **worker service** from this GitHub repository. Railway reads `railway.json` from
   the repository root: it builds `backend/Dockerfile`, starts the worker
   (`python -m app.worker run`), runs `alembic upgrade head` as the pre-deploy command
   before every deploy, and restarts the worker on failure (`restartPolicyType: ALWAYS`).
3. Set the worker service's environment variables: `DATABASE_URL` (a reference to the
   database service — any of the `postgresql://`, `postgres://` or `postgresql+psycopg://`
   schemes works) and `FPL_LEAGUE_IDS`. If the pre-deploy command cannot reach the
   database's private network address, set `DATABASE_URL` to the database's public URL for
   that step instead.
4. Turn on **auto-deploy from `main`** with **"Wait for CI"** enabled, so merging a pull
   request (the owner's gate) is the deploy, and red CI blocks it.
5. Reading logs and running commands in production: `railway logs` (or the dashboard) shows
   `job started` / `job finished` lines and, on a restart, the catch-up sequence below.
   `railway ssh` opens a shell in the worker container to run `python -m app.worker status`
   or a CLI job (`python -m app.fpl …`) by hand — a CLI job waits for a running worker job
   to finish (they share the schedule's advisory locks) rather than running concurrently
   with it.
6. **First-deploy catch-up:** the worker runs a reference sync, then the results sync and
   league sync for every gameweek that is already `finished` and `data_checked`, in
   gameweek order, then follows the normal hourly/15-minute reference schedule. A
   `deadline snapshot missed` log line for the latest already-passed gameweek is expected —
   no snapshot was taken for it before this deploy existed.

The project is built with a spec-driven agentic workflow
([agentic-pipeline](https://github.com/wojciechczarnecki/agentic-pipeline)): every feature
goes from an approved spec through a reviewed plan and implementation to a pull request.

## License

Copyright © 2026 Wojciech Czarnecki. All rights reserved. The source is public for review as
a portfolio project; no licence to use, copy or distribute it is granted.

*Not affiliated with the Premier League or Fantasy Premier League.*
