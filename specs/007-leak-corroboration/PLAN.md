# PLAN 007 — Leak corroboration

## Owner summary

- **Approach:** First the shared code moves: the chat model, the model-settings catalogue, the
  structured call and the retries go to `app/llm/`, the current-extraction SQL becomes one
  query in `app/extraction/store.py`, and the deadline helpers go to `app/fpl/deadlines.py`.
  Embedding calls get a per-call timeout. Existing tests change only in their imports. Next,
  migration `0006` adds and backfills `tweet.reposted_author_handle`, and the three source
  adapters fill it. Then comes the new module `app/corroboration/`. Its pure rules cover the
  label table, independent accounts, the flags and the grade. On top of them sit the SQL
  claims, the retrieval candidates, a one-node LangGraph judge, a Langfuse trace per run and
  the `python -m app.corroboration <player>` CLI. Last, `python -m app.corroboration.evaluation`
  gets `build-cases`, `review` and `evaluate`. Set v1 is built from the development database
  and pre-labelled by `anthropic/claude-haiku-4.5`, and a first test-split run is committed.
- **Main risks:**
  - The development corpus is almost all `doubt` claims, so set v1 may lack `contradicts`
    cases. The builder also pairs posts with an older anchor of another type, and if a label
    is still missing, the implementer escalates.
  - Langfuse nesting (the retrieval observations and the judge generations under one root
    span) depends on the SDK's OpenTelemetry context. It is checked end to end against the
    real Langfuse, with an explicit `trace_context` as the fallback.
  - The X API adapter must ask for one more expansion to see a repost's original author.
    X API reposts stored before this change keep a null original author.
- **New dependency:** no.
- **Data migration:** yes. `0006` adds the nullable column `tweet.reposted_author_handle`
  and backfills it from `raw` for reposts. Accepted in SPEC → "Owner decisions".
- **Manual scenarios for the owner:** 2 — read one real corroboration (Isak) and its Langfuse
  trace and judge whether they make sense. Reviewing set v1 and setting the judge thresholds
  are the owner's post-PR step from the SPEC.

## Approach

**What the plan rests on**

- `specs/007-leak-corroboration/SPEC.md` — read in full (including `## Owner decisions`).
- `docs/CONVENTIONS.md` — read in full.
- `docs/DECISIONS.md` — searched for "extraction", "boundar", "import", "corroborat" and
  "007". The search gave:
  - the relevance rule (2026-09-28);
  - "latest successful extraction is current";
  - the evaluation-set rules;
  - OpenRouter-only and `model_settings.toml` (2026-09-29);
  - the `app/llm` row of 2026-09-29, which rejects "retrieval importing from `app.extraction`";
  - the five 007 rows of 2026-09-30 (corroboration, judge, shared chat code, refactor rule,
    repost author).
- `docs/ROADMAP.md` — searched Stage 2. The 007 item is already linked; it is ticked at the end.
- `docs/BACKLOG.md` — read the table: #18 is to be closed, #19 and #20 are already added by
  the SPEC.
- `docs/PROJECT.md` — searched FR-2.x. FR-2.2 already describes the anchor behaviour.
- `docs/DEPLOYMENT.md` — searched for "TWEET_SOURCE", "pre-deploy", "LLM_MODEL",
  "OPENROUTER" and "migration". Migrations run through the Railway pre-deploy. Section 9
  holds the retrieval notes and names `0005`, which `tests/test_readme.py` enforces.
- `docs/adr/0006-default-extraction-model-openrouter.md` — searched for "haiku":
  `claude-haiku-4.5` has test F1 0.887, 10× the default's price.
- Code read in full:
  - `app/llm/*`;
  - `app/extraction/{providers,model_settings,config,service,flow,schemas,models,store,loop,tracing,generation}.py`,
    `app/extraction/model_settings.toml`, `app/extraction/evaluation/{cases,review,runner}.py`,
    and `app/extraction/cli.py` (dependencies and the `review` command);
  - `app/retrieval/{embedder,indexing,search,config,tracing,store,models}.py` and
    `app/retrieval/evaluation/{llm,queries,dataset}.py`; `app/retrieval/cli.py` (dependencies,
    `parse_warsaw`, `search`); `app/retrieval/evaluation/runner.py` (the retry and
    `_CachedQueryEmbedder` parts);
  - `app/tweets/{models,schedule,store,ingest}.py`, `app/tweets/sources/*.py`;
  - `app/worker/schedule.py`, `app/fpl/models/reference.py`, `app/core/{settings,clock}.py`,
    `app/content/__init__.py`;
  - `migrations/versions/0003_tweets.py`, `0005_retrieval.py`;
  - `tests/conftest.py`, `tests/test_module_boundaries.py`, `tests/db/test_migrations.py`,
    `tests/extraction/fakes.py`, `tests/retrieval/{fakes,helpers}.py`, `tests/test_readme.py`,
    `tests/test_docs.py`, and the recorded tweet payloads.
- The development database was checked:
  - 210 posts from `twscrape`, 42 of them reposts, with the original author at
    `raw->'retweetedTweet'->'user'->>'username'`;
  - 210 embeddings;
  - 25 events for 17 players, almost all `doubt`;
  - deadlines GW5 2026-09-18, GW6 2026-10-10.

**Refactors (Group 1).** AC4 applies to these steps: existing test files change only in
their imports or move with `git mv`. New tests go into new files, so that
`git diff origin/main -- <existing test file>` shows import lines only.

- `app/llm/retry.py`: one `with_retries(fn, clock, stop_event, *, attempts=3,
  backoff=(2.0, 4.0), what="call") -> RetryOutcome[T]`. It merges
  `extraction/service.run_with_retries` and `retrieval/indexing.with_retries`. The stop event
  is checked before each attempt, before the sleep and after the sleep (the extraction
  variant, a superset). The back-off is indexed `min(attempt-1, len-1)`. The log line is
  `"%s attempt failed: %s", what, class name`. `extraction.service.run_with_retries` stays a
  one-line wrapper (no loop), so its call sites keep working.
- `app/llm/models.py` takes over `model_settings.py` and the model-settings TOML file,
  moved unchanged with `git mv`. `app/llm/chat.py` holds:
  - `ChatSettings(LlmSettings)` with `llm_model` and `llm_fallback_model`;
    `extraction.config.ExtractionSettings = ChatSettings` stays as a name, because
    `tests/test_env_example.py` imports it;
  - `DEFAULT_MODEL` and `DEFAULT_FALLBACK_MODEL`;
  - `LlmConfig`, `resolve_llm`, `PROVIDER`, `ChatModelSpec` and `build_chat_model` from
    `extraction/providers.py`.

  `app/extraction/providers.py` and `app/extraction/model_settings.py` are deleted. The
  choice is between moving the factory and a thin wrapper in each feature; the move wins,
  because it is the literal AC1 and the retrieval evaluation already reads the catalogue row
  of its model.
- `app/llm/structured.py`:
  - `Usage` (moved from `extraction/schemas.py`, which re-imports it), `usage_from_raw` and
    `answer_from_raw` (moved from `extraction/flow.py`);
  - `StructuredReply[T]` (`parsed`, `usage`, `cost_usd`, `answered_model`);
  - `StructuredCaller`, moved from `retrieval/evaluation/llm.py` with the same positional
    constructor `(chat_model, model, prices, clock)`, plus `structured_kwargs` and
    `stop_event` fields. It adds `call_with_usage(schema, system, human) -> StructuredReply`,
    and `call()` returns `.parsed`. `StructuredCaller.from_spec(spec, prices, clock)` builds
    one from a `ChatModelSpec`.

  `retrieval/evaluation/llm.py` keeps only `WrittenQuery`, `RelevanceLabel` and
  `DEFAULT_LABEL_MODEL` (= `app.llm.chat.DEFAULT_MODEL`). `build_label_model` is removed:
  the retrieval CLI builds its chat model with
  `build_chat_model(single_model_config(api_key, model))`. That applies the
  catalogue row of the model; the old builder hard-coded effort `none`, which is the same row
  for the default model.
