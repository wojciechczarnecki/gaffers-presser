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
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | deployment runbook (Railway) |

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

Tweet ingest — a separate polling loop inside the same worker, watching one public X List
for team-news leaks. Disabled unless `TWEET_SOURCE` is set in `backend/.env`; the worker
otherwise runs the FPL jobs exactly as above. Configuration (`backend/.env.example` has all
seven): `TWEET_SOURCE` (`twscrape` | `twitterapi_io` | `x_api`), `X_LIST_ID`, and the
credentials of the chosen source — `TWSCRAPE_USERNAME` / `TWSCRAPE_COOKIES` /
`TWSCRAPE_ACCOUNTS_DB` for `twscrape`, `TWITTERAPI_IO_KEY` for `twitterapi_io`,
`X_API_BEARER_TOKEN` for `x_api`. `uv run python -m app.worker status` then also shows the
source, the last successful poll, the next poll and the current mode (window / sparse).

Measuring detection latency per source (posts a source publisher publishes on X → the
moment our system first fetches them), side by side, without a database:

```bash
uv run python -m app.tweets measure --interval-seconds 20 --duration-minutes 45 \
  --output measurements/latency.jsonl                 # polls every configured source
uv run python -m app.tweets summary measurements/latency.jsonl --markdown  # per-source table
```

`backend/measurements/` is gitignored; a source with missing credentials is skipped with a
message and the rest are still measured. Each record is appended to the output file as it is
measured, so Ctrl-C stops the run early and still prints the summary of what was collected.

Tweet extraction — a third loop inside the same worker turns every stored post into typed
events (which player, out / doubt / benched / confirmed starter, how sure the author is),
linked to FPL player IDs. Disabled unless `LLM_PROVIDER` is set; the worker then runs the FPL
jobs and the tweet ingest exactly as above. Configuration (`backend/.env.example` has all
ten): `LLM_PROVIDER` (`google` | `openai` | `anthropic` | `openrouter`), `LLM_MODEL`, the key
of the chosen provider (`GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENROUTER_API_KEY`), the optional Langfuse Cloud keys `LANGFUSE_PUBLIC_KEY` /
`LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` (without them extraction runs untraced) and
`USD_PLN_RATE` (only `evaluate` reads it). `uv run python -m app.worker status` then also shows
the model, the posts waiting, the failed posts and the latest extraction.

```bash
uv run python -m app.extraction --help
uv run python -m app.extraction reextract --x-id 123                 # one post
uv run python -m app.extraction reextract --since 2026-09-25T00:00:00Z --until 2026-09-26T00:00:00Z
uv run python -m app.extraction reextract --failed --provider openai --model <model>
```

The evaluation set lives in `backend/evals/extraction/v1/` (`cases.jsonl` plus a snapshot of
the players it refers to). Its labels start as a model's candidates with `"reviewed": false`;
review each case (fix the expected events, set `"reviewed": true`) before evaluating —
`evaluate` refuses unreviewed cases. The commands, none of them run by `pytest`:

```bash
uv run python -m app.extraction snapshot-players --output evals/extraction/v1/players-2026-27.json
uv run python -m app.extraction prelabel --output evals/extraction/v1/cases.jsonl \
  --provider openai --model <model>          # appends candidates for posts not in the set yet
uv run python -m app.extraction evaluate --split dev --provider openai --model <model>
uv run python -m app.extraction evaluate --split test --provider google --model <model> \
  --run-name gemini-test-1                   # needs USD_PLN_RATE; tune the prompt on dev only
```

`evaluate` writes `backend/evals/extraction/results/<run-name>.json` (precision, recall, F1,
linking accuracy, false-alarm rate, certainty confusion table, latency, tokens, cost and the
projected monthly cost in PLN) and traces the run in Langfuse under the run name. Prices for
the cost figures come from `backend/app/extraction/prices.toml`.

The project is built with a spec-driven agentic workflow
([agentic-pipeline](https://github.com/wojciechczarnecki/agentic-pipeline)): every feature
goes from an approved spec through a reviewed plan and implementation to a pull request.

## Deployment

The worker runs on Railway: the Docker image from `backend/Dockerfile`, with `railway.json`
at the repository root. The owner deploys by merging to `main`; the step-by-step runbook
is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## License

Copyright © 2026 Wojciech Czarnecki. All rights reserved. The source is public for review as
a portfolio project; no licence to use, copy or distribute it is granted.

*Not affiliated with the Premier League or Fantasy Premier League.*
