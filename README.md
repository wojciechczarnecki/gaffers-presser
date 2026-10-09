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
uv run python -m app.tweets members   # fetch the List's members now and store the snapshot
```

`backend/measurements/` is gitignored; a source with missing credentials is skipped with a
message and the rest are still measured. Each record is appended to the output file as it is
measured, so Ctrl-C stops the run early and still prints the summary of what was collected.

Tweet extraction — a third loop inside the same worker turns every stored post into typed
events (which player, out / doubt / benched / confirmed starter, how sure the author is),
linked to FPL player IDs. OpenRouter is the only LLM provider: extraction is disabled unless
`OPENROUTER_API_KEY` is set, and the worker then runs the FPL jobs and the tweet ingest exactly as
above. Configuration (`backend/.env.example` has all of it): `OPENROUTER_API_KEY`, the optional
`LLM_MODEL` (an OpenRouter model ID) and `LLM_FALLBACK_MODEL` (empty means the defaults from
[ADR 0006](docs/adr/0006-default-extraction-model-openrouter.md): `openai/gpt-6-luna` with the
fallback `google/gemini-3.1-flash-lite`; a model needs a row in `backend/app/llm/model_settings.toml` and `prices.toml`),
the optional Langfuse Cloud keys `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`
(without them extraction runs untraced) and `USD_PLN_RATE` (only `evaluate` reads it).
`uv run python -m app.worker status` then also shows the model, the fallback, the posts waiting,
the failed posts and the latest extraction.

```bash
uv run python -m app.extraction --help
uv run python -m app.extraction reextract --x-id 123                 # one post
uv run python -m app.extraction reextract --since 2026-09-25T00:00:00Z --until 2026-09-26T00:00:00Z
uv run python -m app.extraction reextract --failed --model google/gemini-3.1-flash-lite
```

The evaluation set lives in `backend/evals/extraction/v1/` (`cases.jsonl` plus a snapshot of
the players it refers to). Its labels start as a model's candidates with `"reviewed": false`;
review each case (fix the expected events, set `"reviewed": true`) before evaluating —
`evaluate` refuses unreviewed cases. `review` walks the unreviewed cases in file order (no
database or LLM key needed): it shows the post and its expected events with each `fpl_id`
resolved to the player and club from the snapshot, then takes one key — `a` accepts
(`reviewed: true`), `e` edits the case as JSON in `$EDITOR` (validated before it is saved),
`f` finds a player's `fpl_id` by name and optional club, `s` skips, `q` quits. Every change is
saved at once, so a rerun resumes at the first unreviewed case; `--split dev|test` narrows the
run and `--id <case id>` reopens one case, reviewed or not. The commands, none of them run by
`pytest`:

```bash
uv run python -m app.extraction snapshot-players --output evals/extraction/v1/players-2026-27.json
uv run python -m app.extraction prelabel --output evals/extraction/v1/cases.jsonl \
  --model google/gemini-3.1-flash-lite       # appends candidates for posts not in the set yet
uv run python -m app.extraction review --split test   # interactive; --id <case id> for one case
uv run python -m app.extraction spend             # total cost of every run file, dev included
uv run python -m app.extraction compare-labels     # reviewed labels against the pre-labels (git rev dc02d98)
uv run python -m app.extraction evaluate --split dev --model google/gemini-3.1-flash-lite
uv run python -m app.extraction evaluate --split test --model openai/gpt-6-luna \
  --run-name gpt-6-luna-test-1               # needs USD_PLN_RATE; tune the prompt on dev only
