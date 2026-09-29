# PLAN 006 — Hybrid retrieval over posts

## Owner summary

- **Approach:** First a pure refactor: the OpenRouter/Langfuse settings, the Langfuse helpers
  and the price catalogue move to a new `app/llm/`, and the clock moves to `app/core/clock.py`.
  Then a migration adds a generated full-text column on `tweet` (an `english_unaccent`
  configuration) and a `post_embedding` table keyed by (post, model). The new `app/retrieval/`
  holds an OpenRouter embedder behind an `Embedder` interface, an indexing loop in the worker
  and a CLI. Search runs full-text and exact cosine search in SQL and fuses them with RRF in
  Python. The evaluation tooling loads a frozen corpus into its own schema
  (`retrieval_eval`), so it never touches the live `tweet` table. It builds about 40 queries,
  pools and pre-labels candidates with the default chat model, has a review command and a
  runner for recall@5/10 and MRR.
- **Main risks:** the English stemmer does not map "injured" and "injury" to one lexeme
  (checked: `injur` and `injuri`), so query lexemes are matched as OR-ed prefixes. Alembic's
  model/migration comparison may not handle the pgvector type or the generated column. Set v1
  needs paid OpenRouter calls: the implementer runs them against the development database
  with a spend cap (under $1, expected under $0.20). One existing extraction test assertion
  has to narrow to chat models, because the price catalogue now holds an embedding model too.
- **New dependency:** yes. `pgvector==0.5.0` provides the SQLAlchemy `VECTOR` type and has no
  required dependencies. `openrouter==0.11.46` is already installed and is now pinned
  directly. Both were accepted in SPEC → "Owner decisions".
- **Data migration:** yes. `0005` enables `vector` and `unaccent`, creates the
  `english_unaccent` text-search configuration, adds the generated column
  `tweet.search_vector` with a GIN index and creates `post_embedding`. It downgrades
  cleanly. Accepted in SPEC → "Owner decisions". Production gets it through the Railway
  pre-deploy after the merge.
- **Manual scenarios for the owner:** 2. (1) A live worker run with the real key and
  Langfuse: a new post gets its embedding within seconds, and the embedding and search traces
  appear in Langfuse. (2) After the PR, the owner reviews the set v1 labels. This is not
  required for this spec's done.

## Approach

**What the plan rests on**

- `specs/006-hybrid-retrieval/SPEC.md`: read in full, including Owner decisions.
- `docs/CONVENTIONS.md`: read in full.
- `docs/DECISIONS.md`: searched for "retriev", "embed", "vector", "full-text", "rrf",
  "clock", "evaluation set". It gave the rows for ADR 0002 (row 14), API embeddings
  (row 23), fake LLMs in tests (row 25), the extraction evaluation set (row 47) and the five
  spec-006 rows (51–55) that AC27 must keep true.
- `docs/ROADMAP.md`: searched for "Stage 2", "retriev", "hybrid". It gave Stage 2 item 1
  (to tick) and item 5 (it already says that the tooling and set v1 come with 006).
- `docs/BACKLOG.md`: searched for "retriev", "embed", "vector", "rerank", and the
  numbering. The highest number is #15; no existing entry covers reranking or ANN.
- `docs/DEPLOYMENT.md`: searched for "OPENROUTER", "LANGFUSE", "LLM_MODEL", "vector",
  "prices.toml". Section 8 (extraction variables) is where `EMBEDDING_MODEL` goes. It names
  `backend/app/extraction/…prices.toml`, which moves.
- `docs/PROJECT.md`: searched for FR-2.1 and FR-2.4. They match the SPEC and add no
  constraint.
- Code read in full:
  - `app/core/settings.py`, `app/core/errors.py`;
  - `app/extraction/{config,tracing,pricing,prices.toml,providers,model_settings,loop,service,store,models,cli,generation}.py`;
  - `app/extraction/evaluation/{cases,review,selection}.py`;
  - `app/tweets/{models,loop,store}.py`;
  - `app/worker/{cli,loop,jobs}.py`, `app/db/engine.py`, `app/fpl/models/columns.py`;
  - `app/content/__init__.py`;
  - `migrations/env.py`, `migrations/versions/0003_tweets.py`, `0004_extraction.py`;
  - `tests/conftest.py`, `tests/db/test_migrations.py`;
  - `tests/extraction/{fakes,test_tracing,test_pricing,test_dependency,test_model_settings}.py`;
  - `tests/test_env_example.py`, `tests/test_deployment.py`, `tests/test_readme.py`.
- Code searched:
  - `tests/worker/test_cli.py`: the test names, the env fixture `EXTRACTION_VARIABLE`, the
    worker-deps tests (l. 628–710) and the langfuse-warning test;
  - `tests/extraction/test_loop.py`: the test names;
  - every import of the moved names (`TracingConfig`, `resolve_tracing`,
    `app.extraction.tracing`, `app.extraction.pricing`, `ExtractionSettings`,
    `load_extraction_settings`, `Clock`, `SystemClock`, `StopAwareClock`);
  - `tests/fpl/fakes.py::table_contents`.
- Probed in the installed packages:
  - `openrouter.embeddings.generate(input=, model=, encoding_format=, retries=)`, which
    returns `data[].embedding` and `usage.prompt_tokens`/`cost`;
  - the SDK's own 5XX backoff, which is on unless `retry_config` is overridden (as
    `providers.py` does);
  - Langfuse 4.15 `start_observation(as_type="embedding" | "retriever", model=,
    usage_details=, cost_details=)`;
  - the `pgvector` 0.5.0 wheel: `pgvector.sqlalchemy.VECTOR(dim=None)`, list in and list out,
    numpy optional.
- Probed on a `pgvector/pgvector:pg16` container:
  - `english_stem('injury') = injuri` and `english_stem('injured') = injur`;
  - `out` is an English stop word;
  - a copied `english` configuration with `unaccent` maps `Ødegaard` to `odegaard`, and it
    works inside a `GENERATED … STORED` column;
  - the OR-ed prefix query `'injur':* | 'odegaard':*` matches both posts;
  - a stop-word-only query matches nothing and does not fail;
  - `vector` without a dimension accepts mixed dimensions and `<=>` works within one
    dimension.

**Patterns reused**

- Loop and thread wiring: `app/extraction/loop.py`, which is `ExtractionLoop` and
  `start_extractor` with `StopAwareClock` and `Shutdown`. The worker wiring follows
  `app/worker/cli.py`: an optional runtime in `WorkerDeps`, a "… disabled" log, a thread
  join in `finally`.
- Attempts with back-off: `app/extraction/service.py::run_with_retries` (3 attempts, back-off
  2 s and 4 s, stop-aware). It is re-implemented in `app/retrieval/indexing.py` as a small
  generic helper, because AC1 forbids importing it and the SPEC rejects a shared retry
  refactor.
- Latency and failure records: `_store_extracted` and `_store_failed` in extraction
  (`latency = stored_at - tweet.first_fetched_at`, recorded only by the loop).
- CLI shape: `app/extraction/cli.py`. It uses a `typer` app, `get_deps(ctx)` with injectable
  `ctx.obj`, `fail()`, commands that need no database or key never build deps, and results
  go to `evals/<feature>/results/` with `dev/` gitignored.
- JSONL with atomic writes: `app/extraction/evaluation/cases.py::write_cases`, re-implemented
  in the retrieval evaluation because it cannot be imported. The interactive review loop
  (save after every decision, `typer.Abort` → exit 130) follows
  `app/extraction/cli.py::review`.
- Prompts: `app/content/prompts/*.md` with a `version: N` header, loaded by
  `app.content.load_prompt`.
- Tests:
  - `tests/conftest.py` fixtures `db` and `db_session`, on a testcontainer that already runs
    the pgvector image;
  - `tests/extraction/fakes.py::FakeChatModel`, which tests may import (the AC1 boundary
    applies to `app/`, not to `tests/`);
  - `tests/worker/test_cli.py` helpers (`cli`, `FakeSource`, `RealClock`, `_wait_until`);
  - the `test_dependency.py` pin check;
  - the `test_migrations.py` upgrade/downgrade pattern.

**Design choices**

- **Full-text representation.** Chosen: a stored generated column
  `tweet.search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english_unaccent'::regconfig, text)) STORED`,
  with a GIN index. `english_unaccent` is `COPY = english` with `hword, hword_part, word`
  mapped to `unaccent, english_stem`. Rejected: an expression index only (the query must
  repeat the expression exactly, and Alembic compares expression indexes unreliably); a
  trigger-maintained side table (more moving parts). A generated column is filled inside the
  inserting transaction, which is AC5. `unaccent()` itself is not immutable and cannot sit in
  a generated column; a text-search configuration can.
- **Full-text query.** Chosen: the query is normalised with the same configuration. Its
  lexemes are OR-ed as prefix terms, and rows are ranked by `ts_rank_cd`:
  `to_tsquery('simple', string_agg(quote_literal(lexeme) || ':*', ' | ')) FROM unnest(to_tsvector('english_unaccent', :query))`.
  Rejected: `websearch_to_tsquery` or `plainto_tsquery`. Their AND semantics lose natural
  queries, and they cannot match `injured` (`injur`) to `injury` (`injuri`), which AC11
  requires. `ts_rank_cd` still ranks posts that match more terms higher.
- **Embeddings store.** One table `post_embedding` with one row per (post, model): `status`
  `embedded | failed`, `embedding VECTOR` (no fixed dimension, so models of different sizes
  live side by side), `dimensions`, `input_tokens`, `cost_usd`, `latency_seconds`,
  `attempts`, `error_class` and `updated_at`. A failed row is upserted into an embedded one
  on a later success. Rejected: a separate failures table, which is two writes and a join for
  the same information.
- **One post per embedding call.** AC7 stores input tokens and cost per post, and the API
  reports usage per request. A failure also stays attributable to one post. The backfill of
  about 210 posts takes a minute or two, which is acceptable.
