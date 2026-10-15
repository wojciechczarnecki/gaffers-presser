# Code and documentation audit, October 2026

An audit of `backend/app`, `backend/tests` and the documents (README, `docs/`, CLAUDE.md) against
the state of `main` at `aec933c`, run in one Claude Code session on 2026-10-13 and 2026-10-15.
Every finding below was decided by the owner on 2026-10-15; this report is the hand-off that lets
a fresh session carry out any item on its own.

## How to use this report

Each item has a stable ID (A = first pass, N = second pass, D = maintainability), a BACKLOG row
and, for the work before spec 016, an **accepted mini-plan**. A session that picks up an item
reads CLAUDE.md, this report's section and the files it names, shows the mini-plan once
(CLAUDE.md asks for it before editing on the fast path) and then follows the fast path. Start a
session with one sentence:

> Fast path: BACKLOG #38 (N2), details and the accepted mini-plan in
> `docs/reports/code-audit-2026-10.md`, section N2.

For specs 016 and 017, `/pipeline:idea` reads the BACKLOG rows the roadmap item names and the
sections of this report they link to.

## Scope and method

- Read in full: `core`, `db`, `fpl`, `worker`, `tweets`, `extraction`, `retrieval`,
  `corroboration`, `alerts`, `delivery`, `llm`, `content`, the presser service, store, writer,
  fact-sheet build, load and checks, the four evaluation packages, migrations' `env.py`,
  `Dockerfile`, CI, the git hook and `scripts/session_judge.sh`.
- Read at the level of structure only: the presser's fact arithmetic (`facts/moments.py`,
  `stats.py`, `gameweek.py`, `chips.py`, `pitch.py`, about 1.5k lines; covered by about 3k lines
  of tests and by `check_fact_sheet` at run time) and the CLI modules.
- Verification: `ruff check`, `ruff format --check` and `pytest -q` green, 1958 passed; the
  slowest-test profile (`--durations=20`).
- Read-only SQL on the local Compose database (production while the system runs locally): the
  full-text check in A2.
- Tools: an import graph of `app/` (A-level modules), a search for public functions with no
  caller in `app/`, a docstring scan with `ast`.

## Summary

| ID | Area | Finding | Value | Cost | Where | BACKLOG |
|---|---|---|---|---|---|---|
| N2 | worker, presser | the presser can be sent without the gameweek's player results and is never redone | high | S | PR 1 | #38 |
| N1 | extraction | a failed extraction is never retried by the loop; a catch-up backlog delays fresh leaks | high | S–M | PR 2 | #39 |
| D1, D5, N3, N6 | core | four import cycles through `worker`, `CollectorError` naming, four `current_season`, dead code, no boundary guard | medium | S–M | PR 4 | #40 |
| A9, D4 | tracing | Langfuse free tier exceeded (55,134 of 50,000 events); three tracing paths | high | S–M | PR 5 | #41 |
| D6 | tests | the suite takes 6 minutes with no hotspot | medium | S (trial) | PR 6 | #42 |
| A1 | retrieval evaluation | set v1 does not measure the query production sends | high | M | spec 016 | #43 |
| A2 | retrieval | full-text misses players whose `web_name` carries an initial | high | S–M | spec 016 | #44 |
| A3 | retrieval evaluation | pooling bias; the pool cannot be extended | high | M | spec 016 | #45 |
| A4, D2, D3 | evaluations | result files are not self-describing, two CLIs overwrite silently, evaluation CLIs are inconsistent | medium | S + M | specs 016, 017 | #46 |
| A5 | corroboration evaluation | the judge set v1 has no reviewed case and no pass threshold | high | S + owner M | spec 017 | #47 |
| N5 | evaluation | `session_judge.sh` hard-codes the presser schema | low | S | spec 017 | #48 |
| A7 | architecture | option B (corroboration as a graph) deferred | — | M | later | #49 |
| A10, N4 | style | 33 docstrings, one stale comment | low | S | opportunistic | #50 |
| A6, A7, A8, N3 | documents | false or stale claims in README, PROJECT, CLAUDE.md, ROADMAP, DECISIONS | high | S | this PR | — |

## Execution plan

