# Development

How to run every module of The Gaffer's Presser locally: the configuration, the command-line
tools and the evaluation commands. The [README](../README.md) says what the system does and how
it is built; [CONVENTIONS](CONVENTIONS.md) covers code style, tests and git.

## Verification

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto
```

Tests that need PostgreSQL start their own throwaway container (testcontainers) — Docker is
required, but no running database is.

## FPL collector and the worker

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

## Tweet ingest

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

## Extraction

Tweet extraction — a third loop inside the same worker turns every stored post into typed
events (which player, out / doubt / benched / confirmed starter / available, how sure the
author is),
linked to FPL player IDs. OpenRouter is the only LLM provider: extraction is disabled unless
`OPENROUTER_API_KEY` is set, and the worker then runs the FPL jobs and the tweet ingest exactly as
above. Configuration (`backend/.env.example` has all of it): `OPENROUTER_API_KEY`, the optional
`LLM_MODEL` (an OpenRouter model ID) and `LLM_FALLBACK_MODEL` (empty means the defaults from
[ADR 0006](adr/0006-default-extraction-model-openrouter.md): `openai/gpt-6-luna` with the
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
check makes one call per post and club for the linked line-up and absence events
(`confirmed_starter`, `benched`, `out`, `doubt`), gives it the club's previous and next Premier
League fixture, and stores whether the event is about the club's next Premier League match in
`extraction_event.for_next_pl_match` (true, false or null). Reading with
`current_extractions(fresh_lineups_only=True)` drops a false answer, beside the 60 h filter for
line-ups; a failed check stores null and keeps the event. `available` events are not checked.

The evaluation lives in its own module, `python -m app.extraction.evaluation`, and its current
set is `backend/evals/extraction/v3/` (`cases.jsonl`, `set.json`, and snapshots of the players and the
fixtures). The labels start as the Claude Code session judge's candidates with
`"reviewed": false` ([EVALUATION](EVALUATION.md), "The extraction procedure"); review each case (fix
the expected events, set `"reviewed": true`) before evaluating — `evaluate` refuses unreviewed
cases. `review` walks the unreviewed cases in file order (no database or LLM key needed): it
shows the post and its expected events with each `fpl_id` resolved to the player and club from
the snapshot, then takes one key — `a` accepts (`reviewed: true`), `e` edits the case as JSON
in `$EDITOR` (validated before it is saved), `f` finds a player's `fpl_id` by name and optional
club, `s` skips, `q` quits. Every change is saved at once, so a rerun resumes at the first
unreviewed case; `--split dev|test` narrows the run and `--id <case id>` reopens one case,
reviewed or not. The commands, none of them run by `pytest`:

```bash
uv run python -m app.extraction.evaluation snapshot-players --set evals/extraction/v3
uv run python -m app.extraction.evaluation snapshot-fixtures --set evals/extraction/v3
uv run python -m app.extraction.evaluation prelabel --set evals/extraction/v3 \
  --model google/gemini-3.1-flash-lite       # paid pre-labels; v2 and v3 used the session judge instead
uv run python -m app.extraction.evaluation export-judge-inputs --set evals/extraction/v3 --out $SCRATCH/judge
uv run python -m app.extraction.evaluation estimate --export $SCRATCH/judge
uv run python -m app.extraction.evaluation import-judge --export $SCRATCH/judge \
  --judge-model "claude-sonnet-5-5 (Claude Code session)"
uv run python -m app.extraction.evaluation review --set evals/extraction/v3 --split test
uv run python -m app.extraction.evaluation spend --results-dir evals/extraction/results/v3
uv run python -m app.extraction.evaluation compare-labels --set evals/extraction/v3 \
  --baseline evals/extraction/v2/cases.jsonl
uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v3 --split dev \
  --model openai/gpt-6-luna
uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v3 --split test \
  --model openai/gpt-6-luna --run-name test-v3-openai-gpt-6-luna   # needs USD_PLN_RATE; tune on dev only
```

`evaluate` runs the whole flow with the model under test and scores the events the read-time
filter would keep. It writes `backend/evals/extraction/results/<set>/<run-name>.json` for the
test split and `backend/evals/extraction/results/<set>/dev/<run-name>.json` (gitignored) for dev
runs (set v1 writes to `results/` itself). A file holds precision, recall, F1, linking accuracy,
false-alarm rate, `available` F1, the other-match false alarms for line-ups and absences, the
injuries and line-ups the match check dropped, the certainty confusion table, latency, tokens (with
reasoning tokens), cost, the serving hosts and the projected monthly cost in PLN, and the run is
traced in Langfuse under the run name. Prices for the cost figures come from
`backend/app/llm/prices.toml`; `spend` sums the cost of every run file under the results
directory, dev runs included.

## Retrieval

Retrieval — a fourth loop inside the worker embeds every stored post (`EMBEDDING_MODEL`, default
`openai/text-embedding-3-large` through OpenRouter; it needs `OPENROUTER_API_KEY`), and search
fuses PostgreSQL full-text and pgvector rankings with reciprocal rank fusion. The evaluation sets
live in `backend/evals/retrieval/`: v1 is a frozen corpus of public posts with about 40 queries
(the `free` slice); set v2 adds the `player` slice, one query per player with a claim in a
gameweek's alert window, searched the way corroboration searches and scored by the recall of the
relevant posts that extraction missed, at the judge's cut. A set is loaded into its own
`retrieval_eval` schema and never into the live table; the labels come from the Claude Code
session judge and the owner reviews the disagreements and a sample ([EVALUATION](EVALUATION.md), "The
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

## Corroboration

Corroboration — for a player and a moment, `python -m app.corroboration <player>` takes the
newest extracted claim since the latest deadline as the anchor, labels every other post about
the player as supporting, contradicting or related, counts the independent accounts (a repost
counts as its original author), flags a reversal and grades the anchor `high`, `medium` or
`low` with its reasons. Posts that only retrieval finds are labelled by an LLM judge, which needs
`OPENROUTER_API_KEY`; without it the result uses the extracted claims only and says so. Times
are `Europe/Warsaw`; `--at` replays a past moment and `--new-since` marks what is new to the
reader. Each run is one Langfuse trace when the Langfuse keys are set. The judge has its own
evaluation set v2 in `backend/evals/corroboration/v2/` (106 cases, reviewed one by one by the
owner; results in [the report](reports/corroboration-eval-v2.md), choice in
[ADR 0011](adr/0011-corroboration-judge-model.md)). `review` and `evaluate` need no database;
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

## Alerts

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
e-mails for testing; remove it after use (see [DEPLOYMENT](DEPLOYMENT.md), step 12).

## League presser

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
comparison: [report](reports/presser-model-comparison.md)); none of it is run by `pytest`:

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