- **Fusion in Python.** Each leg is one SQL query (depth 50), and `fuse()` is a pure function
  that is easy to test against hand-computed RRF. Ties are broken by the better best rank,
  then by the higher `x_id` (newer post).
- **Evaluation isolation.** Chosen: a dedicated PostgreSQL schema `retrieval_eval` in the
  database from `DATABASE_URL`. It is dropped and recreated per run, and the `tweet` and
  `post_embedding` tables are created in it from the same SQLModel metadata
  (`schema_translate_map={None: "retrieval_eval"}`). The eval engine connects with
  `search_path=retrieval_eval,public`, so the unchanged search and indexing code reads the
  eval tables, and extensions and the text-search configuration resolve from `public`.
  Rejected: a throwaway testcontainer at evaluation time, which would put the dev-only
  `testcontainers` dependency in `app/`. This is how the plan reads AC19's "isolated
  database". The DECISIONS row 55 wording is amended to say so (AC27).
- **The default chat model for evaluation.** `app/retrieval` may not import
  `app.extraction` (AC1), and extraction keeps `providers.py` and `model_settings.py`
  (SPEC). So `app/retrieval/evaluation/llm.py` builds `ChatOpenRouter` itself with
  `DEFAULT_LABEL_MODEL = "openai/gpt-6-luna"`, the ADR 0006 default chat model, overridable
  by `--model`. The settings are those of luna's `model_settings.toml` row: reasoning effort
  `none`, no temperature, `function_calling`, SDK retries off. A test in `tests/` asserts
  `DEFAULT_LABEL_MODEL == app.extraction.config.DEFAULT_MODEL`, so the two cannot drift.
- **Settings.**
  - `app/llm/settings.py::LlmSettings` holds `openrouter_api_key`, `langfuse_*` and
    `usd_pln_rate`, with the same env names and `env_ignore_empty=True`.
  - `ExtractionSettings(LlmSettings)` (+ `llm_model`, `llm_fallback_model`) and
    `load_extraction_settings` move to `app/extraction/config.py`. They are chat-specific,
    and `app/core` must not import `app/llm`.
  - `RetrievalSettings(LlmSettings)` (+ `embedding_model`) lives in
    `app/retrieval/config.py`.
  - A generic `load_llm_settings(cls)` keeps the "invalid value of VAR" error.

**Module layout (new)**

```
backend/app/core/clock.py              Clock (Protocol), SystemClock, StopAwareClock
backend/app/llm/__init__.py
backend/app/llm/settings.py            LlmSettings, load_llm_settings
backend/app/llm/tracing.py             TracingConfig, resolve_tracing, make_handler, make_client, flush
backend/app/llm/pricing.py, prices.toml  (moved; input-only rows allowed)
backend/app/retrieval/__init__.py, __main__.py
backend/app/retrieval/config.py        RetrievalSettings, load_retrieval_settings, DEFAULT_EMBEDDING_MODEL, EmbeddingConfig, resolve_embedding
backend/app/retrieval/embedder.py      Embedder (Protocol), EmbeddingResult, OpenRouterEmbedder, build_embedder
backend/app/retrieval/models.py        PostEmbedding
backend/app/retrieval/tracing.py       RetrievalTracer (Protocol), LangfuseTracer, NullTracer, make_tracer
backend/app/retrieval/store.py         PostToEmbed, next_unembedded, posts_missing, save_embedded, save_failed, embedding_status
backend/app/retrieval/indexing.py      IndexingRuntime, with_retries, embed_post, index_missing, traced_embed
backend/app/retrieval/loop.py          IndexingLoop, start_indexer
backend/app/retrieval/search.py        Mode, SearchFilters, SearchResult, SearchResponse, SearchError, NoEmbeddingsError, fuse, search
backend/app/retrieval/cli.py           index, status, search, export-corpus, build-queries, prelabel, review, evaluate
backend/app/retrieval/evaluation/{__init__,dataset,schema,metrics,queries,labelling,llm,runner}.py
backend/app/content/prompts/retrieval_query.md, retrieval_relevance.md
backend/app/content/retrieval_query_templates.toml
backend/migrations/versions/0005_retrieval.py
backend/evals/retrieval/v1/corpus.jsonl, queries.jsonl   (step 24)
```

**Key shapes** (precision only; names may be refined if the step's tests say so)

```python
class Embedder(Protocol):
    model: str
    def embed(self, texts: Sequence[str]) -> EmbeddingResult: ...
@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    input_tokens: int | None

@dataclass(frozen=True)
class SearchFilters:
    since: datetime | None = None; until: datetime | None = None
    exclude_reposts: bool = False; exclude_replies: bool = False
@dataclass(frozen=True)
class SearchResult:
    x_id: int; author_handle: str; created_at: datetime; text: str
    score: float                           # RRF over the legs that ran
    ranks: dict[str, int | None]           # {"fulltext": 2, "vector": None}, always both keys
@dataclass(frozen=True)
class SearchResponse:
    mode: str; model: str | None; results: list[SearchResult]; failed_legs: tuple[str, ...]
def fuse(rankings: Mapping[str, Sequence[int]], k: int) -> list[tuple[int, float, dict[str, int]]]
def search(engine, query, mode="hybrid", *, embedder=None, filters=SearchFilters(),
           limit=10, k=60, depth=50, tracer=NULL_TRACER, prices=None) -> SearchResponse
```

Evaluation JSONL (one object per line):

- corpus: `{"x_id", "author_handle", "text", "created_at", "is_repost", "is_reply"}`;
- queries: `{"id", "text", "language": "en"|"pl", "origin": "event"|"post", "source_x_id": int|null, "event": {"player", "event_type"}|null, "split": "dev"|"test", "judgements": [{"x_id", "relevant", "reviewed", "labelled_by"}]}`.

## AC → steps matrix

| AC | Steps | Proving test | Red before the change |
|----|-------|--------------|-----------------------|
| AC1 | 1, 2, 4 | `tests/test_module_boundaries.py::test_extraction_takes_no_clock_from_tweets_or_worker`, `::test_shared_layer_lives_in_app_llm_and_core`, `::test_retrieval_imports_nothing_from_extraction` | |
| AC2 | 1, 2, 3 | n/a — kept behaviour: the existing suite passes after steps 1–3 with import-path changes only. The one exception is the catalogue-equality assertion in `tests/extraction/test_model_settings.py`, which narrows to chat rows (step 3, see Risks) | n/a |
| AC3 | 3 | `tests/llm/test_pricing.py::test_input_only_row_loads_and_costs_input_alone`, `::test_unknown_model_costs_none`, `::test_chat_rows_keep_their_cost` | |
| AC4 | 5 | `tests/db/test_migrations.py::test_retrieval_migration_keeps_data_and_downgrades`, `::test_upgrade_downgrade_upgrade`, `::test_models_match_migration` | |
| AC5 | 5, 15 | `tests/retrieval/test_store.py::test_post_findable_by_fulltext_in_the_storing_transaction`; `tests/retrieval/test_cli.py::test_fulltext_search_needs_no_key` | |
| AC6 | 11 | `tests/worker/test_cli.py::test_run_without_key_logs_retrieval_indexing_disabled_once`, `::test_run_with_indexing_logs_model_and_embeds_new_post` | |
| AC7 | 9, 10 | `tests/retrieval/test_indexing.py::test_embed_post_stores_model_dimensions_tokens_cost_latency`, `::test_oldest_first`; `tests/retrieval/test_loop.py::test_new_post_embedded_without_restart` | |
| AC8 | 9, 10, 11 | `tests/retrieval/test_indexing.py::test_three_attempts_then_failed_with_error_class`, `::test_failed_post_not_retried_before_10_minutes`; `tests/retrieval/test_loop.py::test_failure_does_not_stop_the_loop`; `tests/worker/test_cli.py::test_failing_embedder_does_not_stop_polls_or_extraction` | |
| AC9 | 7, 11 | `tests/retrieval/test_config.py::test_unpriced_embedding_model_names_the_variable`, `::test_empty_or_unset_uses_default`; `tests/worker/test_cli.py::test_worker_rejects_unpriced_embedding_model_at_start` | |
| AC10 | 12 | `tests/retrieval/test_cli.py::test_index_embeds_missing_and_prints_counts_and_cost`, `::test_index_second_run_embeds_nothing`, `::test_index_model_option_leaves_other_model_untouched` | |
| AC11 | 14 | `tests/retrieval/test_search.py::test_fulltext_injured_finds_injury`, `::test_fulltext_odegaard_finds_accented` | |
| AC12 | 14 | `tests/retrieval/test_search.py::test_vector_orders_by_cosine_within_model`, `::test_vector_without_embeddings_errors_clearly` | |
| AC13 | 13, 14 | `tests/retrieval/test_fusion.py::test_fuse_matches_hand_computed_rrf`, `::test_single_leg_post_still_appears`; `tests/retrieval/test_search.py::test_hybrid_order_equals_hand_computed_rrf`, `::test_k_and_depth_overridable` | |
| AC14 | 13, 14 | `tests/retrieval/test_search.py::test_filters_limit_window_reposts_replies`, `::test_result_fields_and_ranks` | |
| AC15 | 14 | `tests/retrieval/test_search.py::test_hybrid_degrades_to_fulltext_when_embedding_fails`, `::test_vector_fails_clearly_when_embedding_fails` | |
| AC16 | 15 | `tests/retrieval/test_cli.py::test_search_prints_ranked_results_with_ranks`, `::test_search_time_filters_in_warsaw_compared_in_utc` | |
| AC17 | 12 | `tests/retrieval/test_cli.py::test_status_prints_counts_latest_and_cost` | |
| AC18 | 8, 12, 15, 22 | `tests/retrieval/test_tracing.py::test_langfuse_tracer_records_embedding_generation_offline`, `::test_no_tracing_logs_once`; `tests/retrieval/test_indexing.py::test_embedding_call_traced_with_model_tokens_cost`; `tests/retrieval/test_cli.py::test_search_traced_with_query_mode_and_ids`, `::test_index_without_langfuse_logs_once`; `tests/retrieval/evaluation/test_runner.py::test_runner_traces_searches_and_embeddings` | |
| AC19 | 16, 17 | `tests/retrieval/evaluation/test_dataset.py::test_export_corpus_writes_public_fields_only`; `tests/retrieval/evaluation/test_schema.py::test_eval_schema_never_touches_public_tweet` | |
| AC20 | 19 | `tests/retrieval/evaluation/test_queries.py::test_builder_writes_counts_by_language_and_origin`, `::test_event_queries_templated_from_current_events`, `::test_split_is_stratified_and_deterministic` | |
| AC21 | 20 | `tests/retrieval/evaluation/test_labelling.py::test_pools_top10_of_each_mode_and_prelabels_unreviewed`, `::test_prelabel_resumes_and_skips_labelled_queries` | |
| AC22 | 21 | `tests/retrieval/test_cli.py::test_review_accept_flip_skip_add_and_saves_after_each` | |
| AC23 | 18, 22 | `tests/retrieval/evaluation/test_runner.py::test_runner_reports_metrics_per_mode_and_slice`, `::test_only_reviewed_labels_unless_flag`, `::test_result_file_fields` | |
| AC24 | 18 | `tests/retrieval/evaluation/test_metrics.py::test_no_relevant_in_top_k`, `::test_first_relevant_at_rank_3`, `::test_several_relevant` | |
| AC25 | 24 | `tests/retrieval/evaluation/test_eval_set.py::test_set_v1_committed_and_consistent` | |
| AC26 | 7, 23 | `tests/test_env_example.py::test_every_retrieval_setting_is_an_empty_placeholder`; `tests/test_readme.py::test_deployment_documents_embedding_model` | |
| AC27 | 23 | `tests/test_docs.py::test_backlog_has_reranking_and_ann_entries`; the DECISIONS rows are checked by hand in step 23 (text) | |
| AC28 | all | n/a — kept property: every new test uses `FakeEmbedder` / `FakeChatModel`; `tests/retrieval/test_no_network.py::test_openrouter_embedder_unused_without_injected_client` guards the default path (step 6); full `verify.command` green | n/a |

## Steps

Every step writes and runs its proving tests first (record the red in the matrix), then
makes the product change. Run all commands from the repository root. `V` =
`cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`.

### Group 1 — Shared AI-provider layer (refactor, no behaviour change)

- [x] 1. **Clock to `app/core/clock.py` (AC1).** Move `Clock` (Protocol) and `StopAwareClock`
      from `app/tweets/loop.py`, and `SystemClock` from `app/worker/loop.py`, into
      `app/core/clock.py`. The old modules import them from there, so internal names keep
      working.
      - Update every importer in `app/`: `extraction/{loop,service,cli}.py`,
        `extraction/evaluation/runner.py`, `worker/cli.py`.
      - Update the tests that import them (`tests/tweets/test_loop.py` and others the grep
        finds), changing the import path only.
      - `app/worker/loop.py` also defines its own duplicate `Clock` Protocol: drop it and
        import `Clock` from `app.core.clock` there too, so one Protocol remains.
      - Leave `app/tweets/cli.py`'s private measurement clock untouched (SPEC: nothing
        broader).
      - First write `tests/test_module_boundaries.py` (AST scan of `app/extraction/**/*.py`):
        `test_extraction_takes_no_clock_from_tweets_or_worker` fails on any `ImportFrom` of
        `Clock|SystemClock|StopAwareClock` from a module starting with `app.tweets` or
        `app.worker`. Also write `test_shared_layer_lives_in_app_llm_and_core`, which for
        now only imports `app.core.clock`; step 2 extends it.

      Automatic verification: `cd backend && uv run pytest -q tests/test_module_boundaries.py tests/tweets tests/worker tests/extraction`