| Order | PR | Branch | Items | Depends on |
|---|---|---|---|---|
| 1 | this PR | `docs/audit-2026-10` | A6, A7, A8, N3 (documents), this report, BACKLOG #38–#50, ROADMAP | — |
| 2 | PR 1 | `fix/league-sync-after-results` | N2 | this PR merged |
| 2 | PR 2 | `fix/extraction-retry-and-order` | N1 | this PR merged; parallel with PR 1 (different modules) |
| 3 | PR 4 | `refactor/core-boundaries` | D1, D5, N6 | PR 1 and PR 2 merged (it renames across 22 files) |
| 4 | PR 5 | `fix/tracing-volume` | A9, D4 | PR 4 merged; before 2026-10-29 if possible (the Langfuse month resets then) |
| 5 | PR 6 | `chore/parallel-tests` | D6 | PR 5 merged |
| 6 | spec 016 | `feat/016-…` | A1, A2, A3, A4/D2, D3 (retrieval) | PRs 1–6 |
| 7 | spec 017 | `feat/017-…` | A5, N5, D3 (extraction) | spec 016 |

PRs 1, 2, 4 and 5 are fast path (bugfix, refactor, configuration; no new table, endpoint or
dependency). PR 6 is not: it adds a development dependency, which the fast path excludes. The
owner approved that dependency on 2026-10-15; PR 6 takes the same steps (the mini-plan shown
once, full verification, `/code-review`, the owner's decisions) and names the dependency in the PR. Each PR removes its BACKLOG row when it merges; the last of them ticks the roadmap
item "Hardening and cleanup before spec 016".

## Before spec 016

### N2 — the presser waits for the gameweek's results (PR 1, BACKLOG #38)

- **Problem.** `_next_gameweek_jobs` (`backend/app/worker/schedule.py:131`) plans `results_sync`
  and `league_sync` of a finished, data-checked gameweek independently: when `results_sync`
  fails it is retried after 15 minutes, while `league_sync` runs at once. The presser hook runs
  after a successful `league_sync` (`backend/app/presser/service.py:206`); `load_results` and
  `load_match_results` (`backend/app/presser/facts/load.py:204`, `:351`) return empty maps when
  the gameweek has no `player_gameweek_result` rows, and `check_fact_sheet` checks internal
  consistency, not completeness. A presser is sent once and never redone
  (DECISIONS 2026-10-09).
- **Effect.** The captaincy, "⚽ Z boiska" and key-moment sections are built from no data in a
  presser that goes to the friends' group. FPL answers "The game is being updated" around
  `data_checked`, which is when `results_sync` runs. How often it happened is in
  `SELECT gameweek_fpl_id, started_at, error_class FROM job_run WHERE job = 'results_sync' AND outcome = 'failed'`.
- **Mini-plan (accepted).** `league_sync` of a gameweek is planned only once `results_sync` of
  that gameweek has succeeded (`state.latest_success`). This is the order DECISIONS 2026-09-27
  already states ("results sync then league sync"), so no new decision row.
- **Tests.** `tests/worker/test_schedule.py`: no `league_sync` while `results_sync` is missing or
  failed; `league_sync` planned once it succeeded; a catch-up over several gameweeks keeps the
  order. `test_due_actions_priority_snapshot_then_reference_then_gameweek_jobs` expects both jobs
  due at once and changes with the rule.
- **Documents.** Remove BACKLOG #38.

### N1 — extraction retries failed posts and serves fresh posts first (PR 2, BACKLOG #39)

- **Problem.** `next_pending` (`backend/app/extraction/store.py:128`) takes only posts with no
  extraction row, oldest first. An extraction makes 3 attempts with 2 s and 4 s back-off
  (`backend/app/core/retry.py:11-12`) and is then `failed` for good; only
  `python -m app.extraction reextract --failed` brings it back. Spec 004 set both rules before
  alerts existed; the embedding loop got retries later (DECISIONS 2026-09-29: never-attempted
  posts first, up to 15 attempts).
- **Effect.** A leak posted during a minute-long OpenRouter outage before a deadline never
  reaches a news or breaking alert. After downtime the ingest catches up to 50 pages (about
  1000 posts) and fresh leaks wait behind the backlog; since the system runs on the owner's
  computer (DECISIONS 2026-10-13), sleep or shutdown means exactly such a catch-up.
- **Mini-plan (accepted).** Never-attempted posts first, newest first. A post whose latest
  extraction failed comes back after 10 minutes, up to 5 attempts in total, counted from its
  stored extraction rows (no migration). The re-extraction CLI is unchanged.
- **Tests.** `tests/extraction/test_store.py` and `test_loop.py`: newest first among
  never-attempted posts; a failed post is skipped within 10 minutes and taken after; no sixth
  attempt; a successful retry makes the post current.
- **Documents.** A DECISIONS row (supersedes spec 004's "oldest first, failed after the last
  attempt"); remove BACKLOG #39.

### D1, D5, N3, N6 — module boundaries and dead code (PR 4, BACKLOG #40)

- **Problem.**
  - The feature modules form one direction along the data flow
    (`tweets → extraction → retrieval → corroboration → alerts`; the presser uses only `fpl`,
    `delivery` and `llm`), with no cycle between them. Four cycles all run through `worker`,
    which as the composition root imports everything: `Shutdown` lives in
    `backend/app/worker/jobs.py:22` and is imported by 7 files (every loop, alerts, extraction,
    retrieval); alerts read the gameweek calendar through `worker.store.load_state` and
    `worker.schedule.GameweekState`.
  - `CollectorError` (`backend/app/core/errors.py:1`), named in Stage 0, is the base exception of
    every module (search, evaluations, presser): 74 uses in 22 files.
  - The current season is read in four places: `alerts/service.py:72`, `presser/store.py:14`,
    `worker/store.py:9` and `extraction/linking.py:84` (`max(Player.season)`).
  - Dead code: `backend/app/extraction/evaluation/selection.py` (91 lines, called only by its
    tests; the spec 005 selection was made once), `current_extraction`
    (`extraction/store.py:174`, no caller in `app/`, but used by `tests/extraction/test_store.py`
    and `test_service.py`) and `write_history` (`presser/evaluation/cases.py:115`, used only by
    tests).
- **Decision.** No new abstraction between the feature modules: each consumer calls the public
  functions of the module before it (`current_extractions`, `search`, `corroborate`), and an
  interface with one implementation each would only add indirection. `extraction/linking.py`
  stays where it is although corroboration uses it (one consumer, stable).
- **Mini-plan (accepted).**
  - `Shutdown` moves to `app/core`; the gameweek calendar (`GameweekState`, loading the season
    and its gameweeks) and one `current_season` move to `app/fpl`; `worker` keeps the job plan.
  - `CollectorError` is renamed `AppError`.
  - A boundary test (`backend/tests/test_module_boundaries.py`, `ast` only, no dependency) holds
    the allowed edges between top-level modules and fails on a new edge or a cycle;
    `worker/cli.py` may import every module.
  - The dead code above is deleted (approved by the owner): `selection.py` with its tests and
    `write_history` with its tests; the tests that read one post through `current_extraction`
    switch to `current_extractions(session, x_ids=[x_id])` first, so their coverage stays.
- **Documents.** A DECISIONS row (the boundary rule, where `Shutdown` and the calendar live);
  remove BACKLOG #40.

### A9, D4 — Langfuse volume and one tracing path (PR 5, BACKLOG #41)

- **Problem.** On 2026-10-15 Langfuse Cloud reported 55,134 events of the free tier's 50,000
  for the month that resets on 2026-10-29; ingestion is then suspended. An event is every
  trace, every observation inside it and every score. Events per operation, from the code:
  - extraction goes through `langfuse.langchain.CallbackHandler`
    (`backend/app/llm/tracing.py:34`), which records every internal LangChain step (the graph,
    both nodes, the structured-output chain, the model call, a disambiguation call): around ten
    events per post, and the same for `reextract` and for every extraction evaluation run;
  - every embedding attempt is an observation, failed attempts too, and every retrieval
    evaluation run re-embeds the whole frozen corpus (190 posts) because the evaluation schema is
    recreated on each run (`backend/app/retrieval/evaluation/schema.py:25`,
    `runner.py:136`), plus three searches per query;
  - corroboration in an alert slot is about 13 events per player (span, query embedding,
    search, up to 10 judge calls); the GW6 digest held 47 players;
  - the presser is 2–3 events; the presser and corroboration evaluations are not traced.
  The exact split is in Langfuse (Settings → Usage, or traces by name). Tracing failures are
  logged and dropped (`llm/tracing.py:116`, `retrieval/tracing.py:79`), so a suspended
  ingestion loses traces, not alerts.
- **Also.** Three tracing paths exist (the LangChain callback, `LangfuseGenerationTracer`,
  `RetrievalTracer`); the callback path is the only one whose cost does not come from
  `prices.toml` (DECISIONS 2026-09-30 chose the own tracer for the judge for that reason), so
  extraction traces likely carry no cost.
- **Decision.** Reduce the volume (option b). Rejected: waiting for 2026-10-29 (deadlines until
  then untraced, and the next month runs out the same way); the paid plan at $29 a month
  (several times the whole 20 PLN monthly external-API budget); a self-hosted Langfuse locally
  (ClickHouse, Redis and object storage in Compose, several GB of memory; new infrastructure).
- **Mini-plan (accepted).**
  - Extraction moves from the callback to the own `GenerationTracer`: one span per post and one
    generation per LLM call, with the cost from `prices.toml`. If nothing else imports
    `langfuse.langchain`, the direct `langchain` dependency goes too (it was added only for the
    callback, spec 004 plan).
  - The retrieval evaluation indexes its corpus untraced; query embeddings and searches stay
    traced.
  - The embedding loop traces failed embeddings only; successful ones are in `post_embedding`
    (`python -m app.retrieval status`).
- **Tests.** The extraction tracing tests move to the generation tracer (a fake tracer records
  one span and one generation per call); the indexing tests assert that a success is untraced
  and a failure traced; the retrieval evaluation tests assert an untraced corpus.
- **Documents.** A DECISIONS row (one tracing path through the own tracers); remove BACKLOG #41. After the merge the owner checks the event count per post in
  Langfuse.

### D6 — test suite time (PR 6, BACKLOG #42)

- **Problem.** The suite takes about 6 minutes (`327–362 s`); the 20 slowest tests take about
  70 s in total and the rest average about 0.15 s, so there is no single hotspot. The pipeline
  runs the full verification after every implementation step; specs 016 and 017 have about 15
  steps together.
- **Mini-plan (accepted).** A local trial first, without editing the project:
  `uv run --with pytest-xdist pytest -q -n auto` (each worker starts its own PostgreSQL
  container through the session fixture). Adopt only if it is green three runs in a row and under
  about 2.5 minutes: pin `pytest-xdist` in the `dev` group and change the verification command in
  CLAUDE.md, `docs/CONVENTIONS.md`, `.claude/workflow.json`, the README and CI. Otherwise record
  the measured time in a P3 BACKLOG row and drop the change. Watch BACKLOG #10 and #22 (timing
  tests). Remove BACKLOG #42 and tick the roadmap item "Hardening and cleanup before spec 016".

## For spec 016

### A1 — the retrieval evaluation does not measure the production query (BACKLOG #43)

- **Problem.** Corroboration, and through it every alert, searches with the player's `web_name`
  alone, in hybrid mode, filtered to the alert window and to source posts
  (`backend/app/corroboration/sources.py:142-153`). It takes the top 20
  (`SEARCH_LIMIT`, `:24`), drops the posts extraction already claimed for the player (they are
  counted by the SQL table, `corroboration/service.py:176-186`) and gives the judge at most 10
  (`MAX_JUDGED`, `:25`). Retrieval's job is therefore to surface the posts extraction missed.
  Set v1 has no bare-name query and no time filter: its event queries are templates
  (`backend/app/content/retrieval_query_templates.toml`, e.g. "Isak injury doubt") and many
  LLM-written queries are off the domain ("FPL YouTube content drought", "Ronaldo focused on
  1000 goals", "Manchester City chairman statement"); relevance means "matches the query".
- **Effect.** The mode choice, the embedding-model ADR and the reranking decision (BACKLOG #16)
  would rest on another task than the one the judge reads. For a player with 20 or more claimed
  posts in the window the whole top 20 can be dropped, so the judge sees nothing when a missed
  post matters most.
- **For the spec.** A production-shaped slice: `web_name` queries over a window ending at a
  deadline, relevance "a post about this player's availability that extraction missed", measured
  at the judge's cut (top 10 after the claimed posts are dropped); a decision on the search depth
  before the cut.

### A2 — full-text misses players with an initial in `web_name` (BACKLOG #44)

- **Problem.** `to_tsvector('english_unaccent', 'B.Fernandes')` is one lexeme `'b.fernandes'`
  (PostgreSQL's host token), and the query becomes the prefix `'b.fernandes':*`, which never
  matches "Bruno Fernandes" or "Fernandes" (`backend/app/retrieval/search.py:79`). On the
  local Compose database 22 posts contain "fernandes" and the full-text filter matches 2; for
  "timber" it is 6 and 0. 36 of the 667 players in `evals/extraction/v1/players-2026-27.json`
  have such a `web_name` (`B.Fernandes`, `J.Timber`, `G.Jesus`, `N.Jackson`, …).
- **Effect.** For these players hybrid search is vector-only, on the embedding of a bare
  "B.Fernandes"; corroboration loses supporting and contradicting posts for one of the most
  owned players.
- **For the spec.** Build the corroboration query from the first and second name or the aliases
  in `backend/app/extraction/aliases.toml`; the A1 slice holds such a player. Fix it in spec 016,
  not before, so the baseline is measured first.

### A3 — pooling bias (BACKLOG #45)

- **Problem.** Set v1 judges only the 660 posts that the three modes placed in their top 10
  (`POOL_DEPTH`, `backend/app/retrieval/evaluation/labelling.py:22`) with
  `openai/text-embedding-3-small` on the corpus frozen on 2026-09-30; an unjudged post counts as
  not relevant (`relevant_ids`, `runner.py:76`). `prelabel` skips every query that already has
  judgements (`labelling.py:115`), so the pool cannot be extended. All 660 judgements are
  unreviewed.
- **Effect.** A new embedding model's hits outside the old pool count as misses, so it looks
  worse than it is; posts added from the GW6 window are never judged for the old queries.
- **For the spec.** After each new model or corpus, pre-label only the new unjudged posts that
  reach any mode's top 10 (the owner reviews those); report per run how many of the top 10 were
  unjudged, so a reader sees whether a comparison holds. Owner work: the 660 judgements plus the
  new ones.

### A4, D2, D3 — evaluation results and CLIs (BACKLOG #46)

- **Problem.**
  - No result file of the four evaluations records the set version (extraction, corroboration,
    retrieval, presser); the presser files also lack the reasoning effort (the `model` field of
    two runs was edited by hand to "openai/gpt-6-luna (reasoning high)"), and `summary` and
    `choose` (`backend/app/presser/evaluation/summary.py:67`, `:76`) mix every `test-*` file
    whatever its prompt or set version.
  - `python -m app.corroboration.evaluation evaluate` (`cli.py:273`) and
    `python -m app.retrieval evaluate` overwrite their result without `--force`; the corroboration
    default path is `test-openai-gpt-6-luna.json`, the only v1 result, which holds a hand-written
    note.
  - The atomic write is copied seven times across the evaluation packages.
  - The presser and corroboration evaluations have their own entry point
    (`python -m app.<module>.evaluation`); extraction and retrieval keep theirs in the main CLI,
    the two largest files (`extraction/cli.py`, 619 lines; `retrieval/cli.py`, 609 lines), and
    the two that grow in Stage 2.
- **Effect.** A v2 rerun in spec 017 with the default model destroys the v1 baseline, and a v1
  result cannot be told from a v2 one by its file.
- **For the specs.** One small shared module for the run header (set version, model, prompt
  version, reasoning effort, judge, date, commit), the atomic write and the overwrite guard,
  used by all four evaluations — not a shared evaluation framework (the metrics stay per module,
  DECISIONS 2026-09-29). Spec 016 adds it and moves the retrieval evaluation to
  `python -m app.retrieval.evaluation`; spec 017 moves the extraction evaluation to
  `python -m app.extraction.evaluation`.

## For spec 017

### A5 — the corroboration judge set has no reviewed case (BACKLOG #47)

- **Problem.** `backend/evals/corroboration/v1/cases.jsonl` holds 51 cases, none reviewed. The
  only result (`evals/corroboration/results/test-openai-gpt-6-luna.json`) ran with
  `include_unreviewed: true`: accuracy 0.78 and a false-support rate of 12% against the labels of
  `anthropic/claude-haiku-4.5`, not the owner's. No pass threshold exists for the judge, while
  extraction and the presser have one (DECISIONS 2026-09-28, 2026-10-09). The README said the set
  was reviewed by the owner (corrected in this PR).
- **For the spec.** The owner reviews set v1 before the `contradicts` cases are added (BACKLOG
  #12); the spec sets a pass threshold (accuracy and false-support rate) before the run.

### N5 — `session_judge.sh` serves only the presser (BACKLOG #48)

- **Problem.** The script's reply format and its check are the presser verdict's claims
  (`scripts/session_judge.sh:20`, `:37`), while `docs/EVALUATION.md` suggests the session judge
  for the other evaluations.
- **For the spec.** If spec 017 judges in the session, the script takes the verdict schema as a
  parameter.

## Decisions taken on 2026-10-15

### Orchestration stays in Python (A7; option B in BACKLOG #49)

Recorded in [ADR 0008](../adr/0008-orchestration-plain-python.md) and DECISIONS 2026-10-15.

- **Facts.** Extraction is a two-node LangGraph graph (`extraction/flow.py:186-190`); the judge
  and the writer are one-node graphs; retrieval, corroboration and alerts are plain Python, and
  their recovery comes from the database (the alert log, the delivery idempotency key), not from
  a checkpointer. README, PROJECT and a consequence of ADR 0001 described a graph pipeline;
  this PR corrects README and PROJECT, and [ADR 0008](../adr/0008-orchestration-plain-python.md)
  supersedes that consequence.
- **Options.** A: keep Python, describe it honestly, keep the graph for the Stage 4 Q&A agent
  (routing between SQL and retrieval in a tool loop, and the PydanticAI comparison of BACKLOG
  #1). B: corroboration as a graph (retrieve → judge fan-out with `Send` → count → grade).
  C: the whole alert pipeline as a graph with a checkpointer.
- **Decision: A.** Waiting does not make B dearer: corroboration is already split into
  `sources`, `judge`, `rules` and `service`, so wrapping it in a graph later is mechanical, and
  safer once specs 016 and 017 give a baseline to check it against. B uses none of what makes
  LangGraph pay (branches, loops, checkpoints, interrupts) — its topology is fixed — and its one
  gain, parallel judge calls, a thread pool also gives (BACKLOG #33). Changing corroboration
  while 016 and 017 measure it would mix the refactor into the baselines. C duplicates the
  idempotency the database already gives.

### Langfuse volume — option b (A9)

See PR 5 above.

### No abstraction between the feature modules (N3)

See PR 4 above. `current_extractions` is extraction's public read API, which five modules use;
the DECISIONS row of 2026-09-30 that allowed only `app/retrieval/evaluation/` is superseded in
this PR.

## Found sound

- `core` and `db`: one retry helper with hooks, advisory locks, settings validation.
- `fpl`: client back-off and throttling, idempotent snapshots and syncs, one transaction per job.
- `worker`: a single worker through the session lock, catch-up on start, signal handling and
  thread shutdown.
- `tweets`: paging bounds, the membership guard against a truncated fetch, idempotent storage.
- `alerts`: slot resolution, idempotency through the alert log and the delivery key, deadline
  cut-offs, one window per deadline.
- `delivery`: the row lock around the provider call, the idempotency key.
- Privacy in logs: league ordinals, keys and error classes only; no manager or league names.
- Infrastructure: a non-root image, CI builds and smoke-tests it, a weekly dependency audit, the
  pre-push guard.

## Deliberately left

- Ten CLIs each with their own `Deps`, `get_deps` and `fail` (about ten lines each): explicit and
  easy to inject in tests; a shared base would couple them to save about 50 lines.
- Nine settings classes: each module reads only its own variables.
- 23 raw SQL statements: where the ORM does not reach (`DISTINCT ON`, `tsquery`, advisory locks).
- `_deps_from_settings` in `worker/cli.py` (108 lines): the composition root, long but linear.
- `extraction/linking.py` used by corroboration (see N3).
- 33 docstrings in 17 files against CONVENTIONS ("No docstrings") and a stale comment in
  `backend/app/alerts/config.py:55` (the window counts from the deadline, not from now):
  fixed when the file is next changed (BACKLOG #50).

## Fixed in this PR

- README: the 60 s target is detection only (DECISIONS 2026-09-28); source credibility is planned
  (Stage 4), not built; LangGraph described as it is used; FastAPI is not in the stack yet; the
  system runs locally (DECISIONS 2026-10-13); no "roadmap below"; no stale "BACKLOG #32"; the
  corroboration set is pre-labelled and awaiting review; a retrieval section; links to the
  reports and `docs/EVALUATION.md`.
- PROJECT: the architecture diagram as the code runs; environments (local, Railway postponed);
  ADR 0007 next to ADR 0006; `PRESSER_MODEL` in swappability.
- CLAUDE.md: FastAPI only once an HTTP API is needed; local running; the development commands;
  `docs/reports/` in the document map.
- ROADMAP: the stale production-deployment sentence; the "Hardening and cleanup before spec 016"
  item; BACKLOG numbers in the 016 and 017 items.
- DECISIONS: four rows (orchestration, with ADR 0008; `current_extractions` as the public read
  API; the Compose database as production while the system runs locally; FastAPI only once an
  HTTP API is needed).
- ADR 0008 (orchestration in plain Python), and ADR 0001's status names it.
- CLAUDE.md: migrations only on a test container without asking; the Compose migration is the
  owner's operation.
- DEPLOYMENT: the stale "BACKLOG #32" points to the presser model comparison report.