- `app/extraction/store.py`:
  - One public `current_extractions(session, *, x_ids=None, created_from=None,
    created_until=None) -> list[CurrentExtraction]`, ordered by `created_at, x_id`.
    `created_until` is exclusive.
  - `CurrentExtraction` gains `created_at`, `text` and `reposted_author_handle`
    (`reposted_author_handle` is added in step 8, when the column exists). `EventRecord`
    gains `player_web_name` (a LEFT JOIN on `player`).
  - A private `_LATEST_PER_POST` SQL fragment, with an optional `status = 'extracted'`
    filter, is the only `DISTINCT ON … finished_at DESC, id DESC` in `app/`. The two
    "latest failed" queries use it too.
  - `current_extraction(session, x_id)` stays as a wrapper over it.
  - `retrieval/evaluation/queries.current_events` is rebuilt on `current_extractions`. This
    is the one exception to "retrieval imports nothing from extraction":
    `test_module_boundaries.py` gets a narrower rule, so that only
    `app/retrieval/evaluation/` may import `app.extraction.store`. The 2026-09-30 DECISIONS
    row records it (step 19).
- `app/fpl/deadlines.py`:
  - pure `next_deadline_after(items, t, key=identity)` and
    `latest_deadline_at_or_before(items, t, key=identity)`, which return the item;
  - `upcoming_deadlines(session, now)`, moved from `tweets/store.py`;
  - `deadline_at_or_before(session, t) -> datetime | None`, a SQL `max(deadline_at)`.

  `tweets/schedule.py` and `tweets/loop.py` use the first and the third, and
  `worker/schedule.py` replaces its three private helpers with
  `key=lambda gw: gw.deadline_at` (the tie behaviour of `min`/`max` is kept).