- [x] 2. **Settings and tracing to `app/llm/` (AC1, AC2).**
      - Create `app/llm/__init__.py` and `app/llm/settings.py`: `LlmSettings(BaseSettings)`
        with `openrouter_api_key`, `langfuse_public_key`, `langfuse_secret_key`,
        `langfuse_host` and `usd_pln_rate`. Keep the same defaults and the
        `model_config` of today's `ExtractionSettings`. Add
        `load_llm_settings(cls: type[T]) -> T`, which carries today's
        `ConfigError("invalid value of VAR")` logic.
      - `ExtractionSettings(LlmSettings)` (+ `llm_model`, `llm_fallback_model`) and
        `load_extraction_settings()` move from `app/core/settings.py` to
        `app/extraction/config.py`.
      - Create `app/llm/tracing.py` with `TracingConfig` and `resolve_tracing` (from
        `extraction/config.py`), and `make_handler` and `flush` (from
        `extraction/tracing.py`). `app/extraction/tracing.py` keeps only `run_config`.
      - Update the importers in `app/` (`extraction/{service,loop,cli}.py`,
        `worker/cli.py`) and in the tests (`test_env_example.py`,
        `extraction/{test_config,test_cli,test_tracing,test_openrouter_payload}.py` and any
        other the grep finds), changing import paths only.
      - Tests first: extend `test_shared_layer_lives_in_app_llm_and_core` to import
        `app.llm.settings.LlmSettings`, `app.llm.tracing.{TracingConfig, resolve_tracing, make_handler, flush}`
        and to assert `set(LlmSettings.model_fields) == {"openrouter_api_key", "langfuse_public_key", "langfuse_secret_key", "langfuse_host", "usd_pln_rate"}`.
        Add `tests/llm/__init__.py` and `tests/llm/test_settings.py`: the env names
        unchanged (set `OPENROUTER_API_KEY`, `LANGFUSE_HOST`, `USD_PLN_RATE` → the fields),
        and a malformed `USD_PLN_RATE` → `ConfigError` naming only the variable.

      Automatic verification: `cd backend && uv run pytest -q tests/test_module_boundaries.py tests/llm tests/test_env_example.py tests/extraction tests/worker`
- [x] 3. **Price catalogue to `app/llm/`, input-only rows (AC2, AC3).**
      - `git mv app/extraction/pricing.py app/llm/pricing.py` and `app/extraction/prices.toml`
        `app/llm/prices.toml`.
      - Make `Price.output_per_million: float | None` (`entry.get("output_per_million")`).
      - `compute_cost(model, input_tokens, output_tokens, prices)`:
        - an unknown model → `None`;
        - `input_tokens is None` → `None`;
        - an input-only price → the cost of the input tokens alone, whatever
          `output_tokens` is;
        - otherwise today's behaviour (`output_tokens is None` → `None`).
      - Add the row `["openai/text-embedding-3-small"] input_per_million = 0.02`,
        `checked = "2026-09-29"`, and update the header comment (embedding models carry
        only `input_per_million`).
      - Update the importers:
        - `extraction/service.py`: keep `load_prices` imported into its namespace, because
          `tests/worker/test_cli.py` patches `app.extraction.service.load_prices`; keep its
          error text "prices.toml or aliases.toml cannot be loaded";
        - `extraction/evaluation/runner.py`;
        - the tests.
      - Change `.env.example` and `docs/DEPLOYMENT.md`, which name
        `backend/app/extraction/…prices.toml`, to `backend/app/llm/prices.toml`.
      - In `tests/extraction/test_model_settings.py` narrow the catalogue-equality assertion
        to chat rows:
        `{m for m, p in load_prices().items() if p.output_per_million is not None}`.
        This is the one non-import change to an extraction test (see Risks).
      - Tests first, in `tests/llm/test_pricing.py`:
        - `test_input_only_row_loads_and_costs_input_alone` (a tmp TOML;
          1 000 000 tokens × 0.02 = 0.02, with `output_tokens` `None` and `7`);
        - `test_unknown_model_costs_none`;
        - `test_chat_rows_keep_their_cost` (for each chat row of the real catalogue, the cost
          of 1M in + 1M out equals the hand sum, e.g. `openai/gpt-6-luna` → 0.60);
        - `test_embedding_default_is_priced`.

      Automatic verification: `cd backend && uv run pytest -q tests/llm tests/extraction && uv run pytest -q tests/worker/test_cli.py -k "prices or deps or model" && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`

### Group 2 — Index: schema, embedder, indexing loop, worker, CLI

