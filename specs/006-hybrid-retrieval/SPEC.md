---
status: spec-ready
stage_history:
  - "spec-draft — 2026-09-29"
  - "spec-ready — 2026-09-29"
---

# SPEC 006 — Hybrid retrieval over posts

## Goal

Find the posts relevant to a query — the text of a new leak, a player's status, later a
question in Polish — by combining full-text search and vector similarity with reciprocal
rank fusion (RRF) on PostgreSQL. This is the retrieval half of Stage 2's RAG, and later
specs use it for corroboration (FR-2.2), cited alerts (FR-2.3) and Q&A (FR-4.2). It works when
every stored post can be found in three modes (full-text, vector, hybrid) within seconds of
being stored, and when an evaluation runner reports recall@k and MRR for each mode on a
reviewed evaluation set v1.

## Context

- Stage 1 is done: posts from the watched X List are stored in `tweet` (`app/tweets/`,
  spec 003), and every post goes through the LangGraph extraction flow, which links players
  to FPL IDs (`app/extraction/`, specs 004/005). The development database holds 210 posts
  from 26 accounts (42 reposts) but only 25 extracted events. Much of it is noise: promotions,
  one-word replies.
- Retrieval complements the SQL linking and does not replace it. SQL over
  `extraction_event.player_fpl_id` already finds the posts extraction linked to a player.
  Retrieval also finds the posts extraction did not link: nicknames, context replies, posts
  with no event, paraphrases, and Polish queries over English posts.
- The `vector` extension is not yet enabled in any database. It is available in the
  `pgvector/pgvector:pg16` image used by Compose, the tests (`tests/conftest.py`) and
  production (DEPLOYMENT).
- The `openrouter` SDK (0.11.46, a transitive dependency of `langchain-openrouter`) exposes
  `embeddings.generate`, so embeddings reach OpenRouter with the existing
  `OPENROUTER_API_KEY`. OpenRouter lists `openai/text-embedding-3-small` at $0.02 per million
  input tokens (checked 2026-09-29).
- Code that retrieval needs but that is not specific to extraction lives in `app/extraction`
  today:
  - the shared parts of `ExtractionSettings` (`OPENROUTER_API_KEY`, `LANGFUSE_*`,
    `USD_PLN_RATE`) in `app/core/settings.py`;
  - `TracingConfig`, `make_handler` and `flush` in `app/extraction/tracing.py`;
  - `app/extraction/pricing.py` with `prices.toml`;
  - `Clock` in `app/tweets/loop.py` and `SystemClock` in `app/worker/loop.py`, which
    extraction already imports across modules.
- Worker wiring for independent loops follows `app/worker/cli.py` (`start_poller`,
  `start_extractor`). The evaluation-set pattern (repository JSONL, dev/test split,
  model pre-labels, owner review) follows `app/extraction/evaluation/` and
  `backend/evals/extraction/v1/`.

## Read context

- `docs/ROADMAP.md` — read in full. This is Stage 2, item 1 ("Hybrid retrieval on
  PostgreSQL"). It also takes the tooling half of item 5 ("Retrieval evaluation report"):
  the runner and set v1 are built here, while the reviewed comparison,
  the report and the default-model ADR stay in item 5. The Stage 2 DoD requires tracing and
  an evaluation set for LLM steps. Only one stage is worked on at a time, and Stage 1 is done.
- `docs/PROJECT.md` — read in full. FR-2.1 (index posts for hybrid retrieval) and FR-2.4
  (evaluation set for retrieval); the NFRs on budget ≤ 20 PLN/month, observability,
  swappability (OpenRouter as the only provider), privacy and UTC. The architecture says
  "facts are SQL, narrative is retrieval".
- `docs/DECISIONS.md` — read in full. Binding here:
  - ADR 0002 (PostgreSQL + pgvector, own hybrid retrieval, RRF, optional reranking, facts
    never retrieved by embedding);
  - API embeddings, no local models;
  - OpenRouter as the only provider (ADR 0006);
  - independent loops in the one worker process;
  - Langfuse tracing;
  - evaluation sets in the repository as JSONL with a dev/test split and owner review;
  - unit tests with fakes, evaluations outside `pytest`;
  - business-module layout;
  - testcontainers for database tests.
- `docs/BACKLOG.md` — searched for "retriev", "embed", "vector", "stage 2", "GW6". #12 (GW6
  deadline window, 2026-10-10) means set v1 is small until real pre-deadline leaks exist.
  #11 (twscrape missing posts) concerns ingest, not this spec.
- `docs/CONVENTIONS.md` — searched for "retriev", "embed", "vector", "full-text". No
  retrieval-specific rules; the general code, test and git rules apply.
