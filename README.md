# The Gaffer's Presser

[![CI](https://github.com/wojciechczarnecki/gaffers-presser/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/wojciechczarnecki/gaffers-presser/actions/workflows/ci.yml)

Team-news alerts and a post-gameweek press conference for Polish Fantasy Premier League
mini-leagues — built as a retrieval-augmented LLM system with evaluation and tracing.

> Status: in development. [docs/ROADMAP.md](docs/ROADMAP.md) says what exists and what is
> planned.

## What it does

- **Team-news alerts.** Line-up leaks and injury news appear on X minutes before the FPL
  deadline, scattered across many accounts, while the official FPL flags lag behind. The
  system ingests posts from watched accounts, extracts who is out, doubtful, benched or
  starting, corroborates each leak against the other posts about the player, and e-mails an
  alert with cited sources. The target is to fetch a leak within 60 s of it being posted
  (p95 detection latency, measured at 25.9 s); extraction and delivery come on top.
- **League presser.** After each gameweek, a banter-filled summary of the mini-league in
  Polish FPL slang: the manager of the gameweek, the disasters and the season race.
- **Planned: source credibility.** After each gameweek every leak is settled against who
  actually played, so each account gets a measured accuracy score (Stage 4).

## How it works

- **FPL collector** — players, fixtures, leagues, picks and post-gameweek ground truth from
  the public FPL API into PostgreSQL.
- **Extraction** — a LangGraph flow: an LLM extracts the players, the event type and the
  author's certainty as typed output, then a deterministic lookup links the names to FPL
  players (the LLM only chooses when the lookup is ambiguous).
- **Hybrid retrieval** — PostgreSQL full-text search and pgvector, merged with reciprocal
  rank fusion. Facts are queried with SQL, narrative is retrieved.
- **Corroboration and alerts** — plain Python over SQL and retrieval: the extracted claims
  about a player are labelled by fixed rules, the posts only retrieval finds by an LLM judge;
  alerts are built at deadline-driven slots and recovered after a crash through the database
  (the alert log and the delivery idempotency key).
- **Evaluation and tracing** — every LLM step that produces data has an evaluation set run
  on demand, with its results recorded; LLM calls are traced in Langfuse.

Stack: Python 3.12, SQLModel, Alembic, PostgreSQL 16 + pgvector, LangGraph, LangChain,
OpenRouter, Langfuse, uv; it runs locally on Docker Compose (a Railway deployment is prepared
and postponed).

## Retrieval results

Corroboration searches a player's news with an embedding model, a search mode, a query variant and
a depth, and these were measured, not guessed. Set v2 holds 2557 posts and 269 queries from the
GW6 and GW7 pre-deadline windows, labelled by an LLM judge and reviewed by the owner; the score is
the recall of the relevant posts that extraction missed, within the judge's cut of 10, on the 69
test queries that have one.

| model | best configuration | recall at the cut | recall within the depth | recall@10 |
|---|---|---|---|---|
| `openai/text-embedding-3-large` (the default) | hybrid, `names`, depth 20 | 0.933 | 0.941 | 0.874 |
| `openai/text-embedding-3-small` | full-text, `names`, depth 30 | 0.919 | 0.965 | 0.872 |
| `google/gemini-embedding-001` | full-text, `names`, depth 30 | 0.919 | 0.965 | 0.872 |
| the earlier default: `text-embedding-3-small`, hybrid, `web_name`, depth 20 | | 0.906 | 0.926 | 0.869 |

"Recall within the depth" is each row at its own depth; the reranking check reads the winner's
depth-50 row (0.998). Differences below about 0.05 on 69 queries are within noise; the full-text rows do not use the
embedding, so they are the same for every model. Reranking is not needed. The full tables, the
labelling, the misses and the limits are in the
[report](docs/reports/retrieval-eval-v2.md), and the choice in
[ADR 0009](docs/adr/0009-default-embedding-model.md).

## Extraction results

Set v2, test split (156 cases, 311 events), prompts `extraction@5`, `link_disambiguation@1` and
`match_check@1`, every step with the model under test. Details:
[report](docs/reports/extraction-eval-v2.md), decision:
[ADR 0010](docs/adr/0010-extraction-match-check-and-default-model.md).

| model | F1 | linking | false alarms | other-match false alarms | PLN / month | passes |
|---|---|---|---|---|---|---|
| `openai/gpt-6-luna` | 0.863 | 0.977 | 4.1% | 2 of 29 | 0.84 | yes |
| `qwen/qwen3.8-flash` | 0.869 | 0.970 | 3.1% | 2 of 29 | 1.37 | yes |
| `google/gemini-3.1-flash-lite` | 0.918 | 0.980 | 7.2% | 2 of 29 | 2.39 | no |
| `anthropic/claude-haiku-4.5` | 0.911 | 0.976 | 4.1% | 2 of 29 | 14.30 | no |
| `z-ai/glm-5.3-flash` | 0.902 | 0.979 | 6.5% | 3 of 28 | 2.21 | no |
| `z-ai/glm-5.3-flash` (repeat) | 0.923 | 0.980 | 5.4% | 2 of 29 | 2.11 | no |
| `deepseek/deepseek-v4-flash` | 0.493 | 0.948 | 5.2% | 2 of 29 | 1.35 | no |

The default is `openai/gpt-6-luna`, the cheapest model that passes every threshold (F1 ≥ 0.85,
linking ≥ 0.95, false alarms ≤ 5%, ≤ 5 PLN a month, no errored case); the fallback is
`google/gemini-3.1-flash-lite`.

## Documentation

| Document | Contents |
|---|---|
| [docs/PROJECT.md](docs/PROJECT.md) | problem, requirements, architecture, risks |
| [docs/ROADMAP.md](docs/ROADMAP.md) | stages and their status |
| [docs/DECISIONS.md](docs/DECISIONS.md) and [docs/adr/](docs/adr/) | design decisions and their reasoning |
| [docs/CONVENTIONS.md](docs/CONVENTIONS.md) | code, tests, git |
| [docs/EVALUATION.md](docs/EVALUATION.md) | running an LLM-judged evaluation with the Claude Code session as the judge |
| [docs/reports/](docs/reports/) | evaluation and measurement reports, the code audit |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | deployment runbook (Railway, postponed) |

## Development

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto
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
the failed posts (those the loop still retries apart from those it gave up on) and the latest
extraction.

```bash
uv run python -m app.extraction --help
uv run python -m app.extraction reextract --x-id 123                 # one post
uv run python -m app.extraction reextract --since 2026-09-25T00:00:00Z --until 2026-09-26T00:00:00Z
uv run python -m app.extraction reextract --failed --model google/gemini-3.1-flash-lite
```

The flow has three steps: extract the events, link the players, and a match check. The match
check makes one call per post and club for the linked line-up events (`confirmed_starter`,
`benched`), gives it the club's previous and next Premier League fixture, and stores whether
the line-up is for the club's next Premier League match in `extraction_event.for_next_pl_match`
(true, false or null). Reading with `current_extractions(fresh_lineups_only=True)` drops a false
answer, beside the 60 h filter; a failed check stores null and keeps the event.

The evaluation lives in its own module, `python -m app.extraction.evaluation`, and its set is
`backend/evals/extraction/v2/` (`cases.jsonl`, `set.json`, and snapshots of the players and the
fixtures). The labels start as the Claude Code session judge's candidates with
`"reviewed": false` (`docs/EVALUATION.md`, "The extraction procedure"); review each case (fix
the expected events, set `"reviewed": true`) before evaluating — `evaluate` refuses unreviewed
cases. `review` walks the unreviewed cases in file order (no database or LLM key needed): it
shows the post and its expected events with each `fpl_id` resolved to the player and club from
the snapshot, then takes one key — `a` accepts (`reviewed: true`), `e` edits the case as JSON
in `$EDITOR` (validated before it is saved), `f` finds a player's `fpl_id` by name and optional
club, `s` skips, `q` quits. Every change is saved at once, so a rerun resumes at the first
unreviewed case; `--split dev|test` narrows the run and `--id <case id>` reopens one case,
reviewed or not. The commands, none of them run by `pytest`:

```bash
uv run python -m app.extraction.evaluation snapshot-players --set evals/extraction/v2
uv run python -m app.extraction.evaluation snapshot-fixtures --set evals/extraction/v2
uv run python -m app.extraction.evaluation prelabel --set evals/extraction/v2 \
  --model google/gemini-3.1-flash-lite       # paid pre-labels; v2 used the session judge instead
uv run python -m app.extraction.evaluation export-judge-inputs --set evals/extraction/v2 --out $SCRATCH/judge
uv run python -m app.extraction.evaluation estimate --export $SCRATCH/judge
uv run python -m app.extraction.evaluation import-judge --export $SCRATCH/judge \
  --judge-model "claude-sonnet-5-5 (Claude Code session)"
uv run python -m app.extraction.evaluation review --set evals/extraction/v2 --split test
uv run python -m app.extraction.evaluation spend --results-dir evals/extraction/results/v2
uv run python -m app.extraction.evaluation compare-labels --set evals/extraction/v2 --revision f6ced46
uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v2 --split dev \
  --model google/gemini-3.1-flash-lite
uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v2 --split test \
  --model openai/gpt-6-luna --run-name test-v2-gpt-6-luna   # needs USD_PLN_RATE; tune on dev only
```

`evaluate` runs the whole flow with the model under test and scores the events the read-time
filter would keep. It writes `backend/evals/extraction/results/v2/<run-name>.json` for the test
split and `backend/evals/extraction/results/v2/dev/<run-name>.json` (gitignored) for dev runs. A
file holds precision, recall, F1, linking accuracy, false-alarm rate, the other-match false alarms
and the line-ups the match check dropped, the certainty confusion table, latency, tokens (with
reasoning tokens), cost, the serving hosts and the projected monthly cost in PLN, and the run is
traced in Langfuse under the run name. Prices for the cost figures come from
`backend/app/llm/prices.toml`; `spend` sums the cost of every run file under the results
directory, dev runs included.

Retrieval — a fourth loop inside the worker embeds every stored post (`EMBEDDING_MODEL`, default
`openai/text-embedding-3-large` through OpenRouter; it needs `OPENROUTER_API_KEY`), and search
fuses PostgreSQL full-text and pgvector rankings with reciprocal rank fusion. The evaluation sets
live in `backend/evals/retrieval/`: v1 is a frozen corpus of public posts with about 40 queries
(the `free` slice); set v2 adds the `player` slice, one query per player with a claim in a
gameweek's alert window, searched the way corroboration searches and scored by the recall of the
relevant posts that extraction missed, at the judge's cut. A set is loaded into its own
`retrieval_eval` schema and never into the live table; the labels come from the Claude Code
session judge and the owner reviews the disagreements and a sample (`docs/EVALUATION.md`, "The
retrieval procedure"). None of these commands is run by `pytest`:

```bash
uv run python -m app.retrieval index [--model MODEL]       # embed what is missing for a model
uv run python -m app.retrieval status                      # embedded, missing and failed posts
uv run python -m app.retrieval search "Isak injury" --mode hybrid   # or fulltext / vector
uv run python -m app.retrieval.evaluation build-player-slice --gameweek 6   # the player slice
uv run python -m app.retrieval.evaluation export-judge-inputs --out DIR --embedding-model MODEL
uv run python -m app.retrieval.evaluation import-judge --export DIR --judge-model NAME
uv run python -m app.retrieval.evaluation review --sample 30   # blind review of the labels
uv run python -m app.retrieval.evaluation evaluate --split test --reference-judge NAME
uv run python -m app.retrieval.evaluation compare RESULT_FILE...   # one table, same set and split
```

Corroboration — for a player and a moment, `python -m app.corroboration <player>` takes the
newest extracted claim since the latest deadline as the anchor, labels every other post about
the player as supporting, contradicting or related, counts the independent accounts (a repost
counts as its original author), flags a reversal and grades the anchor `high`, `medium` or
`low` with its reasons. Posts that only retrieval finds are labelled by an LLM judge, which needs
`OPENROUTER_API_KEY`; without it the result uses the extracted claims only and says so. Times
are `Europe/Warsaw`; `--at` replays a past moment and `--new-since` marks what is new to the
reader. Each run is one Langfuse trace when the Langfuse keys are set. The judge has its own
evaluation set v2 in `backend/evals/corroboration/v2/` (106 cases, reviewed one by one by the
owner; results in [the report](docs/reports/corroboration-eval-v2.md), choice in
[ADR 0011](docs/adr/0011-corroboration-judge-model.md)). `review` and `evaluate` need no database;
`build-scratch` and `build-candidates` run only against a throwaway database (`--database-url`;
they refuse port 5432 and the configured `DATABASE_URL`); none of these commands is run by `pytest`:

```bash
uv run python -m app.corroboration Isak --at 2026-09-29T18:00       # a name or an FPL ID
uv run python -m app.corroboration Isak --since 2026-09-25T00:00 --new-since 2026-09-29T12:00
uv run python -m app.corroboration.evaluation build-cases           # set v1 from the stored posts
uv run python -m app.corroboration.evaluation build-scratch --database-url URL      # v2: corpus, embeddings, extraction
uv run python -m app.corroboration.evaluation build-candidates --database-url URL --gameweek 6 --gameweek 7 --out FILE
uv run python -m app.corroboration.evaluation export-judge-inputs --candidates FILE --out DIR
uv run python -m app.corroboration.evaluation estimate --export DIR
uv run python -m app.corroboration.evaluation import-judge --export DIR --set evals/corroboration/v2 --judge-model NAME
uv run python -m app.corroboration.evaluation review                # keys 1-4, a accept, s skip, b back, q quit
uv run python -m app.corroboration.evaluation agreement             # the owner's labels against each pre-label
uv run python -m app.corroboration.evaluation evaluate --split test --model google/gemini-3.1-flash-lite
```

`evaluate` reports accuracy, per-label precision and recall, the false-support rate, the
production slice (`has_player_event: false`), a pass or fail per threshold and the cost a month,
counts reviewed cases only unless `--include-unreviewed` is given, and writes
`backend/evals/corroboration/results/v2/test-<model>.json`. Without `--model` it evaluates the
model the judge runs on: `LLM_JUDGE_MODEL` when set, the default judge model
(`google/gemini-3.1-flash-lite`, ADR 0011) otherwise. No candidate passed the threshold on set
v2; the owner chose the model closest to it over the rule's pick (`qwen/qwen3.8-flash`), and it
is not a model proven good enough.

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
`PRESSER_MODEL` (default `openai/gpt-6-sol`, ADR 0007) and `PRESSER_NICKNAMES` (a JSON object of FPL entry
ID to nickname, kept out of the repository). `uv run python -m app.worker status` ends with a
`Presser:` line. The CLI works by hand, also for an older gameweek and with `PRESSER_ENABLED=false`
(which switches off only the automatic presser), and the evaluation tooling
measures a model's faithfulness to the facts and the owner's rating of its style (the model
comparison: [report](docs/reports/presser-model-comparison.md)); none of it is run by `pytest`:

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

The worker and PostgreSQL run locally on Docker Compose. A Railway deployment is prepared —
the Docker image from `backend/Dockerfile`, `railway.json` at the repository root, both built
in CI — and postponed until the system has to run without the owner's computer; the
step-by-step runbook is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## License

Copyright © 2026 Wojciech Czarnecki. All rights reserved. The source is public for review as
a portfolio project; no licence to use, copy or distribute it is granted.

*Not affiliated with the Premier League or Fantasy Premier League.*