- [x] 4. **Dependencies and the retrieval package (AC1 boundary, owner-accepted pins).**
      - Add `pgvector==0.5.0` and `openrouter==0.11.46` to
        `backend/pyproject.toml` `dependencies`, then run `cd backend && uv lock && uv sync --extra dev`.
      - Create `app/retrieval/__init__.py`.
      - Tests first:
        - `tests/retrieval/__init__.py`;
        - `tests/retrieval/test_dependency.py::test_retrieval_dependencies_pinned_version`
          (the pattern of `tests/extraction/test_dependency.py` for `pgvector` and
          `openrouter`);
        - `tests/test_module_boundaries.py::test_retrieval_imports_nothing_from_extraction`
          (AST scan of `app/retrieval/**/*.py`; it asserts the file list is non-empty, so it
          is red before the package exists, and that no `import app.extraction…` or
          `from app.extraction…` appears).

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_dependency.py tests/test_module_boundaries.py tests/extraction/test_dependency.py`
- [x] 5. **Migration 0005 and models (AC4, AC5)**, a separate step.
      - `app/tweets/models.py`: add
        `search_vector: str | None = Field(default=None, sa_column=Column(TSVECTOR(), Computed("to_tsvector('english_unaccent'::regconfig, text)", persisted=True), nullable=True))`
        and `Index("ix_tweet_search_vector", "search_vector", postgresql_using="gin")`.
      - `app/retrieval/models.py::PostEmbedding` (table `post_embedding`) with these
        columns:
        - `id` PK;
        - `tweet_x_id BigInteger` FK `tweet.x_id`, not null;
        - `model str`;
        - `status str  # embedded | failed`;
        - `embedding VECTOR()` nullable;
        - `dimensions int | None`;
        - `input_tokens int | None`;
        - `cost_usd float | None`;
        - `latency_seconds float | None`;
        - `attempts int`;
        - `error_class str | None`;
        - `updated_at` (`utc_column()`).

        Add `UniqueConstraint("tweet_x_id", "model", name="uq_post_embedding_tweet_model")`
        and `Index("ix_post_embedding_model_status", "model", "status")`.
      - `migrations/versions/0005_retrieval.py` (revision `0005`, down `0004`). Upgrade:
        1. `CREATE EXTENSION IF NOT EXISTS vector`;
        2. `… unaccent`;
        3. `CREATE TEXT SEARCH CONFIGURATION english_unaccent (COPY = english)`;
        4. `ALTER TEXT SEARCH CONFIGURATION english_unaccent ALTER MAPPING FOR hword, hword_part, word WITH unaccent, english_stem`;
        5. `op.add_column` with `sa.Computed(…, persisted=True)`;
        6. the GIN index;
        7. `op.create_table("post_embedding", …)` with the constraint and index.

        Downgrade is the exact reverse, ending with `DROP TEXT SEARCH CONFIGURATION english_unaccent`,
        `DROP EXTENSION IF EXISTS unaccent`, `DROP EXTENSION IF EXISTS vector`.
      - Add `import app.retrieval.models  # noqa: F401` to `migrations/env.py` and to
        `tests/db/test_migrations.py`.
      - Tests first:
        - `tests/db/test_migrations.py::test_retrieval_migration_keeps_data_and_downgrades`:
          upgrade `0004`, seed a `job_run` row and a tweet with text "Ødegaard injury",
          snapshot the other tables with `table_contents(…, exclude=DEFAULT_EXCLUDE | {"search_vector"})`,
          then upgrade `head`. Assert `post_embedding` exists, the tweet's `search_vector`
          is filled (`'odegaard'` in it) and the other contents are unchanged. Then
          downgrade `-1`: the table, the column and the configuration are gone
          (`pg_ts_config`), the extensions are gone (`pg_extension`), and the contents are
          unchanged.
        - Adjust the existing per-revision tests, which now have a later head: exclude
          `post_embedding` from their `other_tables`, pass
          `exclude=DEFAULT_EXCLUDE | {"search_vector"}` where they snapshot `tweet` before
          0005, and change `test_extraction_migration_adds_only_new_tables` to
          `upgrade "0004"` instead of `head`.
        - `tests/retrieval/test_store.py::test_post_findable_by_fulltext_in_the_storing_transaction`:
          in one `Session`, call `app.tweets.store.store_posts(...)` for "Saka picked up an
          injury", then, before commit, `SELECT x_id FROM tweet WHERE search_vector @@ to_tsquery('simple', 'injuri')`
          returns it.

      Automatic verification: `cd backend && uv run pytest -q tests/db/test_migrations.py tests/retrieval/test_store.py tests/tweets tests/extraction/test_store.py`