- `docs/DEPLOYMENT.md` — searched for "retriev", "embed", "vector", "pgvector". Production
  PostgreSQL already runs the pgvector image, so the migration needs no database change;
  a new optional environment variable goes into the runbook.
- `docs/adr/` — read in full: 0002; searched 0006 for "embed" (no hits). ADR 0002 is the
  basis of this spec.

## Scope

- **Shared AI-provider layer `app/llm/`** (a refactor with no change in behaviour). It holds:
  - the OpenRouter and Langfuse settings;
  - the Langfuse client, handler and flush;
  - the price catalogue (`prices.toml`), generalised to models with input-only pricing.

  `Clock` and `SystemClock` move to `app/core`. Extraction keeps what is chat-specific
  (`providers.py`, `model_settings.py`, `run_config`, its evaluation).
- **Embedding adapter** behind an interface in `app/retrieval/`, with an OpenRouter
  implementation on the `openrouter` SDK. The default model is
  `openai/text-embedding-3-small`, overridable by an optional `EMBEDDING_MODEL`.
- **Index** built by a migration. It enables the `vector` and `unaccent` extensions, adds a
  full-text representation of every post (English configuration, diacritics-insensitive)
  and adds an embeddings store keyed by post and model, so vectors of several models can live
  side by side.
- **Indexing loop** in the worker process: its own thread, independent of the tweet poller,
  extraction and FPL jobs. It embeds every stored post that has no embedding for the
  configured model, with retries and a record of failures, cost and latency.
- **Search** over posts in three modes (`fulltext`, `vector`, `hybrid` with RRF). Filters:
  a time window, excluding reposts, excluding replies. Each result carries the post, the
  fused score and its rank in each mode.
- **CLI** `python -m app.retrieval`:
  - `index`: backfill, or re-index with another model;
  - `search`: one query, any mode;
  - `status`;
  - the evaluation commands (next item).
- **Evaluation tooling and set v1**:
  - a frozen corpus snapshot and queries as JSONL in the repository, split dev/test;
  - a query-set builder that mixes queries templated from real extracted events
    (player + status) with LLM-written queries for sampled posts, about 40 queries in
    total, about 10 of them in Polish;
  - pooled candidates from the three modes with binary relevance pre-labelled by the
    default chat model;
  - an interactive review command for the owner;
  - a runner that reports recall@5, recall@10 and MRR per mode, with the English and
    Polish slices shown separately.
- **Tracing** in Langfuse. Embedding calls are traced as generations with tokens and cost.
  Searches from the CLI and the evaluation runner are traced with the per-mode results.
- Documentation:
  - `.env.example` and DEPLOYMENT (`EMBEDDING_MODEL`);
  - DECISIONS rows;
  - the ROADMAP link;
  - a BACKLOG entry for reranking.

## Out of scope

- Corroboration of leaks (FR-2.2) — its own spec (Stage 2, item 2). It decides how to combine
  retrieval with SQL linking.