```

`evaluate` writes `backend/evals/extraction/results/<run-name>.json` for the test split and
`backend/evals/extraction/results/dev/<run-name>.json` (gitignored) for dev runs. A file holds
precision, recall, F1, linking accuracy, false-alarm rate, the certainty confusion table, latency,
tokens (with reasoning tokens), cost, the serving hosts and the projected monthly cost in PLN, and
the run is traced in Langfuse under the run name. Prices for the cost figures come from
`backend/app/llm/prices.toml`; `uv run python -m app.extraction spend` sums the cost of
every run file under `results/`, dev runs included.

Corroboration — for a player and a moment, `python -m app.corroboration <player>` takes the
newest extracted claim since the latest deadline as the anchor, labels every other post about
the player as supporting, contradicting or related, counts the independent accounts (a repost
counts as its original author), flags a reversal and grades the anchor `high`, `medium` or
`low` with its reasons. Posts that only retrieval finds are labelled by an LLM judge, which needs
`OPENROUTER_API_KEY`; without it the result uses the extracted claims only and says so. Times
are `Europe/Warsaw`; `--at` replays a past moment and `--new-since` marks what is new to the
reader. Each run is one Langfuse trace when the Langfuse keys are set. The judge has its own
evaluation set in `backend/evals/corroboration/v1/`, pre-labelled by a model and reviewed by
the owner (`review` needs no database or key); none of these commands is run by `pytest`:

```bash
uv run python -m app.corroboration Isak --at 2026-09-29T18:00       # a name or an FPL ID
uv run python -m app.corroboration Isak --since 2026-09-25T00:00 --new-since 2026-09-29T12:00
uv run python -m app.corroboration.evaluation build-cases           # set v1 from the stored posts
uv run python -m app.corroboration.evaluation review --split test   # accept or change each label
uv run python -m app.corroboration.evaluation evaluate --split test --model openai/gpt-6-luna
```

`evaluate` reports accuracy, per-label precision and recall and the false-support rate, counts
reviewed cases only unless `--include-unreviewed` is given, and writes
`backend/evals/corroboration/results/<split>-<model>.json`. Without `--model` it evaluates the
model the judge runs on: `LLM_MODEL` when set, the default model otherwise.

Alerts — before each deadline the worker e-mails team news for the players that matter to the
league (league-owned, widely owned or trending): a digest at 20:00 Warsaw time the day before, news at T-60 and a
breaking e-mail per new post until the deadline, each with graded, linked sources; they run when delivery,
tweet ingest and extraction are enabled. The alert text is a deterministic Polish template.
`python -m app.alerts` shows the state, measures post → inbox latency and previews an alert at
any past moment; none of these is run by `pytest`:

```bash
uv run python -m app.alerts status                              # next slot, last alert, failures
uv run python -m app.alerts latency --gameweek 6                # or --rehearsal
uv run python -m app.alerts preview --at 2026-10-04T16:00 --kind digest
```

`ALERT_REHEARSAL_DEADLINE` (a Warsaw time) runs the whole cycle for a made-up deadline with real
e-mails for testing; remove it after use (see `docs/DEPLOYMENT.md`, step 12).

League presser — after each gameweek the worker sends the owner one press conference per
league by e-mail, in Polish FPL slang: the manager and the flop of the gameweek, the captains,
the bench, transfers and chips, and the table. The facts are computed in SQL, an LLM only writes
the text, and the e-mail has a "send to WhatsApp" button. It runs when delivery is enabled and
`OPENROUTER_API_KEY` is set. Variables (`backend/.env.example`): `PRESSER_ENABLED` (default true),
`PRESSER_MODEL` (default `openai/gpt-6-luna`) and `PRESSER_NICKNAMES` (a JSON object of FPL entry
ID to nickname, kept out of the repository). `uv run python -m app.worker status` ends with a
`Presser:` line. The CLI works by hand, also for an older gameweek and with `PRESSER_ENABLED=false`
(which switches off only the automatic presser), and the evaluation tooling
measures a model's faithfulness to the facts and the owner's rating of its style (the comparison
run is BACKLOG #32); none of it is run by `pytest`:

```bash
uv run python -m app.presser facts --league ID --gameweek N     # the fact sheet as JSON
uv run python -m app.presser preview --league ID --gameweek N   # generate and print, send nothing
uv run python -m app.presser send --league ID --gameweek N      # generate and send by hand
uv run python -m app.presser status                             # enabled or why not, latest presser

uv run python -m app.presser.evaluation build-cases --gameweeks 1-5   # real cases, pseudonymised
uv run python -m app.presser.evaluation evaluate --split test --model openai/gpt-6-luna  # --force overwrites a run
uv run python -m app.presser.evaluation review --run PATH       # rate the style 1-5
uv run python -m app.presser.evaluation judge-review --run PATH # check the judge's claim labels
uv run python -m app.presser.evaluation summary                 # the pass rule over the test runs
```

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