- [x] 6. **Embedder (AC28 guard, basis of AC7).** `app/retrieval/embedder.py`:
      - `Embedder` Protocol and `EmbeddingResult` (see Key shapes).
      - `OpenRouterEmbedder(api_key: SecretStr, model: str, client: httpx.Client | None = None)`:
        - builds `OpenRouter(api_key=…, client=client, timeout_ms=30_000)` with
          `sdk_configuration.retry_config = RetryConfig("none", BackoffStrategy(0, 0, 1.0, 0), False)`,
          because the service owns the attempts;
        - `embed()` calls `embeddings.generate(input=list(texts), model=model, encoding_format="float")`,
          orders `data` by `index`, and raises `ValueError` on a non-list embedding or a
          count mismatch;
        - `input_tokens = usage.prompt_tokens if usage else None`.
      - `build_embedder(config: EmbeddingConfig) -> Embedder`. It is defined in step 7; here
        it may take `api_key, model` directly.
      - `tests/retrieval/fakes.py::FakeEmbedder(model, vectors: dict[str, list[float]] | None, default: list[float], responses: list[Exception | None] | None, tokens_per_text=5)`:
        - it records `calls`;
        - a scripted exception is raised in order;
        - an unknown text gets a deterministic vector (from a hash of the text) when
          `default` is None.
      - Tests first, in `tests/retrieval/test_embedder.py`, with `httpx.MockTransport`
        returning the synthetic payload `tests/retrieval/payloads/openrouter_embeddings.json`:
        - the request body carries model, input and `encoding_format`;
        - two texts return in index order;
        - `input_tokens` comes from the payload;
        - a 500 raises after exactly one request (SDK retries off).

        `tests/retrieval/test_no_network.py::test_openrouter_embedder_unused_without_injected_client`
        asserts that `FakeEmbedder` satisfies the Protocol and that no test module under
        `tests/retrieval` constructs `OpenRouterEmbedder` without a `client=` argument (AST
        scan).

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_embedder.py tests/retrieval/test_no_network.py`
- [x] 7. **Retrieval config and `.env.example` (AC9, AC26 part).** `app/retrieval/config.py`:
      - `RetrievalSettings(LlmSettings)` with `embedding_model: str = ""`, and
        `load_retrieval_settings()` (via `load_llm_settings`).
      - `DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"`.
      - `@dataclass(frozen=True) EmbeddingConfig(model, api_key, prices)`.
      - `resolve_embedding(settings, model: str | None = None, prices: dict[str, Price] | None = None) -> EmbeddingConfig | None`:
        - `None` without `openrouter_api_key`;
        - the model is `--model`, else `EMBEDDING_MODEL`, else the default;
        - `prices` is loaded when not given, and a load error →
          `ConfigError("prices.toml cannot be loaded: <ErrorClass>")`;
        - a model with no row →
          `ConfigError("<VAR> names a model with no row in prices.toml")`, with `VAR` one of
          `--model` / `EMBEDDING_MODEL` / `DEFAULT_EMBEDDING_MODEL`, never a key.
      - `.env.example`: after the extraction block, add a commented `EMBEDDING_MODEL=`
        (optional; empty = `openai/text-embedding-3-small`; the model must have a row in
        `backend/app/llm/prices.toml`; indexing runs whenever `OPENROUTER_API_KEY` is set).
      - Tests first:
        - `tests/retrieval/test_config.py`:
          - `test_empty_or_unset_uses_default` (unset and `EMBEDDING_MODEL=""`);
          - `test_unpriced_embedding_model_names_the_variable` (message contains
            `EMBEDDING_MODEL` and not the key sentinel);
          - `test_model_option_overrides`;
          - `test_no_key_disables`.

          Every settings object in these tests is built with `_env_file=None` (or after
          `monkeypatch.chdir(tmp_path)`), because `backend/.env` may hold a real key (see
          Risks).
        - `tests/test_env_example.py`: add `_RETRIEVAL_FIELD_TO_VARIABLE` (inherited fields
          plus `embedding_model → EMBEDDING_MODEL`), then
          `test_every_retrieval_setting_field_has_its_variable_covered` and
          `test_every_retrieval_setting_is_an_empty_placeholder`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_config.py tests/test_env_example.py`
- [x] 8. **Retrieval tracing (AC18 base).**
      - `app/llm/tracing.py::make_client(tracing) -> Langfuse | None`.
      - `app/retrieval/tracing.py` defines `RetrievalTracer` (Protocol) with:
        - `embedding(*, model, texts, input_tokens, cost_usd)`;
        - `search(*, query, mode, model, filters, ids_by_mode: dict[str, list[int]], failed_legs)`;
        - `flush()`.
      - `NullTracer` / `NULL_TRACER` does nothing.
      - `LangfuseTracer(client)`:
        - an embedding call → `client.start_observation(name="embedding", as_type="embedding", model=…, input=texts, usage_details={"input": n}, cost_details={"input": c}).end()`.
          `embedding` is Langfuse's generation-like type (model, usage and cost, as for a
          generation).
        - a search → `start_observation(name="retrieval-search", as_type="retriever", input={"query","mode","filters"}, output=ids_by_mode, metadata={"model","failed_legs"}).end()`.
      - `make_tracer(tracing: TracingConfig | None) -> RetrievalTracer`:
        - `None` → logs a warning once, "retrieval tracing disabled: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set",
          and returns `NULL_TRACER`;
        - a construction error → logs the error class and returns `NULL_TRACER`.
      - `tests/retrieval/fakes.py::RecordingTracer` records the calls.
      - Tests first, in `tests/retrieval/test_tracing.py`:
        - `test_langfuse_tracer_records_embedding_generation_offline` (a real `Langfuse`
          with host `http://127.0.0.1:9`, as `test_handler_built_offline` does: no exception,
          and `flush()` returns);
        - `test_no_tracing_logs_once` (`make_tracer(None)` → one warning, and calling the
          null tracer does not log again).

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_tracing.py tests/extraction/test_tracing.py`
- [x] 9. **Store and indexing service (AC7, AC8, AC18).**
      - `app/retrieval/store.py`:
        - `PostToEmbed(x_id, text, first_fetched_at)`;
        - `next_unembedded(session, model, now, retry_after=timedelta(minutes=10))`: the
          oldest (`created_at, x_id`) tweet with no row for the model, or with a `failed`
          row whose `updated_at <= now - retry_after`;
        - `posts_missing(session, model)`: every tweet without an `embedded` row, failed
          ones included, oldest first; used by the CLI;
        - `save_embedded(...)` and `save_failed(...)` as upserts on (tweet_x_id, model), via
          `sqlalchemy.dialects.postgresql.insert … on_conflict_do_update`;
        - `embedding_status(engine, models)`, used in step 12.
      - `app/retrieval/indexing.py`:
        - `IndexingRuntime(model, make_embedder: Callable[[], Embedder], prices, tracing: TracingConfig | None, clock: Clock | None = None)`.
        - `with_retries(fn, clock, stop_event, attempts=3, backoff=(2.0, 4.0)) -> RetryOutcome`,
          stop-aware as in extraction.
        - `traced_embed(embedder, texts, tracer, prices) -> tuple[EmbeddingResult, float | None]`:
          computes the cost via `compute_cost(model, tokens, None, prices)` and calls
          `tracer.embedding`.
        - `embed_post(engine, runtime, embedder, post, tracer, clock, stop_event, record_latency) -> IndexOutcome | None`:
          - `None` when stopped;
          - on success it stores `dimensions = len(vector)`, tokens, cost,
            `latency = now - first_fetched_at` (only when `record_latency`), `attempts`, and
            `updated_at = now`;
          - after the last failed attempt it stores `failed` with `type(exc).__name__` and
            logs `"embedding of a post failed: %s"` with the class only.
        - `index_missing(engine, runtime, embedder, tracer, clock, stop_event) -> IndexSummary(embedded, failed, cost_usd)`
          for the CLI and the evaluation.
      - Tests first, in `tests/retrieval/test_indexing.py` (DB, `FakeEmbedder`, a fixed
        clock):
        - `test_embed_post_stores_model_dimensions_tokens_cost_latency` (latency = clock −
          `first_fetched_at`; cost = 5 tokens × 0.02 / 1e6);
        - `test_oldest_first`;
        - `test_three_attempts_then_failed_with_error_class` (sleeps 2, 4);
        - `test_failed_post_not_retried_before_10_minutes` (at +9 min `next_unembedded` is
          `None`, at +10 min it is the post);
        - `test_success_after_failure_replaces_the_failed_row`;
        - `test_other_model_rows_untouched`;
        - `test_embedding_call_traced_with_model_tokens_cost` (`RecordingTracer`).

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_indexing.py`
- [x] 10. **Indexing loop (AC7, AC8).** `app/retrieval/loop.py::IndexingLoop` and
      `start_indexer(engine, runtime, stop_event, clock=None) -> Thread` (name `"indexer"`,
      daemon), a mirror of `ExtractionLoop`:
      - one `make_tracer(runtime.tracing)` at start and `flush` in `finally`;
      - an embedder built once;
      - an idle sleep of 2 s;
      - an iteration error → a logged class and a 30 s sleep;
      - `record_latency=True`.

      Tests first, in `tests/retrieval/test_loop.py` (DB, real thread):
      - `test_new_post_embedded_without_restart` (start the loop on an empty table, insert a
        tweet, wait ≤ 5 s for its `embedded` row);
      - `test_failure_does_not_stop_the_loop` (the first post's embedder always fails:
        `failed` row; the second post still gets `embedded`);
      - `test_stops_on_stop_event`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_loop.py`
- [x] 11. **Worker wiring (AC6, AC8, AC9).** `app/worker/cli.py`:
      - `WorkerDeps.indexing: IndexingRuntime | None = None`.
      - `_deps_from_settings` calls `load_retrieval_settings()` and `resolve_embedding()`.
        A `ConfigError` → `fail()` with exit 1. On success it builds
        `IndexingRuntime(model, make_embedder=lambda: build_embedder(config), prices, tracing=resolve_tracing(retrieval_settings))`.
      - `run()`:
        - no indexing → `logger.info("retrieval indexing disabled")`;
        - otherwise `start_indexer(...)` and
          `logger.info("retrieval indexing started: model=%s", model)`;
        - join the thread within the shared 5 s deadline in `finally`.
      - Tests first, in `tests/worker/test_cli.py`:
        - add `EMBEDDING_MODEL` to the `EXTRACTION_VARIABLE` fixture regex;
        - `test_run_without_key_logs_retrieval_indexing_disabled_once`;
        - `test_run_with_indexing_logs_model_and_embeds_new_post` (injected runtime with
          `FakeEmbedder`, a seeded tweet → an `embedded` row, and the log has the model);
        - `test_failing_embedder_does_not_stop_polls_or_extraction`, modelled on
          `test_polls_continue_while_extraction_blocks`: `FakeSource`, fake extraction, an
          embedder that always raises. Assert that polls continue (≥ 2 `tweet_poll` rows),
          extraction rows are stored, `post_embedding` rows are `failed`, and the exit code
          is 0 on SIGTERM;
        - `test_worker_rejects_unpriced_embedding_model_at_start` (`EMBEDDING_MODEL=x/unknown`
          plus a key → exit 1, stderr names `EMBEDDING_MODEL`, no key sentinel);
        - `test_deps_enable_indexing_with_key`.

        The two tests that go through `_deps_from_settings` follow
        `test_deps_enable_extraction_with_key`: `monkeypatch.chdir(tmp_path)` and `Settings`
        patched with `_env_file=None`, so `backend/.env` is never read. The indexing
        runtime they build must not call `make_embedder` (the real embedder may be
        constructed, never used).

      Automatic verification: `cd backend && uv run pytest -q tests/worker/test_cli.py`
- [x] 12. **CLI `index` and `status` (AC10, AC17, AC18).**
      - `app/retrieval/cli.py` (a typer app, `prog_name="python -m app.retrieval"`) and
        `app/retrieval/__main__.py`.
      - `RetrievalCliDeps(engine: Engine | None, settings: RetrievalSettings, make_embedder: Callable[[str | None], Embedder], clock: Clock, make_tracer: Callable[[], RetrievalTracer], make_chat_model: Callable[[str], BaseChatModel] | None = None)`,
        injected through `ctx.obj`. `make_chat_model` is used from step 19.
      - `index [--model M]`:
        - `index_missing` over `posts_missing`, with `record_latency=False`;
        - prints `embedded: N`, `failed: N` and `total cost: $X.XXXXXX` (`n/a` when no cost
          is known);
        - never touches the rows of other models.
      - `status [--model M]` prints `posts: N`, then per model (every model in
        `post_embedding` plus the configured one) `model <id>: embedded N  missing N  failed N`
        (embedded + missing + failed = posts), then
        `latest embedding: <UTC time> x_id=… model=… latency=…s` (or `never`), then
        `total embedding cost: X.XXXXXX USD`.
      - Tests first, in `tests/retrieval/test_cli.py` (CliRunner, DB, `FakeEmbedder`):
        - `test_index_embeds_missing_and_prints_counts_and_cost`;
        - `test_index_second_run_embeds_nothing`;
        - `test_index_model_option_leaves_other_model_untouched`;
        - `test_index_counts_failed_posts`;
        - `test_status_prints_counts_latest_and_cost`;
        - `test_index_without_langfuse_logs_once` (3 posts, one "retrieval tracing
          disabled" warning);
        - `test_index_without_key_fails_naming_the_variable` (`monkeypatch.chdir(tmp_path)`
          and no `OPENROUTER_API_KEY` in the environment, so `backend/.env` is not read).

        Every other CLI test injects `RetrievalCliDeps` through `ctx.obj` with a
        `FakeEmbedder`; no test lets `get_deps` build deps from `backend/.env`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_cli.py -k "index or status" && uv run python -m app.retrieval --help && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`

### Group 3 — Search

- [x] 13. **Fusion and result types (AC13, AC14).** `app/retrieval/search.py`:
      - `Mode`, `SearchFilters`, `SearchResult`, `SearchResponse`, and
        `SearchError(CollectorError)` with its subclass `NoEmbeddingsError`;
      - `fuse(rankings, k)`: RRF `Σ 1/(k + rank)` over the legs, ranks 1-based; order by
        score desc, then best rank asc, then `x_id` desc.

      Tests first, in `tests/retrieval/test_fusion.py`:
      - `test_fuse_matches_hand_computed_rrf` (e.g. fulltext `[1, 2]`, vector `[3, 2, 1]`,
        k=60 → 1: 1/61+1/63, 2: 1/62+1/62, 3: 1/61 → order `[1, 2, 3]`);
      - `test_single_leg_post_still_appears`;
      - `test_ties_broken_by_best_rank_then_newer_id`;
      - `test_k_changes_scores`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_fusion.py`
- [x] 14. **Search legs and modes (AC11–AC15).** `search(...)` in `app/retrieval/search.py`.
      - Full-text leg (SQL `text()`): the OR-prefix tsquery from Design choices;
        `WHERE search_vector @@ q` plus the filters; order by
        `ts_rank_cd(search_vector, q) DESC, created_at DESC, x_id DESC`; `LIMIT :depth`.
      - Vector leg:
        - first check that `post_embedding` has any `embedded` row for the model; if not,
          `NoEmbeddingsError("no embeddings for model <m>; run python -m app.retrieval index --model <m>")`;
        - embed the query once (`traced_embed`, a single attempt: search is interactive);
        - `SELECT … FROM post_embedding e JOIN tweet t ON t.x_id = e.tweet_x_id WHERE e.model = :model AND e.status = 'embedded'`
          plus the filters, `ORDER BY e.embedding <=> :q, t.x_id DESC LIMIT :depth`, with
          `:q` bound as `bindparam(type_=VECTOR())`.
      - Filters: `created_at >= since`, `created_at < until`, `NOT is_repost` and
        `NOT is_reply`, applied in both legs before the depth.
      - Modes:
        - `fulltext`/`vector`: that leg alone, scores via `fuse` of one ranking;
        - `hybrid`: both legs, then `fuse`, then the `limit`. When the query embedding
          raises (not `NoEmbeddingsError`): `logger.error("vector leg failed: %s", ErrorClass)`,
          `failed_legs=("vector",)`, and full-text results only. In `vector` mode the
          embedding error becomes `SearchError("query embedding failed: <ErrorClass>")`.
      - `ranks` always has both keys, `None` where a leg did not return the post or did not
        run.
      - Every call ends with `tracer.search(...)`, with the ids per leg and the fused ids.
      - Tests first, in `tests/retrieval/test_search.py` (DB; tweets inserted through
        `store_posts`; embeddings through `save_embedded` with explicit 3-d vectors;
        `FakeEmbedder` with the query vectors):
        - `test_fulltext_injured_finds_injury`;
        - `test_fulltext_odegaard_finds_accented`;
        - `test_fulltext_stop_words_only_returns_nothing`;
        - `test_vector_orders_by_cosine_within_model`, where a post embedded only under
          another model is absent;
        - `test_vector_without_embeddings_errors_clearly`;
        - `test_hybrid_order_equals_hand_computed_rrf`: assert both legs' ranks first, then
          the fused order computed by hand in the test;
        - `test_k_and_depth_overridable` (depth=1 cuts a leg; k changes the scores);
        - `test_filters_limit_window_reposts_replies`;
        - `test_result_fields_and_ranks`;
        - `test_hybrid_degrades_to_fulltext_when_embedding_fails` (caplog has the class);
        - `test_vector_fails_clearly_when_embedding_fails`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_search.py tests/retrieval/test_fusion.py`