- The e-mail adapter and alerts (FR-2.3) — Stage 2, items 3 and 4.
- The reviewed comparison of modes and embedding models, the results report, the README
  section and the default-model ADR. These are Stage 2, item 5, after the GW6 deadline
  window (BACKLOG #12) has grown the set.
- Tuning RRF `k` and candidate depth beyond the defaults. The runner accepts them as
  parameters, and the tuning is item 5's work on the dev split.
- Reranking with a cross-encoder or an LLM → BACKLOG, P2, trigger: the item 5 report shows
  hybrid precision in the top 5 below what alerts need.
- An approximate-nearest-neighbour index (HNSW/IVFFlat) → BACKLOG, P3, trigger: vector search
  over the stored posts exceeds 200 ms p95. Exact search is enough at a few thousand posts a
  season.
- Indexing podcast transcripts or presser history (Stage 3/4).
- An HTTP API for search.

## Requirements and acceptance criteria

**Shared layer (refactor)**

- [ ] AC1: The OpenRouter/Langfuse settings, the Langfuse tracing helpers and the price
      catalogue live in `app/llm/`, and `Clock`/`SystemClock` live in `app/core`.
      `app/retrieval` imports nothing from `app/extraction`, and `app/extraction` imports
      no clock from `app/tweets` or `app/worker`.
- [ ] AC2: Extraction behaves exactly as before. The existing extraction, tweet and worker
      tests pass with only import-path changes, and the environment variable names are
      unchanged.
- [ ] AC3: The price catalogue accepts a model with input-only pricing. Cost is computed
      from input tokens alone for such a model and is `None` for a model not in the
      catalogue, and every existing chat-model price row keeps its current cost.

**Index and indexing loop**

- [ ] AC4: The migration enables `vector` and `unaccent`, adds the full-text
      representation and the embeddings store, and downgrades cleanly. It passes the
      existing migration test pattern (upgrade → downgrade → upgrade on a fresh
      container).
- [ ] AC5: A post is findable by full-text search in the same transaction that stores
      it. No embedding call is needed for that mode.
- [ ] AC6: With `OPENROUTER_API_KEY` set, the worker starts the indexing loop and logs its
      model. Without the key it logs "retrieval indexing disabled" and every other loop
      runs as before.
- [ ] AC7: The indexing loop embeds every post that has no embedding for the configured
      model, oldest first. It stores the model, the vector dimension, the input tokens, the
      cost and the latency from `first_fetched_at` to the stored embedding. With a fake
      embedder in a test, a newly stored post gets its embedding without restarting the
      loop.
- [ ] AC8: An embedding call that fails is retried (3 attempts with back-off, as in
      extraction). A post that still fails is recorded with its error class and is retried
      on a later pass no sooner than 10 minutes later, and the loop continues with the next
      post. A failure in this loop never stops the tweet poller or extraction (a test with a
      failing fake embedder).
- [ ] AC9: `EMBEDDING_MODEL` set to a model with no row in the price catalogue fails the
      worker on start with a message naming the variable. An empty or unset value uses
      `openai/text-embedding-3-small`.
- [ ] AC10: `python -m app.retrieval index` embeds every post missing an embedding for the
      chosen model (`--model` overrides the configured one) and prints the counts of
      embedded and failed posts and the total cost. A second run embeds nothing.
      Embeddings of another model stay untouched.

**Search**

- [ ] AC11: `fulltext` ranks posts by lexical match on the English configuration.
      "injured" finds a post saying "injury", and "Odegaard" finds a post saying "Ødegaard".
- [ ] AC12: `vector` ranks posts by cosine similarity between the query embedding and the
      post embeddings of the same model. It uses only posts with an embedding for that
      model, and it errors clearly when the model has no embeddings at all.
- [ ] AC13: `hybrid` fuses the full-text and vector rankings with RRF,
      `score = Σ 1/(k + rank)`, with default `k = 60` and default candidate depth 50 per
      mode, both overridable. A post found by only one mode can still appear. On a fixed
      test corpus the fused order equals a hand-computed RRF order.
- [ ] AC14: Every mode accepts `limit` (default 10), `since`/`until` (UTC), `exclude_reposts`
      and `exclude_replies`. Each result carries the X ID, author, `created_at`, text, the
      fused score, and its rank in each mode (empty where a mode did not return it).
     
- [ ] AC15: When the embedding call fails during a `hybrid` search, the search returns the
      full-text results, marks the vector leg as failed and logs the error class. A `vector`
      search fails with a clear error.
- [ ] AC16: `python -m app.retrieval search "<query>" [--mode] [--limit] [--since]
      [--until]` prints the ranked results with the per-mode ranks. The time filters are
      entered and shown in `Europe/Warsaw` and stored and compared in UTC.
- [ ] AC17: `python -m app.retrieval status` prints the post count, the embedded, missing
      and failed counts per model, the latest embedding with its latency, and the total
      embedding cost.

**Tracing**

- [ ] AC18: With Langfuse configured, every embedding call is traced as a generation with
      the model, input tokens and cost, and every CLI or evaluation search is traced with
      the query, the mode and the returned X IDs per mode. Without Langfuse configured,
      everything runs untraced and logs that once.

**Evaluation**

- [ ] AC19: The evaluation corpus is a committed JSONL snapshot of public posts (X ID,
      author, text, `created_at`, repost/reply flags). The runner loads it into an
      isolated database and never reads or writes the `tweet` table, so results do not
      depend on the current contents of any database. It is exported by a command from
      the development database.
- [ ] AC20: The query-set builder writes about 40 queries in JSONL: about 30 in English and
      about 10 in Polish, each tagged with its language and origin (`event` or `post`),
      split dev/test. `event` queries are templated from the corpus posts' current
      extraction events (player + status), and `post` queries are LLM-written for sampled
      posts.
- [ ] AC21: For every query the builder pools the top 10 candidates of each mode.
      The default chat model labels each candidate relevant or not relevant, and the
      label is stored as unreviewed.
- [ ] AC22: An interactive review command walks the unreviewed labels query by query. It
      lets the owner accept, flip or skip a label and add a relevant post by X ID, and it
      saves after every decision, as the extraction `review` command does.
- [ ] AC23: The runner computes recall@5, recall@10 and MRR per mode for a chosen split,
      overall and for the English and Polish slices. It counts only reviewed labels unless
      `--include-unreviewed` is given, and it writes a JSON result file with the model, `k`,
      depth, split, date and per-query ranks.
- [ ] AC24: Metric computation is covered by unit tests on hand-computed cases: a query with
      no relevant post in the top k, a query whose first relevant post is at rank 3, and a
      query with several relevant posts.
- [ ] AC25: Set v1 is committed: the corpus snapshot, the queries and the pre-labels. The
      owner's review of the labels is an owner-side step after the PR and is not required
      for this spec's done.

**Documentation and repository hygiene**

- [ ] AC26: `.env.example` and `docs/DEPLOYMENT.md` document `EMBEDDING_MODEL` (optional)
      and say that indexing runs whenever `OPENROUTER_API_KEY` is set.
- [ ] AC27: The `docs/DECISIONS.md` rows added with this SPEC still match what was built
      (edited in place if the implementation changed them), and `docs/BACKLOG.md` gets the
      reranking and ANN-index entries named in "Out of scope".
- [ ] AC28: Tests use a fake embedder and fake chat model. The only network calls are the
      owner's manual CLI runs, and `<verify.command>` stays green.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Index every stored post, reposts and replies included; flags are search filters | only posts with extracted events; everything but reposts | the posts extraction did not link are exactly what retrieval adds over SQL; cost is negligible |
| Embeddings through OpenRouter with the `openrouter` SDK pinned as a direct dependency, behind an own `Embedder` interface | the OpenAI API directly; `langchain-openai` with a `base_url`; local models | one provider and one key (ADR 0006); the SDK is already installed; local models are rejected for memory (DECISIONS) |
| Default embedding model `openai/text-embedding-3-small`; the model comparison belongs to roadmap item 5 | `qwen/qwen3-embedding-8b`; `google/gemini-embedding-001` | a multilingual, widely known baseline at $0.02 per million tokens; 1536 dimensions fit pgvector comfortably |
| Embeddings keyed by (post, model), several models side by side | one vector column per post | model comparison and re-indexing without losing the current index |
| Exact vector search, no ANN index | HNSW / IVFFlat now | a few thousand posts a season; exact results keep the evaluation clean |
| RRF with default `k = 60` and depth 50 per mode | weighted score fusion; a learned combination | RRF needs no calibration between `ts_rank` and cosine scales (ADR 0002) |
| Indexing in its own loop in the worker, plus a CLI | CLI only; a step in the extraction loop | fresh posts are indexed within seconds before a deadline; an embedding outage never blocks extraction (same reasoning as the extraction loop decision) |
| A shared `app/llm/` layer (provider settings, tracing, prices), with the clock moved to `app/core`; no shared evaluation framework | retrieval importing from `app.extraction`; a broad refactor including retries, loop base classes and evaluation | retrieval is the second consumer, and a third consumer (alerts, presser) is coming; the two evaluations differ in metrics, so a common framework would be premature |
| Evaluation set v1: a mix of event-templated and LLM-written queries (~40, ~10 Polish), pooled from the three modes, binary relevance pre-labelled by a model, reviewed by the owner; a frozen corpus snapshot | the owner writing every query; event-templated queries only; graded relevance with nDCG | realistic corroboration queries plus variety; the Polish slice checks cross-lingual retrieval before Stage 4; binary labels make review fast and consistent |
| Tooling and set v1 here; the reviewed comparison and the report in roadmap item 5 | no evaluation now; everything in one spec | the set stays small until the GW6 window (BACKLOG #12); the parameters are measured, not guessed |
| Embedding calls and searches traced in Langfuse | embeddings only; no tracing | cost visibility and per-mode ranking for every query, and it is visible in the portfolio |

## Owner decisions

- New dependency `pgvector` (Python, exact pin), for the SQLAlchemy vector type — accepted.
- `openrouter==0.11.46` promoted from a transitive to a direct, exactly pinned dependency —
  accepted (OpenRouter embeddings chosen).
- Data migration: `CREATE EXTENSION vector`, `CREATE EXTENSION unaccent`, the full-text
  representation of `tweet` and the embeddings store — accepted. Production gets it through
  the Railway pre-deploy after the owner merges.
- Evaluation scope: tooling and set v1 here; the report stays in roadmap item 5.
- Refactor: `app/llm/` plus the clock in `app/core`, and nothing broader.
- Indexing: a loop in the worker plus a CLI.
- Evaluation: English queries with a Polish slice; binary relevance; a mix of queries with
  pooling.
- Tracing: embeddings and searches.

## Open questions (non-blocking)

- none