- Per-call embedding timeout (AC5, BACKLOG #18):
  - `Embedder.embed(texts, *, timeout_seconds: float | None = None)`;
  - `OpenRouterEmbedder` passes `timeout_ms` per call (the SDK's `generate` accepts it) and
    keeps 30 s as the client default;
  - `traced_embed` and `search` take `embed_timeout_seconds=None` and forward it only when it
    is set, so the indexing loop, the `search` CLI and embedders that predate the parameter
    (`BlockingEmbedder` in `tests/worker/test_cli.py`) behave as today;
  - `FakeEmbedder` and `_CachedQueryEmbedder` accept the keyword.

**Repost authors (Group 2).**

- Migration `0006_repost_author` adds the nullable `tweet.reposted_author_handle` and runs one
  `UPDATE … SET reposted_author_handle = COALESCE(raw #>> '{retweetedTweet,user,username}',
  raw #>> '{retweeted_tweet,author,userName}', raw #>> '{retweeted_author,username}') WHERE
  is_repost`. The downgrade drops the column.
- `FetchedPost.reposted_author_handle: str | None = None` is filled by each adapter:
  - twscrape: `tweet.retweetedTweet.user.username`;
  - twitterapi.io: `retweeted_tweet.author.userName`;
  - X API: the request adds the expansion `referenced_tweets.id.author_id`, so
    `includes.tweets` and `includes.users` resolve the retweeted tweet's author. The adapter
    stores that user as `raw["retweeted_author"]`, the key the backfill reads.

  `store_posts` writes the new field. This is a behaviour change under AC7, so the expected
  query string in `tests/tweets/sources/test_x_api.py` changes. It is the one non-import
  edit of an existing tweet test, and it is outside AC4's "no change in behaviour" heading.
  The synthetic payloads gain an original author on their repost.
- `SearchResult` gains `is_repost` and `reposted_author_handle`.

**Corroboration (Group 3).** The package `app/corroboration/` is split by subdomain:

- `schemas.py`:
  - `Label`, `Origin` and `Freshness` literals;
  - `PlayerRef` (season, fpl_id, web_name, team name);
  - `PostRef` (x_id, author_handle, reposted_author_handle, is_repost, created_at, text);
  - `Claim` (post + event_type + certainty);
  - `Citation` (x_id, url `https://x.com/{author_handle}/status/{x_id}`, author, original
    author, created_at, certainty, origin, label, freshness);
  - `Grade` (level, reasons);
  - `RetrievalReport` (status `ran`/`skipped`, skipped reason, failed legs, failure, judged,
    unjudged);
  - `Corroboration` (player, as_of, window_start, new_since, anchor or `None`, supporting,
    contradicting, related, reversal, newer_contradiction, grade, retrieval, trace_id).
- `rules.py` (pure):
  - `label_claim(anchor_type, claim_type)` — the AC10 table;
  - `account_of(post)` — the lower-cased `reposted_author_handle` of a repost, otherwise the
    author; a repost with no stored original falls back to its author;
  - `count_accounts(labelled, anchor)`:
    - the input is every labelled post except the anchor post;
    - the newest post per account decides;
    - accounts with `supports` are supporting, except the anchor's account, which is never
      a confirmation;
    - accounts with `contradicts` are contradicting, the anchor's account included (an
      account reversing its own earlier story);
  - `freshness(created_at, new_since)`;
  - `reversal` and `newer_contradiction`;
  - `grade(certainty, supporting, contradicting, newer_contradiction, new_since,
    rules=GradeRules())`. `GradeRules` holds `high_min_supporting=2`,
    `medium_min_supporting=1` and the Stage 4 hook `credibility: Callable[[str], float]`
    (default 1.0 per account; the supporting weight is the sum).
- `sources.py`:
  - `resolve_player(session, text)` looks up an FPL ID or a name in the latest season, using
    `extraction.linking.load_players` + `PlayerIndex.resolve`;
  - `window(session, as_of, since)` — the start is the `--since` value, else
    `deadline_at_or_before`, else `as_of − 7 days`; the window is `[start, as_of)`;
  - `sql_claims(session, player, start, as_of)` builds on `current_extractions`: one claim
    per post, the first event for the player in extraction order;
  - `retrieval_candidates(...)` runs a hybrid `search(web_name, filters=SearchFilters(since,
    until=as_of), limit=SEARCH_LIMIT=20, embed_timeout_seconds=5.0)` and drops the posts that
    are SQL claims for the player. At most `MAX_JUDGED = 10` go on, in rank order.
- `judge.py` — prompt `app/content/prompts/corroboration_judge.md` (`version: 1`, English
  like the other prompts), `JudgeInput` (player, anchor claim, post), `JudgeOutput(label)`,
  and `PROMPT_VERSION = "corroboration_judge@N"`. `build_judge(caller) -> Judge` compiles a
  one-node LangGraph `StateGraph`, and `Judge.run(input) -> StructuredReply[JudgeOutput]`
  calls the node, which uses `StructuredCaller.call_with_usage` (retries inside). The
  service and the evaluation runner share it.
- `tracing.py` — a `CorroborationTracer` protocol with `NULL` and Langfuse variants:
  - `span(name, input) -> ContextManager[handle]`: the root through
    `client.start_as_current_observation(as_type="chain")`, with `handle.update(output=…)`
    and `handle.trace_id`;
  - `generation(model, input, output, usage, cost_usd, error_class)`: `start_observation(as_type="generation")`;
  - `retrieval()` returns a `retrieval.tracing.LangfuseTracer` on the same client, so the
    search and the query embedding nest under the root through the OTel context.

  Choice: explicit generations through our own tracer beat the LangChain `CallbackHandler`,
  because the cost comes from `prices.toml` and a fake client can assert it. A tracing
  failure is logged by class name and never fails the run, as in `retrieval/tracing.py`.
- `service.py` — `CorroborationRuntime(embedder | None, judge | None, tracer, prices,
  embed_timeout_seconds=5.0, rules=GradeRules(), skipped_reason=None)` and
  `corroborate(engine, player, as_of, new_since=None, *, since=None, runtime) ->
  Corroboration`:
  1. window;
  2. SQL claims;
  3. no claim → a result with no anchor, and no search or judge call;
  4. anchor = the newest claim (a tie goes to the higher `x_id`);
  5. SQL labels;
  6. retrieval + judge (skipped when the embedder or judge is `None`; a failed judge call
     counts as unjudged);
  7. rules → the result.
- `cli.py` — a single-command Typer app. `python -m app.corroboration <player> [--at]
  [--since] [--new-since]` prints:
  - the player, the window and the anchor;
  - the grade and its reasons;
  - the flags;
  - the retrieval and judge line;
  - the supporting, contradicting and related posts, each split into `new` and `context`;
  - `trace: <id>` when traced.

  It uses the `CliDeps` injection pattern from `retrieval/cli.py`. Times are parsed and
  printed through a new `app/core/local_time.py` (`WARSAW`, `parse_local`, `format_local`),
  and `retrieval/cli.py` uses it too, instead of `app.corroboration` importing another
  module's CLI. `CorroborationSettings(ChatSettings, RetrievalSettings)` reads the existing
  variables; no new variable is added.

**Judge evaluation (Group 4).** `app/corroboration/evaluation/`:

- `cases.py` — `JudgeCase` (id, split, player {fpl_id, web_name, team}, anchor {x_id,
  author_handle, created_at, event_type, certainty, text}, post {x_id, author_handle,
  reposted_author_handle, is_repost, created_at, text}, expected label, labelled_by,
  reviewed, has_player_event). It also holds atomic `load_cases`/`write_cases` (the
  `os.replace` pattern of `extraction/evaluation/cases.py`) and
  `composition_problems(cases)`, which requires:
  - 50–70 cases;
  - unique ids;
  - a dev share of 0.25–0.35;
  - each of the four labels in the whole set and in the test split;
  - every `labelled_by` ≠ `DEFAULT_MODEL`;
  - at least one case of a known miss (`has_player_event` false with an expected label ≠
    `unrelated`).

  `PRELABEL_MODEL = "anthropic/claude-haiku-4.5"`. Among the catalogue rows it is a model
  tier above the flash/luna class (10× the default's price), and it comes from another
  vendor than the default judge `openai/gpt-6-luna`, so its pre-labels do not share the
  judge's biases. `qwen/qwen3.8-flash` has the best extraction F1 but is a flash-tier model.
- `building.py` builds the candidates:
  - for every player with a current event, up to 2 anchors (the newest claim, plus the newest
    claim of another event type if one exists);
  - `retrieval_candidates` at `as_of` = the last post + 1 min;
  - then posts that are SQL claims of the player too, if a label is short.

  Pre-labelling runs every candidate through the real `Judge` on `PRELABEL_MODEL` (the same
  prompt). The selection is balanced and seeded (misses first, then by label, `unrelated`
  capped at 40%). The split is stratified by label.
- `metrics.py` — accuracy, per-label precision and recall, and the false-support rate
  (expected ∈ {unrelated, related, contradicts} predicted `supports`, over the cases
  expected in that set). An errored case counts as wrong and is reported.
- `runner.py` — `run_evaluation(cases, judge, split, include_unreviewed, model,
  prompt_version) -> dict` and `write_result(path, data)` (model, prompt_version, split,
  date, include_unreviewed, cost_usd, metrics, per-case outcomes).
- `cli.py` + `__main__.py` — `python -m app.corroboration.evaluation build-cases | review |
  evaluate`. `review` copies the flow of `extraction/cli.review`: `[a]ccept [c]hange
  [s]kip [q]uit`, `c` asks for one of the four labels, and the cases are saved after every
  accept or change.
- Data: `backend/evals/corroboration/v1/cases.jsonl` and
  `backend/evals/corroboration/results/test-<model>.json`.

**Patterns reused:**

- the `CliDeps` + `get_deps(ctx)` injection (`app/retrieval/cli.py:77-138`);
- `FakeChatModel` (`tests/extraction/fakes.py`);
- `FakeEmbedder`, `FakeLangfuseClient` and `FixedClock` (`tests/retrieval/{fakes,helpers}.py`);
- the migration test pattern (`tests/db/test_migrations.py::test_retrieval_migration_keeps_data_and_downgrades`);
- the review loop (`app/extraction/cli.py:_review_cases`);
- the atomic JSONL write (`app/extraction/evaluation/cases.py:write_cases`).

## AC → steps matrix

| AC | Steps | Proving test | Red before the change |
|----|-------|--------------|-----------------------|
| AC1 | 1, 2, 3 | `tests/test_shared_code.py::test_no_chat_model_or_retry_loop_outside_app_llm`, `tests/llm/test_retry.py`, `tests/llm/test_structured.py` | `cd backend && uv run pytest -q tests/test_shared_code.py` → `AssertionError: assert ['extraction/...with_retries'] == []` (step 1); `tests/llm/test_retry.py` had no `app.llm.retry` yet (import error, then green with the module); `uv run pytest -q tests/test_shared_code.py::test_chat_model_factory_and_catalogue_live_in_app_llm` → `AssertionError: assert ['extraction/...on/llm.py:34'] == []` (step 2) |
| AC2 | 4 | `tests/extraction/test_current_extractions.py`, `tests/test_shared_code.py::test_current_extraction_sql_lives_once` | `cd backend && uv run pytest -q tests/extraction/test_current_extractions.py` (stub `current_extractions` returning `[]`) → `assert [] == [1]`; `uv run pytest -q tests/test_shared_code.py::test_current_extraction_sql_lives_once` → `assert {'extraction/...ueries.py': 1} == {'extraction/store.py': 1}` |
| AC3 | 5 | `tests/fpl/test_deadlines.py`, `tests/test_shared_code.py::test_schedules_use_fpl_deadline_helpers` | |
| AC4 | 1–6 | n/a — kept behaviour: the existing suites stay green with import-only diffs (the check command in step 6) | |
| AC5 | 6, 13 | `tests/retrieval/test_embed_timeout.py`, `tests/corroboration/test_service.py::test_slow_embedding_falls_back_to_fulltext` | |
| AC6 | 7 | `tests/db/test_migrations.py::test_repost_author_migration_backfills_and_downgrades` | |
| AC7 | 8 | `tests/tweets/sources/test_repost_authors.py` | |
| AC8 | 11, 13 | `tests/corroboration/test_sources.py::test_window_*`, `tests/corroboration/test_service.py::test_replay_ignores_posts_after_as_of` | |
| AC9 | 11, 13, 14 | `tests/corroboration/test_service.py::test_no_claim_makes_no_judge_call`, `tests/corroboration/test_cli.py::test_no_claim_message` | |
| AC10 | 10 | `tests/corroboration/test_rules.py::test_label_table` | |
| AC11 | 11, 12, 13 | `tests/corroboration/test_sources.py::test_candidates_*`, `tests/corroboration/test_judge.py`, `tests/corroboration/test_service.py::test_judged_posts_*` | |
| AC12 | 9, 10 | `tests/corroboration/test_rules.py::test_accounts_*`, `tests/retrieval/test_search_repost_fields.py` | |
| AC13 | 10, 13 | `tests/corroboration/test_rules.py::test_freshness_*`, `tests/corroboration/test_service.py::test_citations_carry_fields` | |
| AC14 | 10 | `tests/corroboration/test_rules.py::test_reversal_*`, `::test_newer_contradiction_*` | |
| AC15 | 10 | `tests/corroboration/test_rules.py::test_grade_*` (one per branch) | |
| AC16 | 14 | `tests/corroboration/test_cli.py`, `tests/core/test_local_time.py` | |
| AC17 | 13 | `tests/corroboration/test_service.py::test_without_key_sql_only`, `::test_failed_judge_call_counts_unjudged` | |
| AC18 | 12, 13 | `tests/corroboration/test_tracing.py` | |
| AC19 | 15, 18 | `tests/corroboration/evaluation/test_eval_set.py::test_committed_set_composition` | |
| AC20 | 15, 18 | `tests/corroboration/evaluation/test_eval_set.py::test_prelabel_model_*`, `tests/corroboration/evaluation/test_building.py` | |
| AC21 | 16 | `tests/corroboration/evaluation/test_review_cli.py` | |
| AC22 | 17 | `tests/corroboration/evaluation/test_metrics.py`, `tests/corroboration/evaluation/test_runner.py` | |
| AC23 | 18 | `tests/corroboration/evaluation/test_results_files.py` | |
| AC24 | 19 | `tests/test_docs.py::test_backlog_18_closed_and_corroboration_entries_kept`, `tests/test_readme.py::test_corroboration_commands_documented` | |
| AC25 | all | n/a — the property of every step's tests; `<verify.command>` green in step 19 | |

## Steps

All commands run from the repository root. `V` = `cd backend && uv run ruff check . && uv run ruff
format --check . && uv run pytest -q` (the full verification). Every step that delivers an
AC writes its proving test first and runs it red before the product change.

### Group 1 — Shared refactors (app/llm, current extraction, deadlines, embed timeout)

- [x] 1. **One retry helper in `app/llm/retry.py`.**
  - Write `tests/llm/test_retry.py`:
    - success on the 2nd attempt, with the sleeps `[2.0]`;
    - exhaustion after 3 attempts, with the sleeps `[2.0, 4.0]` and the last error kept;
    - a stop before the first attempt, and a stop set during the back-off → `stopped`;
    - the back-off index is clamped when `attempts > len(backoff) + 1`.
  - Add the first part of `tests/test_shared_code.py`:
    - no module-level `MAX_ATTEMPTS` or `RETRY_BACKOFF_SECONDS`, and no function whose name
      contains `retr` with a `for`/`while` loop, anywhere under `app/extraction` or
      `app/retrieval` (AST); `extraction/generation.HostLookup` is a 404 poll for the serving
      host, not a call retry, and stays;
    - `test_app_llm_imports_no_feature_module`: nothing under `app/llm` imports
      `app.extraction`, `app.retrieval` or `app.corroboration` (the import-cycle risk below).
  - Then implement `with_retries` + `RetryOutcome[T]` and move the constants there:
    - `extraction/service.run_with_retries` becomes a wrapper;
    - `retrieval/indexing.with_retries` and its constants are removed;
    - `indexing.embed_post`, `retrieval/evaluation/llm.py` and
      `retrieval/evaluation/runner.py` import from `app.llm.retry`.
  - Existing tests: update the imports of `RETRY_BACKOFF_SECONDS` and `with_retries` only.

  Automatic verification: `cd backend && uv run pytest -q tests/llm/test_retry.py tests/test_shared_code.py tests/extraction/test_service.py tests/retrieval/test_indexing.py tests/retrieval/evaluation`
- [x] 2. **Chat-model factory and model catalogue in `app/llm`.**
  - Extend `tests/test_shared_code.py` with a check that no file under `app/extraction`
    or `app/retrieval` imports `langchain_openrouter` or names `ChatOpenRouter`, and that
    `app/llm/model_settings.toml` exists while `app/extraction/model_settings.toml` does not.
    Run it red.
  - Then:
    - `git mv` `app/extraction/model_settings.{py,toml}` → `app/llm/models.py` and
      `app/llm/model_settings.toml` (the content unchanged; `DEFAULT_PATH` follows);
    - create `app/llm/chat.py` from `extraction/providers.py` + `extraction/config.py`
      (`ChatSettings`, `DEFAULT_MODEL`, `DEFAULT_FALLBACK_MODEL`, `PROVIDER`, `LlmConfig`,
      `resolve_llm`, `ChatModelSpec`, `build_chat_model`, `load_chat_settings`);
    - `extraction/config.py` keeps `ExtractionSettings = ChatSettings` and
      `load_extraction_settings`;
    - delete `extraction/providers.py`;
    - update the imports in `extraction/{flow,service,cli,evaluation/runner}.py`,
      `worker/cli.py` and the extraction and worker tests;
    - `git mv tests/extraction/test_model_settings.py tests/extraction/test_providers.py
      tests/llm/` with the imports changed;
    - update the `model_settings.toml` path mentioned in comments.

  Automatic verification: `cd backend && uv run pytest -q tests/test_shared_code.py tests/llm tests/extraction tests/worker/test_cli.py tests/test_env_example.py`
- [x] 3. **Structured call in `app/llm/structured.py`.**
  - Write `tests/llm/test_structured.py` (with `FakeChatModel`):
    - `call_with_usage` returns the parsed output, the tokens, and the cost from the
      `prices` dict;
    - a parse failure raises and increments `failures`;
    - `from_spec` passes `structured_method`;
    - `structured_kwargs_for` returns the catalogue row's method, and `{}` for a model outside
      the catalogue;
    - costs accumulate across calls;
    - `usage_from_raw` / `answer_from_raw` read `usage_metadata` and `response_metadata`.
  - Then:
    - move `StructuredCaller` there (constructor unchanged plus the keyword fields
      `structured_kwargs` and `stop_event`), and `Usage`, `usage_from_raw` and
      `answer_from_raw` from extraction;
    - `extraction/schemas.py` re-imports `Usage`, and `extraction/flow.py` uses the helpers;
    - `retrieval/evaluation/llm.py` drops `build_label_model`, `usage_cost` and
      `StructuredCaller`;
    - `app/llm/chat.py` gains `single_model_config(api_key, model, catalogue=None) ->
      LlmConfig` (no fallback; a model outside the catalogue → `ConfigError`), which
      `resolve_llm` also uses for its primary row, and `structured_kwargs_for(model,
      catalogue=None) -> dict` (the catalogue row's `{"method": …}`, `{}` for a model outside
      the catalogue). `retrieval/cli.py`'s default `make_chat_model` returns
      `build_chat_model(single_model_config(settings.openrouter_api_key, model)).chat_model`,
      because `RetrievalSettings` has no `llm_model` fields and must keep its field set
      (`tests/test_env_example.py`). `RetrievalCliDeps.make_chat_model` keeps its type
      `Callable[[str], BaseChatModel]`: `tests/retrieval/evaluation/test_{queries,labelling}.py`
      inject `lambda model: fake`, so changing it would break AC4's import-only rule. The CLI
      builds `StructuredCaller(chat_model, model, prices, clock,
      structured_kwargs=structured_kwargs_for(model))`;
    - update the imports in `tests/retrieval/evaluation/test_{queries,labelling}.py`.

  Automatic verification: `cd backend && uv run pytest -q tests/llm tests/test_shared_code.py tests/extraction/test_flow.py tests/retrieval/evaluation tests/retrieval/test_cli.py`
- [x] 4. **One current-extraction query.**
  - Write `tests/extraction/test_current_extractions.py` (db):
    - the batch by `x_ids` returns the latest successful extraction per post, with its events
      (including `player_web_name`) and the post fields (`created_at`, `text`);
    - a failed newer extraction does not hide the older successful one;
    - the `created_from` / `created_until` window (the end exclusive), ordered by
      `created_at, x_id`;
    - posts with no successful extraction are absent.
  - Add `test_current_extraction_sql_lives_once` to `tests/test_shared_code.py`: the
    regex `DISTINCT ON \(tweet_x_id\)` matches only `app/extraction/store.py`, and only once.
    Narrow `tests/test_module_boundaries.py::test_retrieval_imports_nothing_from_extraction`
    to allow `app.extraction.store` from `app/retrieval/evaluation/` only (the documented
    exception).
  - Then implement `current_extractions` and `_LATEST_PER_POST`, and use the fragment in
    `posts_for_reextract(failed=True)` and `extraction_status`.
    `current_extraction(session, x_id)` delegates, and `retrieval/evaluation/queries.current_events`
    is rebuilt on it (`player = event.player_web_name or event.mention`).

  Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_current_extractions.py tests/extraction/test_store.py tests/retrieval/evaluation/test_queries.py tests/test_shared_code.py tests/test_module_boundaries.py`
- [ ] 5. **Deadline helpers in `app/fpl/deadlines.py`.**
  - Write `tests/fpl/test_deadlines.py`:
    - `next_deadline_after`: strictly after, `None` when none, and with a `key` over objects;
    - `latest_deadline_at_or_before`: inclusive at equality, `None` before the first;
    - `upcoming_deadlines(session, now)`, moved from tweets/store (db);
    - `deadline_at_or_before(session, t)` (db): exact equality, between two, before all →
      `None`.
  - Add `test_schedules_use_fpl_deadline_helpers` to `tests/test_shared_code.py` (AST):
    - `app/tweets/schedule.py` and `app/worker/schedule.py` import from
      `app.fpl.deadlines`;
    - they define no function whose name contains `deadline_after` or `at_or_before`;
    - `app/tweets/store.py` no longer defines `upcoming_deadlines`.
  - Then implement, and switch `tweets/schedule.py`, `tweets/loop.py`, `worker/cli.py` and
    `worker/schedule.py` over. `tests/tweets/test_store.py::test_upcoming_deadlines` gets
    only its import changed.

  Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_deadlines.py tests/test_shared_code.py tests/tweets tests/worker/test_schedule.py tests/worker/test_loop.py`
- [ ] 6. **Per-call embedding timeout.**
  - Write `tests/retrieval/test_embed_timeout.py`:
    - `OpenRouterEmbedder.embed(texts, timeout_seconds=2.5)` sends the request with a 2.5 s
      httpx timeout (`request.extensions["timeout"]` via the `client=` MockTransport of
      `tests/retrieval/test_embedder.py`), and without the argument the 30 s default holds;
    - `search(..., mode="hybrid", embed_timeout_seconds=1.0)` with a `SlowEmbedder` fake
      (raises `TimeoutError` when `timeout_seconds` is below its delay; no real sleep) →
      full-text results, `failed_legs == ("vector",)`, and a `failure` naming
      `TimeoutError`;
    - `search` without `embed_timeout_seconds` calls `embed(texts)` with no keyword.
  - Then implement:
    - the Protocol signature, `OpenRouterEmbedder` (`timeout_ms` per call), and the
      forwarding in `traced_embed`/`search`;
    - `SlowEmbedder` in `tests/retrieval/fakes.py`, and `FakeEmbedder.embed` accepting the
      keyword;
    - `_CachedQueryEmbedder.embed` forwarding it.
  - Finally check AC4 over Group 1:
    `git diff origin/main -U0 -- $(git diff --name-only --diff-filter=M origin/main -- backend/tests/extraction backend/tests/retrieval backend/tests/tweets backend/tests/worker | grep -v fakes.py) | grep -E '^[-+][^-+]' | grep -vE '^[-+]\s*(from |import |\)|[A-Za-z_]+,$)'`
    prints nothing.

  Automatic verification: `cd backend && uv run pytest -q tests/retrieval tests/worker/test_cli.py && cd .. && <the AC4 diff command above>`, then `V`

### Group 2 — Repost authors

- [ ] 7. **Migration `0006` — `tweet.reposted_author_handle` with a backfill** (a data
  migration, its own step).
  - Write `tests/db/test_migrations.py::test_repost_author_migration_backfills_and_downgrades`:
    - upgrade to `0005`;
    - insert five tweets: a twscrape repost (`raw.retweetedTweet.user.username`), a
      twitterapi.io repost (`raw.retweeted_tweet.author.userName`), an X API repost
      (`raw.retweeted_author.username`), an original post with a `retweetedTweet`-free raw,
      and a repost with `{}` raw;
    - upgrade to head: the three reposts are backfilled, the others stay null, and the other
      tables are unchanged (the `table_contents` pattern);
    - `downgrade -1` → the column is gone and the data is unchanged;
    - `upgrade head` → backfilled again.
  - Run it red, then add `migrations/versions/0006_repost_author.py` (revision `0006`, down
    `0005`) and the `Tweet.reposted_author_handle: str | None = None` column.
    `test_models_match_migration` and `test_upgrade_downgrade_upgrade` must stay green.

  Automatic verification: `cd backend && uv run pytest -q tests/db/test_migrations.py`
- [ ] 8. **Source adapters fill the original author.**
  - Update the synthetic payloads (keeping `tests/tweets/payloads/README.md` rules and
    `test_payloads.py` green):
    - twitterapi.io `retweeted_tweet` gains `"author": {"userName": "synthetic_leaker_9"}`;
    - X API page 1 gains `includes.tweets: [{"id": "500", "author_id": "u9"}]` and user `u9`;
    - twscrape already carries a `retweeted_status_result`: find its synthetic username with
      `zcat`.
  - Write `tests/tweets/sources/test_repost_authors.py`: per adapter, the repost gets its
    original author and the original post gets `None`; the X API repost's `raw` carries
    `retweeted_author`; `store_posts` persists the field (db).
  - Then:
    - add `FetchedPost.reposted_author_handle`;
    - extend the three `_to_post` functions (X API: `_PARAMS["expansions"] =
      "author_id,referenced_tweets.id.author_id"` and a lookup
      `tweets_by_id`/`users_by_id`);
    - write the field in `tweets/store.store_posts`;
    - add `reposted_author_handle` to `CurrentExtraction` (step 4 left it out);
    - update the expected query string in `tests/tweets/sources/test_x_api.py` (the AC7
      behaviour change).

  Automatic verification: `cd backend && uv run pytest -q tests/tweets tests/extraction/test_current_extractions.py`
- [ ] 9. **`SearchResult` carries the repost fields.**
  - Write `tests/retrieval/test_search_repost_fields.py` (db): a repost with a stored
    original author and an original post come back from `search` with `is_repost` and
    `reposted_author_handle` set.
  - Then add both fields to `SearchResult` and fill them in `search`.

  Automatic verification: `cd backend && uv run pytest -q tests/retrieval/test_search_repost_fields.py tests/retrieval/test_search.py`, then `V`

### Group 3 — Corroboration

- [ ] 10. **Schemas and pure rules** — files: `app/corroboration/{__init__,schemas,rules}.py`.
  - Write `tests/corroboration/test_rules.py` first:
    - `test_label_table`: all 16 pairs of the AC10 table;
    - `test_accounts_*`:
      - a repost counts as its original author;
      - handles are compared case-insensitively;
      - the newest post per account decides (an older `supports` beaten by a newer
        `related`);
      - the anchor's account never supports;
      - the anchor's account's older contradiction counts;
      - a repost with a null original falls back to its author;
    - `test_freshness_*`: strictly after `new_since` → `new`, equal → `context`;
    - `test_reversal_*`: an older contradicting account → true, a newer one alone → false;
    - `test_newer_contradiction_*`: only judged posts newer than the anchor;
    - `test_grade_*`, one per branch:
      - `confirmed` → high;
      - 2 supporting → high;
      - `likely` → medium;
      - 1 supporting → medium;
      - otherwise low;
      - a `new` contradicting account lowers high → medium;
      - a newer contradiction lowers medium → low;
      - both lowering conditions together lower the grade one step only (high → medium);
      - low stays low;
      - the reasons name each rule applied;
      - a custom `credibility` weight changes the supporting sum.

  Automatic verification: `cd backend && uv run pytest -q tests/corroboration/test_rules.py`
- [ ] 11. **Window, player lookup, SQL claims and retrieval candidates** — file:
  `app/corroboration/sources.py`.
  - Write `tests/corroboration/test_sources.py` (db, `FakeEmbedder`, helpers from
    `tests/retrieval/helpers.py`):
    - `test_window_*`:
      - the start is the latest deadline at or before `as_of` (equality included);
      - with no deadline, `as_of − 7 days`;
      - `since` overrides;
      - the end is exclusive;
    - `test_resolve_player_*`: by FPL ID, by name, via an alias, ambiguous → every candidate,
      unknown → empty, in the latest season only;
    - `test_sql_claims_*`: only the player's current events in the window; one claim per
      post; a superseded extraction is ignored;
    - `test_candidates_*`:
      - posts that are SQL claims are excluded;
      - posts after `as_of` are never returned;
      - at most `MAX_JUDGED` in rank order;
      - the search uses `embed_timeout_seconds=5.0` and the window filters.

  Automatic verification: `cd backend && uv run pytest -q tests/corroboration/test_sources.py`
- [ ] 12. **The judge and the tracer** — files: `app/content/prompts/corroboration_judge.md`,
  `app/corroboration/{judge,tracing}.py`.
  - Write the tests first:
    - `tests/corroboration/test_judge.py` (`FakeChatModel`):
      - each of the four labels round-trips;
      - the human message holds the player, the anchor type, certainty and text, the post
        author, the original author and the text;
      - a failure after retries raises;
      - `PROMPT_VERSION` follows the prompt header;
    - `tests/content/test_corroboration_prompt.py`: the prompt loads with a version and states
      `supports`, `contradicts`, `related`, `unrelated` and "next premier league";
    - `tests/corroboration/test_tracing.py` with a context-aware fake Langfuse client in
      `tests/corroboration/fakes.py`, which records the current-span stack:
      - the root span carries the input (player, `as_of`, `new_since`) and its output update
        (anchor, grade);
      - `generation` records the model, the usage and the cost;
      - the retrieval tracer's observations are started while the root is current;
      - a failing client never raises;
      - the null tracer does nothing.
  - The prompt states:
    - the extraction relevance rule (availability whatever the competition; line-ups only for
      the next Premier League match; national, cup, European and women's line-ups →
      `unrelated`);
    - the four labels, mirrored on the AC10 table (the same status → `supports`; starting or
      fit versus out, doubt or benched → `contradicts`; compatible but different
      availability news → `related`; another player or topic → `unrelated`).

  Automatic verification: `cd backend && uv run pytest -q tests/corroboration/test_judge.py tests/corroboration/test_tracing.py tests/content`
- [ ] 13. **`corroborate()`** — file: `app/corroboration/service.py`.
  - Write `tests/corroboration/test_service.py` (db, `FakeChatModel` judge, `FakeEmbedder`,
    fake tracer):
    - `test_no_claim_makes_no_judge_call`;
    - `test_anchor_is_newest_claim` (a tie → the higher `x_id`);
    - `test_judged_posts_labelled_and_unrelated_dropped`;
    - `test_replay_ignores_posts_after_as_of`;
    - `test_citations_carry_fields` (link, author, original author, `created_at`, certainty
      for SQL, origin, label, `new`/`context`, `new_since` defaulting to the window start);
    - `test_slow_embedding_falls_back_to_fulltext` (`SlowEmbedder` → the retrieval report
      shows the vector leg failed, and the judge still runs on the full-text candidates);
    - `test_without_key_sql_only` (the runtime has no embedder or judge → skipped with a
      reason, and no calls);
    - `test_failed_judge_call_counts_unjudged` (the fake raises 3× for one post → unjudged
      1, the others judged);
    - `test_trace_holds_every_step` (the fake Langfuse: one root, a search observation, and
      one generation per judge call with the cost, the grade in the root output).

  Automatic verification: `cd backend && uv run pytest -q tests/corroboration/test_service.py`
- [ ] 14. **The CLI and shared local time** — files: `app/core/local_time.py`,
  `app/retrieval/cli.py` (it uses `local_time`), `app/corroboration/{cli,__main__}.py`.
  - Write the tests first:
    - `tests/core/test_local_time.py`: a naive time is read as Warsaw, summer and winter
      offsets, an invalid string → `ValueError`, the round trip of `format_local`;
    - `tests/corroboration/test_cli.py` (CliRunner + injected dependencies, db):
      - the print order is the anchor, the grade with its reasons, the flags, then
        supporting, contradicting and related, each split into `new` and `context`;
      - `--at` / `--since` / `--new-since` are parsed as Warsaw time and passed on in UTC;
      - an FPL ID and a name both work;
      - an ambiguous name lists the candidates and exits 1, an unknown player exits 1;
      - a player with no claim → the "no claim" message, exit 0;
      - without a key → the "retrieval and judge skipped" line;
      - the `trace:` line appears only when traced.
  - `tests/retrieval/test_cli.py` stays green without edits.

  Automatic verification: `cd backend && uv run pytest -q tests/core/test_local_time.py tests/corroboration tests/retrieval/test_cli.py`, then `V`

### Group 4 — Judge evaluation, set v1 and documentation

- [ ] 15. **Cases, composition and `build-cases`** — files:
  `app/corroboration/evaluation/{__init__,__main__,cases,building,cli}.py`.
  - Write the tests first:
    - `tests/corroboration/evaluation/test_cases.py`: the round trip, the atomic write,
      `composition_problems` (size, duplicate ids, dev share, a missing label per split, a
      `labelled_by` equal to the default model, no miss case);
    - `tests/corroboration/evaluation/test_building.py` (db, fakes):
      - candidates come per player and anchor, with the anchor post excluded;
      - `has_player_event` is set correctly;
      - pre-labels come from the injected judge with `labelled_by = model` and
        `reviewed = false`;
      - the selection is deterministic for a seed, puts misses first and caps `unrelated`;
      - the split is stratified;
      - `build-cases` refuses to overwrite without `--force`.
    - `test_prelabel_model_is_catalogued_and_not_default`: `PRELABEL_MODEL` has a row in
      `model_settings.toml` and `prices.toml` and differs from `DEFAULT_MODEL`.

  Automatic verification: `cd backend && uv run pytest -q tests/corroboration/evaluation/test_cases.py tests/corroboration/evaluation/test_building.py`
- [ ] 16. **`review`.**
  - Write `tests/corroboration/evaluation/test_review_cli.py` (CliRunner input):
    - accept → `reviewed=true` on disk after the decision;
    - change → a label prompt, the new label saved with `reviewed=true`;
    - skip leaves the case untouched;
    - quit stops, and the earlier decisions stay on disk;
    - `--split` and `--id` select;
    - "nothing to review";
    - Ctrl-C → exit 130 and everything decided is saved.
  - Then implement it in `evaluation/cli.py`, with rendering in `evaluation/cases.py` (player,
    anchor, post, pre-label, progress).

  Automatic verification: `cd backend && uv run pytest -q tests/corroboration/evaluation/test_review_cli.py`
- [ ] 17. **Metrics, runner and `evaluate`.**
  - Write the tests first:
    - `tests/corroboration/evaluation/test_metrics.py`: hand-computed cases (accuracy,
      per-label precision and recall with zero-division → `None`, the false-support rate
      numerator and denominator, an errored case counting as wrong);
    - `tests/corroboration/evaluation/test_runner.py`:
      - reviewed-only by default; with no reviewed case in the split → an error naming
        `--include-unreviewed`;
      - `--include-unreviewed` includes them;
      - the result JSON carries the model, `prompt_version`, split, date,
        `include_unreviewed`, `cost_usd` and per-case `{id, expected, predicted,
        error_class, input_tokens, output_tokens, cost_usd}`;
      - the default output path is `evals/corroboration/results/<split>-<model slug>.json`;
      - `evaluate --model <id>` runs the judge on that model (AC22 "for a chosen split and
        model"), the default being `DEFAULT_MODEL`, and a model outside the catalogue exits 1;
      - the CLI prints the metrics.

  Automatic verification: `cd backend && uv run pytest -q tests/corroboration/evaluation`
- [ ] 18. **Build set v1 and record the first run** (real calls against the development
  database; the cost is expected below 0.50 USD).
  - Write the tests first:
    - `tests/corroboration/evaluation/test_eval_set.py::test_committed_set_composition`:
      `composition_problems(load_cases(DEFAULT_CASES_PATH)) == []`, and every
      `labelled_by == PRELABEL_MODEL`;
    - `tests/corroboration/evaluation/test_results_files.py`: a committed
      `results/test-*.json` for `DEFAULT_MODEL` with `split == "test"`,
      `include_unreviewed == true`, the metrics keys and one outcome per test case.

    Run them red (the files are missing).
  - Then:
    `cd backend && uv run python -m app.corroboration.evaluation build-cases --output evals/corroboration/v1/cases.jsonl`
    followed by
    `cd backend && uv run python -m app.corroboration.evaluation evaluate --split test --include-unreviewed`.
  - If `composition_problems` reports a label missing from the corpus even with the
    second-anchor pairing, stop: `RESULT: ESCALATE` (AC19 cannot be met from the corpus).

  Automatic verification: `cd backend && uv run pytest -q tests/corroboration/evaluation`
- [ ] 19. **Documentation and hygiene.**
  - Write `tests/test_docs.py::test_backlog_18_closed_and_corroboration_entries_kept`: no
    row mentions "query-embedding timeout", and the #19 (P2) and #20 (P3) rows have
    triggers.
  - Write `tests/test_readme.py::test_corroboration_commands_documented`: the README
    Development section and `docs/DEPLOYMENT.md` name `app.corroboration`,
    `app.corroboration.evaluation build-cases`, `review` and `evaluate`, and DEPLOYMENT names
    `0006`.
  - Then edit:
    - `docs/BACKLOG.md`: delete row #18;
    - `docs/DECISIONS.md`: edit the 2026-09-30 rows in place where the build differs:
      - `app/llm/models.py` and `chat.py`, and the one allowed
        `retrieval.evaluation → extraction.store` import;
      - the 5 s corroboration embed timeout and the `[start, as_of)` window;
      - `PRELABEL_MODEL` and the explicit Langfuse generations;
    - `docs/PROJECT.md` FR-2.2: check it against the behaviour and edit it only if it
      differs;
    - `README.md` Development: the corroboration CLI and the evaluation commands;
    - `docs/DEPLOYMENT.md`:
      - migration `0006` and its backfill, which runs in the pre-deploy; X API reposts stored
        before it keep a null original author;
      - `python -m app.corroboration <player>` from a Railway shell;
    - `docs/ROADMAP.md`: tick item 2 of Stage 2.

  Automatic verification: `cd backend && uv run pytest -q tests/test_docs.py tests/test_readme.py`, then `V`, then `workflow_metrics.py --check specs/007-leak-corroboration`

## Risks and traps

- **Missing labels in set v1.** 19 of the 25 development events are `doubt` (4
  `confirmed_starter`, 2 `benched`, no `out`), so `contradicts` may be rare. The builder's second-anchor pairing
  (a different event type of the same player) is the planned mitigation. A label that is
  still missing is an escalation (step 18), never synthetic cases, because the SPEC says
  "built from the development corpus".
- **Langfuse nesting.** `start_as_current_observation` sets the OTel context. The
  retrieval tracer's `start_observation` on the same client should become a child. If the
  e2e check shows a separate trace, pass `trace_context={"trace_id", "parent_span_id"}` from
  the root handle into `LangfuseTracer`. This is within scope, not a deviation from the
  architecture.
- **The X API expansion.** `referenced_tweets.id.author_id` adds `includes.tweets` to the
  response. `_map_posts` must tolerate a retweet whose referenced tweet is missing from
  `includes` (the original author stays `None`, and the post is still stored).
- **Env for "no key" e2e.** `env_ignore_empty=True` and `.env` in `backend/` mean an empty
  `OPENROUTER_API_KEY=` does not unset the key. Run the no-key check from a directory with no
  `.env`, with `DATABASE_URL` exported.
- **Time zones.** CLI input is Warsaw, and everything stored and compared is UTC. The window
  end is exclusive in both the SQL claims and `SearchFilters.until` (already `<`). Test the
  DST offsets in `test_local_time.py`.
- **AC4 discipline.** Step 6's diff check must print nothing. The allowed exceptions are
  `tests/retrieval/fakes.py` (the Protocol keyword), the narrowed rule in
  `tests/test_module_boundaries.py` (AC2), and the X API query string (AC7, Group 2).
- **Import cycles.** `app.llm` must not import `app.extraction` or `app.retrieval`.
  `app.corroboration` imports extraction, retrieval, fpl and llm, and nothing imports it.
- **Judge latency.** Judge calls run one after another, each with up to 3 attempts and 2 s +
  4 s of back-off, so a slow or failing provider can make one corroboration take tens of
  seconds (10 posts). That is accepted here; the alert spec owns the latency budget and may
  add concurrency or a shorter retry policy through `with_retries(attempts=…, backoff=…)`.
- **Secrets.** Result files and cases hold public post text only: no key, and no manager or
  league data. The judge prompt holds no private data.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

Against the local development stack (`docker compose up -d`, `backend/.env` with
`DATABASE_URL`, `OPENROUTER_API_KEY` and the Langfuse keys, which are all set):

1. `cd backend && uv run alembic upgrade head` → head `0006`. Then
   `docker exec gaffers-presser-db-1 psql -U <user> -d presser -c "select count(*) filter
   (where reposted_author_handle is null) as missing, count(*) from tweet where is_repost"`
   → `missing = 0`, `count = 42`.
2. `cd backend && uv run python -m app.corroboration Isak --at 2026-09-29T18:00` → exit 0;
   the anchor is a `doubt`; the window starts at 2026-09-18 19:30 (Warsaw); at least one
   judged post (the `@BenDinnery` withdrawal expected among the supporting or related
   posts); a grade with reasons; a `trace:` line.
3. `cd backend && uv run python -m app.corroboration Gakpo` and `… Palmer` → exit 0, the
   output is complete.
4. An ambiguous name (pick one from
   `select web_name, count(*) from player group by 1 having count(*) > 1`, or a shared first
   name) → exit 1 and a list of candidates.
5. No key: `cd "$SCRATCH" && DATABASE_URL=<dev url> uv run --project <repo>/backend python
   -m app.corroboration Isak --at 2026-09-29T18:00` → "retrieval and judge skipped", the SQL
   claims only, exit 0.
6. The trace: with the id from 2, `cd backend && uv run python -c "import time; from
   langfuse import Langfuse; time.sleep(15); t = Langfuse().api.trace.get('<id>');
   print(sorted({o.name for o in t.observations}), [o.type for o in t.observations])"` →
   one trace holding `retrieval-search`, `embedding` and one `GENERATION` per judged post,
   with a cost.
7. Step 18's `build-cases` and `evaluate` outputs recorded in `## Deviations` if anything
   differs (the counts per label, the cost).

Record the outputs of 1–6 (condensed) under the Definition of Done.

### Manual (performed by the owner)

1. Run `python -m app.corroboration Isak --at 2026-09-29T18:00` and one player you follow,
   and judge whether the anchor, the supporting and contradicting accounts and the grade are
   what you would conclude from the posts.
2. Open the trace from 1 in Langfuse and check that it reads as one story (the search, the
   judge calls, the grade).

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
      fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, `docs/BACKLOG.md`, `docs/PROJECT.md`,
      `docs/DEPLOYMENT.md` and `README.md` as in step 19
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation: date, stage, question, decision)_

## Review log

### 2026-09-30 — /pipeline:plan-review

**Findings (severity counted before the fixes)**

| id | severity | finding | change |
|----|----------|---------|--------|
| R1 | `major` | Step 3 changed `RetrievalCliDeps.make_chat_model` to return a `ChatModelSpec`, but `tests/retrieval/evaluation/test_queries.py:227` and `test_labelling.py:224,293` inject `lambda model: fake` (a bare chat model). Those tests would need non-import edits, which breaks AC4 and the step 6 diff check. | `make_chat_model` keeps `Callable[[str], BaseChatModel]`. The default builds `build_chat_model(single_model_config(...)).chat_model`, and the catalogue's structured method reaches `StructuredCaller` through a new `structured_kwargs_for(model)` (step 3, with a test). |
| R2 | `minor` | AC15 says either condition "lowers the grade one step", but no test covered both conditions at once, which could lower it two steps. | Step 10: a test that both conditions together lower high → medium only. |
| R3 | `minor` | AC22 says "for a chosen split and model", but step 17 tested only `--split`. | Step 17: a test for `evaluate --model`, with the default model and an uncatalogued model exiting 1. |
| R4 | `minor` | The import-cycle risk (`app.llm` must not import feature modules) had no guard test. | Step 1: `test_app_llm_imports_no_feature_module` in `tests/test_shared_code.py`. |
| R5 | `minor` | Judge calls are serial with 2 s + 4 s back-off, and the plan did not mention the latency this adds. | A "Judge latency" risk: accepted here, and the alert spec owns the budget. |
| R6 | `minor` (no change) | An interpretation for the final review: `count_accounts` counts the anchor account's own older contradicting post as a contradicting account, so a self-reversal sets `reversal`. If that post is `new`, it also lowers the grade. AC12/AC14 allow this reading ("an account reversing its own earlier story"), and the plan states it explicitly. | Kept. Flagged so the owner can confirm it at GATE 2. |

**Checked and found correct (later stages need not repeat this)**

- **Coverage:** AC1–AC25 each have steps and a named proving test, and the matrix matches the
  steps. AC4 and AC25 are properly `n/a` with a reason. Each AC's proving test is written in
  the step that delivers it.
- **Compliance:**
  - The plan follows DECISIONS 2026-09-26 (fake LLM in unit tests; evaluation outside
    `pytest`), 2026-09-28 (relevance rule, Langfuse, evaluation sets as JSONL with dev/test
    and owner review), 2026-09-29 (OpenRouter only, `model_settings.toml`, the
    `app/llm` shared layer) and the five 2026-09-30 rows.
  - The one `retrieval.evaluation → extraction.store` import is a direct consequence of AC2
    (one public query in `extraction/store.py`). It is recorded as a DECISIONS edit in step 19.
  - Prompts stay in `app/content/prompts/`, in English like the existing four prompts.
    CONVENTIONS "Polish product content" covers generated texts, and a judge prompt is not one.
- **Code facts verified:**
  - `DISTINCT ON (tweet_x_id)` appears exactly at `extraction/store.py:148,212` and
    `retrieval/evaluation/queries.py:23`, so the regex guard is red first and then green.
  - The only `retr`-named looped functions are `extraction/service.run_with_retries` and
    `retrieval/indexing.with_retries`, and their semantics match the merged helper
    (stop checks, clamped back-off index).
  - The worker's `_deadline_after`, `_gameweek_with_deadline_after` and
    `_gameweek_with_greatest_deadline_at_or_before`, and `tweets/schedule.next_deadline_after`,
    are caught by the AST guard.
  - `upcoming_deadlines` is used by `tweets/loop.py` and `worker/cli.py`.
  - The SDK's `embeddings.generate` accepts `timeout_ms`.
  - `anthropic/claude-haiku-4.5` has rows in `model_settings.toml` and `prices.toml`.
  - `load_players`, `PlayerIndex.resolve`, `SearchFilters(since, until)` and the tracer
    observation names `retrieval-search`/`embedding` exist.
  - The X API `raw` is `{"tweet", "author"}`, so the new top-level `retweeted_author` key
    fits the backfill path. The twitterapi.io author field is `author.userName`.
  - The retrieval eval's `current_events` runs on the live engine, not the `retrieval_eval`
    schema.
  - `parse_warsaw` is not imported by any test.
- **Minimality:** the refactors are the ones the SPEC scopes. There is no new dependency,
  and the existing patterns are reused (`CliDeps`, `FakeChatModel`, `FakeEmbedder`, the review
  loop, the atomic JSONL write).
- **Feasibility:** there are no forward dependencies (`CurrentExtraction.reposted_author_handle`
  is explicitly deferred from step 4 to step 8). The migration is its own step with the
  upgrade/downgrade/upgrade test. The time zone, exclusive window end, missing X API
  expansion and `.env` no-key traps are covered.
- **E2E:** the automatic part runs on the local stack (migration, CLI runs, ambiguous name,
  no key, Langfuse trace fetch). The manual part is only the owner's judgement of the output.
  There is no UI scope.
- **Testability:** every step has an `Automatic verification:` line with exact test paths.
- **Groups:** there are 4 groups, each ending on completed work. That is appropriate for 19
  steps, and `implement.chunked` is false.
- **Test-first:** every AC-delivering step writes its test first, and the matrix has the
  fourth column.
- **Summary:** consistent with the plan. There is no new dependency, and the data migration
  `0006` is accepted in the SPEC's `## Owner decisions`.
- **Language:** English throughout, matching `language: en`.

**Approval:** the plan is ready. The one `major` finding (R1) was fixed in the plan, no
blocker remains, and the only escalation trigger (the `0006` data migration) is already
accepted by the owner, so the status is set to `plan-approved`.

## Chunk notes

_(filled in by /pipeline:implement in chunk mode — one entry per chunk that ends at a group boundary)_

## Deviations

- Step 2 (minor): `single_model_config` and `structured_kwargs_for`, and the switch of the
  retrieval CLI's default chat-model builder to `build_chat_model`, were pulled forward from
  step 3 into step 2, because the step 2 guard test (no `ChatOpenRouter` under `app/retrieval`)
  cannot go green while `retrieval/evaluation/llm.build_label_model` builds one. Step 3 keeps
  the rest (`StructuredCaller`, `Usage`, tests for the two helpers).

## Final review

_(filled in by /pipeline:final-review)_