- [x] 15. **CLI `search` and search tracing (AC16, AC18).**
      - `search QUERY [--mode hybrid|fulltext|vector] [--limit 10] [--since] [--until] [--exclude-reposts] [--exclude-replies] [--model] [--k 60] [--depth 50]`.
      - `--since`/`--until` accept `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM`, read as
        `Europe/Warsaw` (`zoneinfo`) and converted to UTC; an explicit offset is honoured.
      - The output is one line per result:
        `<n>. <score:.4f>  fts=<rank|->  vec=<rank|->  @author  <created_at in Warsaw, YYYY-MM-DD HH:MM>  <x_id>`,
        then the text indented. A header names the mode, the model and the window in
        Warsaw time; a failed vector leg adds `vector leg failed — full-text only`.
      - `SearchError` → `fail(message)`, exit 1.
      - `--mode fulltext` needs no `OPENROUTER_API_KEY` and never builds an embedder (AC5).
        `vector` and `hybrid` without the key →
        `fail("OPENROUTER_API_KEY is not set; use --mode fulltext or set the key")`, exit 1.
      - Tests first, in `tests/retrieval/test_cli.py`:
        - `test_search_prints_ranked_results_with_ranks`;
        - `test_search_time_filters_in_warsaw_compared_in_utc`: a post at 22:30 UTC on
          2026-09-30 is 00:30 Warsaw on 2026-10-01, so `--since 2026-10-01` keeps it and
          `--until 2026-10-01` drops it; the output shows `2026-10-01 00:30`;
        - `test_search_traced_with_query_mode_and_ids` (`RecordingTracer`);
        - `test_vector_search_without_embeddings_fails_with_hint`;
        - `test_fulltext_search_needs_no_key` (deps with no key and a `make_embedder` that
          raises if called: fulltext succeeds; `--mode hybrid` exits 1 naming
          `OPENROUTER_API_KEY`).

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_cli.py -k search && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`

### Group 4 — Evaluation tooling, set v1 and documents

- [x] 16. **Dataset models and `export-corpus` (AC19).**
      - `app/retrieval/evaluation/dataset.py`: pydantic `CorpusPost`, `Judgement`
        (`x_id: int`, `relevant: bool`, `reviewed: bool`, `labelled_by: str`) and `Query`
        (see Approach), all `extra="forbid"`.
      - `load_corpus/write_corpus` and `load_queries/write_queries`: JSONL with an atomic
        write (tmp file + `os.replace`).
      - Default paths: `EVALS_DIR = backend/evals/retrieval`, `v1/corpus.jsonl`,
        `v1/queries.jsonl`, `results/`.
      - The command `export-corpus [--output]` reads every `tweet` of the database, oldest
        first, and writes only the six public fields; `raw` and `source` never leave.
      - Tests first, in `tests/retrieval/evaluation/test_dataset.py`:
        - `test_export_corpus_writes_public_fields_only`;
        - `test_roundtrip_and_atomic_write`;
        - `test_query_rejects_unknown_fields`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/evaluation/test_dataset.py`
- [x] 17. **Isolated evaluation schema (AC19).** `app/retrieval/evaluation/schema.py`:
      - `EVAL_SCHEMA = "retrieval_eval"`.
      - `make_eval_engine(engine) -> Engine`: `create_engine(engine.url, pool_pre_ping=True, hide_parameters=True, connect_args={"options": "-c timezone=UTC -c search_path=retrieval_eval,public"})`.
      - `load_eval_corpus(engine, corpus) -> Engine`:
        1. `DROP SCHEMA IF EXISTS retrieval_eval CASCADE; CREATE SCHEMA retrieval_eval`;
        2. `SQLModel.metadata.create_all(conn.execution_options(schema_translate_map={None: EVAL_SCHEMA}), tables=[Tweet.__table__, PostEmbedding.__table__])`;
        3. insert the corpus (`first_fetched_at = created_at`, `source = "eval-corpus"`,
           `raw = {}`);
        4. return the eval engine.
      - Tests first, in `tests/retrieval/evaluation/test_schema.py` (the DB fixture; drop the
        schema in teardown, because the `db` fixture truncates `public` only):
        `test_eval_schema_never_touches_public_tweet` seeds one public tweet "Saka injury",
        loads a 3-post corpus, then:
        - a full-text search on the eval engine returns only corpus ids;
        - `index_missing` on the eval engine writes `retrieval_eval.post_embedding`;
        - `public.tweet` still holds exactly the seeded row;
        - `public.post_embedding` is empty.

        `test_reload_replaces_previous_corpus`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/evaluation/test_schema.py`
- [x] 18. **Metrics (AC24, AC23 base).** `app/retrieval/evaluation/metrics.py`:
      - `recall_at_k(ranked, relevant, k)`;
      - `reciprocal_rank(ranked, relevant)`, which is 0 when none is found;
      - `aggregate(per_query, slices)`: mean recall@5, recall@10 and MRR per mode for
        `all`, `en` and `pl`, with query counts. Queries with no relevant post are excluded
        and counted as `queries_without_relevant`.

      Tests first, in `tests/retrieval/evaluation/test_metrics.py` (hand-computed):
      - `test_no_relevant_in_top_k` (recall@5 = 0, RR = 0);
      - `test_first_relevant_at_rank_3` (RR = 1/3, recall@5 = 1);
      - `test_several_relevant` (3 relevant, 2 in the top 5, 3 in the top 10 → 2/3 and 1);
      - `test_aggregate_slices_and_excludes_empty`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/evaluation/test_metrics.py`
- [x] 19. **Query-set builder `build-queries` (AC20).**
      - `app/retrieval/evaluation/llm.py`:
        - `DEFAULT_LABEL_MODEL = "openai/gpt-6-luna"`;
        - `build_label_model(api_key, model)` → `ChatOpenRouter(model, api_key, timeout=60_000, max_retries=0, reasoning={"effort": "none"})`
          with the SDK retry config off;
        - `usage_cost(message, model, prices)`, which reads `usage_metadata`.
      - `app/retrieval/evaluation/queries.py`:
        - `current_events(engine, x_ids)`: own SQL, the latest `extracted` extraction per
          tweet → `extraction_event`, left join `player` on (season, fpl_id) for
          `web_name`; the name falls back to `mention`.
        - `build_queries(corpus, events, write_query: Callable[[CorpusPost, str], str], counts, seed) -> list[Query]`:
          - event queries: distinct (player, event_type) pairs, templated from
            `app/content/retrieval_query_templates.toml` (`[en]` / `[pl]` tables keyed by
            event type, e.g. `out = "{player} injury"`, `pl.out = "{player} kontuzja"`);
          - post queries: seeded sampling of non-repost posts with ≥ 40 characters, written
            by the model through the prompt `app/content/prompts/retrieval_query.md`
            (`version: 1`; one short search query in the requested language, not copying
            the post; structured output `WrittenQuery(query: str)`, `function_calling`),
            with `source_x_id` set;
          - a shortfall of event queries is filled with post queries of the same language;
          - default counts: event en 15, event pl 5, post en 15, post pl 5;
          - split stratified per (language, origin): in id order, the first `round(0.3·n)`
            are `dev`;
          - ids `q-<origin>-<lang>-NNN`.
        - LLM calls go through `with_retries`, and one failed post is skipped and counted.
      - `build-queries [--corpus] [--output] [--event-en 15] [--event-pl 5] [--post-en 15] [--post-pl 5] [--seed 6] [--model]`
        refuses to overwrite an existing output without `--force` and prints the counts and
        the total cost.
      - Tests first, in `tests/retrieval/evaluation/test_queries.py` (DB seeded with
        tweets, extraction, events and players; `FakeChatModel` from
        `tests/extraction/fakes.py`):
        - `test_builder_writes_counts_by_language_and_origin`;
        - `test_event_queries_templated_from_current_events` (the latest extraction wins;
          `web_name` is used);
        - `test_split_is_stratified_and_deterministic`;
        - `test_shortfall_filled_with_post_queries`;
        - `test_label_model_default_matches_extraction_default`
          (`DEFAULT_LABEL_MODEL == app.extraction.config.DEFAULT_MODEL`).

        `tests/content/test_prompts.py` asserts that the two new prompts load, and
        `tests/retrieval/evaluation/test_templates.py` asserts that every event type has an
        `en` and a `pl` template with `{player}`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/evaluation/test_queries.py tests/retrieval/evaluation/test_templates.py tests/content/test_prompts.py`
- [x] 20. **Pooling and pre-labels `prelabel` (AC21).** `app/retrieval/evaluation/labelling.py`:
      - `pool(eval_engine, query, embedder, tracer) -> list[int]`: the union, in first-seen
        order, of the top 10 of `fulltext`, `vector` and `hybrid` (default k and depth),
        plus `source_x_id` for `post` queries.
      - `label(chat_model, query, post) -> bool`: prompt
        `app/content/prompts/retrieval_relevance.md` (`version: 1`; is this post relevant
        to the query, meaning it reports the news the query asks about) and structured
        `RelevanceLabel(relevant: bool)`.
      - `prelabel(...)`:
        1. load the corpus into the eval schema and index it for the embedding model;
        2. for each query that has no judgements yet, pool the candidates and label each
           one, giving `Judgement(reviewed=False, labelled_by=<model>)`;
        3. write the file after every query, so a run can be resumed.
      - A failed label after its attempts is left out and counted.
      - The command `prelabel [--queries] [--corpus] [--model] [--embedding-model]` prints
        the queries labelled, the candidates, the relevant count, the failures and the total
        cost (chat + embeddings).
      - Tests first, in `tests/retrieval/evaluation/test_labelling.py`:
        - `test_pools_top10_of_each_mode_and_prelabels_unreviewed`;
        - `test_source_post_joins_the_pool`;
        - `test_prelabel_resumes_and_skips_labelled_queries`;
        - `test_prelabel_never_touches_public_tweet`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/evaluation/test_labelling.py`
- [ ] 21. **Review command (AC22).** `review [--queries] [--corpus] [--split dev|test]`
      needs no database or key and never builds deps. It walks the queries with unreviewed
      judgements in file order. Per judgement it shows:
      - a progress line;
      - the query id, language, origin and text;
      - the post: `@author`, created at in Warsaw time, `x_id`, text;
      - the model's label.

      Actions: `[a]ccept  [f]lip  [s]kip  [+] add post  [n]ext query  [q]uit`.
      - accept → `reviewed=True`;
      - flip → `relevant = not relevant, reviewed=True`;
      - add → prompts for an X ID that must be in the corpus, and appends
        `Judgement(relevant=True, reviewed=True, labelled_by="owner")`, replacing an existing
        judgement of that id;
      - every accept, flip and add writes the file (atomic);
      - `typer.Abort`/`KeyboardInterrupt` → a summary and exit 130.

      Tests first, in `tests/retrieval/test_cli.py` (CliRunner `input=`):
      - `test_review_accept_flip_skip_add_and_saves_after_each`: the file on disk is checked
        after a `q` mid-run;
      - `test_review_add_rejects_unknown_id`;
      - `test_review_nothing_to_review`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_cli.py -k review`
- [ ] 22. **Runner `evaluate` (AC23).** `app/retrieval/evaluation/runner.py` and the command
      `evaluate --split dev|test [--include-unreviewed] [--embedding-model] [--k 60] [--depth 50] [--limit 10] [--run-name] [--queries] [--corpus] [--output-dir]`.
      1. Load the corpus into the eval schema and index it.
      2. Embed every query once (cached in the runner), with `with_retries`.
      3. Run the three modes with `limit` results. A failed vector leg stops the run with an
         error naming the query, and no file is written.
      4. Compute the relevant sets from the reviewed judgements only (all of them with
         `--include-unreviewed`). With no evaluable query, fail with
         "no reviewed labels in the <split> split; run review or pass --include-unreviewed".
      5. Print a table of mode × slice (recall@5, recall@10, MRR, n).
      6. Write `<output-dir>/<run-name>.json` with `run_name`, `embedding_model`, `k`,
         `depth`, `limit`, `split`, `include_unreviewed`, `date` (UTC ISO), `metrics` and
         `queries` (per query: id, language, origin, the relevant ids, and per mode the
         retrieved ids and the rank of each relevant id or `null`).

      Tracing (AC18): the runner takes the tracer from `RetrievalCliDeps.make_tracer`, passes
      it to `index_missing` and to every `search(...)` call (so each evaluation search is
      traced with the query, the mode and the ids per leg), embeds the queries through
      `traced_embed`, and flushes the tracer at the end of the run. `prelabel` (step 20) does
      the same through `pool(...)`.

      The default output dir is `evals/retrieval/results/`, and `dev` runs go to
      `results/dev/`. Add `backend/evals/retrieval/results/dev/` to `.gitignore` and
      `backend/evals/retrieval/results/.gitkeep`.

      Tests first, in `tests/retrieval/evaluation/test_runner.py` (DB, fake embedder with
      hand-set vectors, a 4-post corpus and 2 queries, one en and one pl):
      - `test_runner_reports_metrics_per_mode_and_slice`;
      - `test_only_reviewed_labels_unless_flag`;
      - `test_result_file_fields`;
      - `test_vector_leg_failure_stops_without_file`;
      - `test_runner_traces_searches_and_embeddings` (`RecordingTracer`: one `search` record
        per query × mode with the query text, the mode and the ids per leg, and one
        `embedding` record per query embedding).

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/evaluation/test_runner.py && cd .. && git check-ignore -q backend/evals/retrieval/results/dev/x.json`
- [ ] 23. **Documents (AC26, AC27).**
      - `docs/DEPLOYMENT.md`: a new item after 8, "Retrieval indexing". It says:
        - indexing runs whenever `OPENROUTER_API_KEY` is set, and without it the worker
          logs `retrieval indexing disabled`;
        - `EMBEDDING_MODEL` is optional; empty means `openai/text-embedding-3-small`, and
          a model with no row in `backend/app/llm/prices.toml` fails the worker on start;
        - migration 0005 enables `vector`/`unaccent` through the pre-deploy;
        - the backfill is `python -m app.retrieval index`, and `status` and `search` exist.
      - `docs/BACKLOG.md`: take the next free numbers (#16, #17 unless taken) for these
        entries:
        - reranking with a cross-encoder or an LLM (P2; trigger: the item 5 report shows
          hybrid top-5 precision below what alerts need);
        - an ANN index, HNSW/IVFFlat (P3; trigger: vector search p95 > 200 ms).
      - `docs/DECISIONS.md`: check rows 51–55 against what was built, and edit in place:
        - row 52: add "query lexemes are OR-ed prefix matches, so `injured` finds `injury`";
        - row 55: "the corpus is loaded into its own schema `retrieval_eval`, never the
          live `tweet` table".
      - Tests first:
        - `tests/test_readme.py::test_deployment_documents_embedding_model`: `EMBEDDING_MODEL`,
          `retrieval indexing disabled` and `app.retrieval index` all appear in DEPLOYMENT;
        - `tests/test_docs.py::test_backlog_has_reranking_and_ann_entries`: BACKLOG rows
          mention "rerank" and "HNSW".

      Automatic verification: `cd backend && uv run pytest -q tests/test_readme.py tests/test_docs.py tests/test_env_example.py`
- [ ] 24. **Generate and commit set v1 (AC25).**
      - Test first: `tests/retrieval/evaluation/test_eval_set.py::test_set_v1_committed_and_consistent`
        checks:
        - both files exist;
        - the corpus has ≥ 100 posts and unique ids;
        - there are 35–45 queries, 8–12 of them `pl`;
        - both origins are present, and both splits in each language;
        - every query has ≥ 1 judgement;
        - every judged `x_id` is in the corpus;
        - all judgements are `reviewed=false` with `labelled_by` a model id.
      - Precondition: `backend/.env` sets `OPENROUTER_API_KEY` (check that it is set without
        printing it), and the Compose `db` is up with the development posts. If either is
        missing → `RESULT: ESCALATE`, and the owner runs the commands below.
      - Run:
        1. `docker compose up -d db`;
        2. `cd backend && uv run alembic upgrade head` (development database on localhost
           only);
        3. `uv run python -m app.retrieval export-corpus`;
        4. `uv run python -m app.retrieval build-queries`;
        5. `uv run python -m app.retrieval prelabel`.
      - Spend guard: add up the `total cost` lines. Expected < $0.20; at > $1 stop and
        escalate.
      - Commit `backend/evals/retrieval/v1/corpus.jsonl` and `queries.jsonl`.
      - Smoke: `uv run python -m app.retrieval evaluate --split dev --include-unreviewed`
        (the result goes to the ignored `results/dev/`).
      - Tick Stage 2 item 1 in `docs/ROADMAP.md`.

      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/evaluation/test_eval_set.py && test -z "$(git status --porcelain evals/retrieval/results/)" && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`

## Risks and traps

- **AC2 and the catalogue test.** `tests/extraction/test_model_settings.py` asserts
  `set(load_model_settings()) == set(load_prices())`. AC3/AC9 put the embedding model in the
  same catalogue, so that assertion must narrow to chat rows (those with an output price).
  Its intent (every chat model has request settings) stays. This is the only extraction-test
  change beyond import paths. `tests/db/test_migrations.py` (not an extraction, tweet or
  worker test) also changes, because head moves.
- **Stemming.** `injured` → `injur` and `injury` → `injuri` (probed). Only the prefix query
  makes AC11 pass. Prefix matching also widens matches (`sak:*`). `out` is a stop word, so
  "Saka out" searches `saka` only; the vector leg covers the meaning.
- **`unaccent` in a generated column.** Only through the text-search configuration. The
  function is not immutable, and `GENERATED` rejects it.
- **Alembic comparison** (`test_models_match_migration`). The `VECTOR` user type, the
  `Computed` column and the GIN index must compare clean. `pgvector.sqlalchemy` registers
  `vector` in `ischema_names`. If a spurious diff remains, align the model to the reflected
  form, e.g. `postgresql_using` and the exact `Computed` text as Postgres renders it. Do not
  weaken the test; escalate if it cannot be aligned.
- **`table_contents` and older revisions.** It selects every metadata column, so a snapshot
  taken before 0005 must exclude `search_vector`. The per-revision migration tests must
  exclude `post_embedding`.
- **Extensions need privileges.** The test container and the Compose/Railway `postgres` user
  are superusers, and the production image is the same as the test one (DEPLOYMENT).
- **The eval schema and the `db` fixture.** The fixture truncates only `public`. Evaluation
  tests must drop `retrieval_eval` in teardown, or tests leak state into each other.
- **Warsaw time.** `zoneinfo` needs the system tz database. Debian bookworm images ship
  `tzdata` (Priority: required). If `ZoneInfo("Europe/Warsaw")` fails anywhere, adding the
  `tzdata` package is a new dependency → escalate.
- **SDK retries.** Both the `openrouter` client and `ChatOpenRouter` retry 5XX on their own
  for up to an hour unless `retry_config` is overridden (see `providers.py`). The embedder
  and the label model must switch it off.
- **Paid calls.** AC28 keeps tests offline. Set v1 generation (step 24) and E2E index/search
  are real calls from the development machine, like spec 005's dev runs, with a hard cap of
  $1. The corpus holds public posts only (DECISIONS row 47 accepts that for the extraction
  set); `raw` and `source` are not exported.
- **`backend/.env` holds a real key.** Settings read `.env` from the current directory, and
  step 24 requires `OPENROUTER_API_KEY` in `backend/.env`. A test that builds settings from
  the environment while running in `backend/` would construct the real embedder and could
  call OpenRouter (AC28). Every test that loads settings uses `_env_file=None` or
  `monkeypatch.chdir(tmp_path)`, as the existing worker and extraction tests do; all other
  tests inject deps with `FakeEmbedder` / `FakeChatModel`.
- **Worker log noise.** Extraction already warns "langfuse tracing disabled". Retrieval uses
  its own text "retrieval tracing disabled", so `test_run_without_langfuse_warns_once` keeps
  counting 1.
- **Thread shutdown.** The indexer must join within the existing shared 5 s deadline. The
  SIGTERM tests (`test_sigterm_*`) stay green only if `IndexingLoop` sleeps through
  `StopAwareClock`.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

1. `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
   is green.
2. `docker compose up -d db && cd backend && uv run alembic upgrade head`: the development
   database reaches `0005`, and
   `uv run alembic current` shows `0005 (head)`.
3. `uv run python -m app.retrieval --help`, `index --help`, `search --help`,
   `status --help`, `export-corpus --help`, `build-queries --help`, `prelabel --help`,
   `review --help` and `evaluate --help` all exit 0.
4. `uv run python -m app.retrieval status` → `posts: <N ≈ 210>`, and the default model has
   `embedded 0  missing N  failed 0` (before indexing).
5. `uv run python -m app.retrieval search "injury" --mode fulltext --limit 5` → ranked
   posts with `fts=` ranks and `vec=-`, with no network call.
6. With the key in `backend/.env`:
   - `uv run python -m app.retrieval index` → `embedded: N`, `failed: 0` and a total cost
     under $0.01;
   - a second `index` → `embedded: 0`;
   - `status` → `missing 0` and a latest embedding;
   - `search "Saka kontuzja" --mode hybrid` and `search "Odegaard injured" --mode hybrid`
     → results carrying both `fts=` and `vec=` ranks.
7. Step 24's commands and the `evaluate --split dev --include-unreviewed` smoke run: the
   printed table has three modes × {all, en, pl}.

Record the outputs (counts, costs) under Deviations or the step notes.

### Manual (performed by the owner)

1. Run the worker locally with the real `OPENROUTER_API_KEY` and Langfuse keys. The log
   shows `retrieval indexing started: model=openai/text-embedding-3-small`. A newly polled
   post gets an `embedded` row within seconds (`python -m app.retrieval status`). In
   Langfuse, an `embedding` observation carries the model, tokens and cost, and a CLI
   `search` shows a `retrieval-search` observation with ids per mode.
2. After the PR: `python -m app.retrieval review` over set v1. This is not required for this
   spec's done; it feeds roadmap item 5.

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
      fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated (Stage 2 item 1 ticked); `docs/DECISIONS.md` rows 52 and 55
      amended; `docs/BACKLOG.md` entries added; `docs/DEPLOYMENT.md` and `.env.example`
      updated
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation: date, stage, question, decision)_

## Review log

**2026-09-30 — /pipeline:plan-review**

Anti-anchoring leads (from the SPEC alone): `unaccent` is not immutable, so a generated
column needs a wrapper or a text-search configuration; an untyped `vector` column for
several models; the stemmer may not join `injured`/`injury`; the evaluation needs isolation
from the live `tweet`; tests must stay offline although a real key exists locally. The plan
handles the first four (probed); the last one was a gap (F3).

Findings (severity counted before the fixes):

| id | severity | finding | change |
|----|----------|---------|--------|
| F1 | `major` | The automatic verification of steps 3, 12, 15 and 24 chains `cd backend` twice in one shell, so the second `cd` fails and the step can never go green; step 3's `-k "prices or deps or model"` also applied to `tests/llm` and deselected its proving tests (`test_input_only_row_loads_and_costs_input_alone`, `test_chat_rows_keep_their_cost`) | one `cd backend` per command; the `-k` filter applies to `tests/worker/test_cli.py` only |
| F2 | `major` | AC18 requires every evaluation search to be traced; step 22 (runner) passed no tracer and had no test, so the matrix covered only CLI searches | step 22 passes the tracer to `index_missing`, every `search`, and `traced_embed` for queries; new `test_runner_traces_searches_and_embeddings`; matrix AC18 → steps 8, 12, 15, 22 |
| F3 | `major` | `backend/.env` holds a real `OPENROUTER_API_KEY` (step 24 needs it) and settings read `.env` from the working directory; tests that build settings from the environment (steps 7, 11, 12) would construct the real embedder and could reach OpenRouter, breaking AC28 | new Risks entry; steps 7, 11 and 12 require `_env_file=None` / `monkeypatch.chdir(tmp_path)` as the existing worker tests do, and deps injection elsewhere |
| F4 | `minor` | Step 24's `git status --porcelain evals/retrieval/results/` only prints, so it can never fail | `test -z "$(git status --porcelain …)"` |
| F5 | `minor` | The `search` CLI's behaviour without a key was undefined, though AC5 says full-text needs no embedding call and E2E step 5 runs it that way | step 15: `fulltext` never builds an embedder; `vector`/`hybrid` without the key fail naming `OPENROUTER_API_KEY`; new `test_fulltext_search_needs_no_key`, added to the AC5 row |
| F6 | `minor` | `app/worker/loop.py` has its own duplicate `Clock` Protocol, which step 1 did not mention | step 1 drops it and imports `Clock` from `app.core.clock` |

Checked and found correct (later stages need not repeat it):

- **Coverage:** every AC 1–28 has steps and named proving tests; the matrix matches the
  steps; the fourth column is present (empty, or `n/a` for AC2 and AC28 with a reason).
- **Code facts verified:** the moved names and their importers (`Clock`/`StopAwareClock`
  in `app/tweets/loop.py`, `SystemClock` in `app/worker/loop.py`, `TracingConfig` and
  `resolve_tracing` in `app/extraction/config.py`, `ExtractionSettings` in
  `app/core/settings.py`); the patched path `app.extraction.service.load_prices` and
  `app.extraction.loop.make_handler` stay valid if those modules keep the names in their
  namespace; `store_posts` executes in the caller's session without committing (AC5 test
  is sound); the test DB is built by `alembic upgrade head`, so the text-search
  configuration exists for every DB test; `table_contents` iterates the metadata, which
  is why pre-0005 snapshots must exclude `search_vector` and `post_embedding`, as planned;
  `extraction_event`/`player` columns match `current_events`; `DEFAULT_MODEL` exists in
  `app/extraction/config.py`; numpy is not installed, so `pgvector` returns lists.
- **Compliance:** Polish templates and prompts in `app/content/`; fakes only in tests;
  testcontainers for DB tests; exact pins; business-module layout; UTC storage with Warsaw
  rendering; no key or handle in logs; DECISIONS rows 14, 23, 25, 28, 47 and 51–55 are
  kept, and the edits of rows 52 and 55 are planned in step 23 (AC27).
- **Interpretations accepted without escalation:** (1) AC19's "isolated database" is met by
  the dropped-and-recreated `retrieval_eval` schema: results do not depend on any live
  contents and the live `tweet` is never read or written; a throwaway container would put a
  dev-only dependency in `app/`. (2) AC2's "only import-path changes" cannot hold literally
  for `test_model_settings_and_prices_have_the_same_models`, because AC3/AC9 put the
  embedding model into the same catalogue; narrowing it to chat rows keeps its intent and
  is disclosed in the Owner summary. (3) AC28's "only network calls are the owner's manual
  CLI runs" is read as a rule for the test suite; step 24's paid runs are required by AC25,
  are capped at $1, and escalate when the key is missing.
- **Minimality:** the refactor is limited to what the SPEC names; retries and JSONL writes
  are re-implemented rather than shared, as the SPEC's decision requires.
- **Feasibility:** no forward dependencies (step 6's `build_embedder` signature is finalised
  in step 7, as stated); the migration and its downgrade are accounted for; extension
  privileges, time zones, SDK retries and thread shutdown are in Risks.
- **E2E:** the automatic part runs on the development database and CLI; the manual part
  holds only what needs the owner's Langfuse account and the label review. No UI scope.
- **Groups:** four groups, each ending at a complete, green state; `implement.chunked` is
  false.
- **Owner summary:** the dependency (`pgvector==0.5.0`, `openrouter==0.11.46`) and the
  migration flags match the plan and are accepted in SPEC → Owner decisions.
- **Language:** English throughout, as `language: en`.

Decision: the plan is ready — every AC has steps and proving tests, all findings were fixed
in the plan, and the new dependencies and the migration are accepted in the SPEC's Owner
decisions.

## Chunk notes

_(filled in by /pipeline:implement in chunk mode — one entry per chunk that ends at a group boundary)_

## Deviations

_(filled in by /pipeline:implement — every deviation from the plan with its rationale)_

## Final review

_(filled in by /pipeline:final-review)_
