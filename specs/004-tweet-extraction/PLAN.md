# PLAN 004 — LangGraph extraction flow, player linking and the default LLM

## Owner summary

- **Approach:** a new module `app/extraction` holds a two-node LangGraph flow (extract with
  typed Pydantic output → link through a deterministic, accent-insensitive lookup over the
  current season's players plus a reviewed alias file, with an LLM disambiguation call only
  among ambiguous candidates). The chat model comes from a provider factory (Google, OpenAI,
  Anthropic, OpenRouter via the OpenAI client) chosen by environment variables; every flow
  run is one Langfuse trace. Extractions and their events go to two new tables; a third
  thread in the worker extracts new posts oldest first, and `python -m app.extraction`
  gives re-extraction, pre-labelling, a player snapshot and the evaluation command. The
  implementer builds evaluation set v1 (all posts stored locally + ~25 synthetic cases,
  candidate labels with `reviewed: false`); the last group (ADR 0006, default model) waits
  for your comparison run.
- **Main risks:** the local database holds 107 posts today, not ~150 — the set takes every
  post stored at implementation time and the composition test requires ≥ 100 real cases;
  structured output behaves differently per provider (OpenRouter models without tool
  calling); Langfuse SDK 4.x API differs from older docs; the last group cannot finish
  without your comparison results, so the implementer will escalate there by design.
- **New dependency:** yes — `langgraph`, `langchain-core`, `langchain-google-genai`,
  `langchain-openai` (also for OpenRouter), `langchain-anthropic`, `langfuse`, exact pins;
  accepted in SPEC → "Owner decisions". `rapidfuzz` is not added (exact normalised matching
  plus aliases; the evaluation shows whether fuzzy matching is needed).
- **Data migration:** yes — migration `0004` adds tables `extraction` and
  `extraction_event` only; accepted in SPEC → "Owner decisions".
- **Manual scenarios for the owner:** 4 — review every pre-labelled case, tune the prompt on
  dev only, run the evaluation on the test split for at least one cheap model per provider
  (≤ 10 PLN in total), and one live run of the worker with real keys and Langfuse.

## Approach

### What this plan rests on

- `specs/004-tweet-extraction/SPEC.md` — read in full.
- `docs/CONVENTIONS.md` — read in full.
- `docs/DECISIONS.md` — searched for "LLM", "LangGraph", "Langfuse", "extraction",
  "evaluation", "fake LLM", "exact pin", "worker": gave the rows on LangGraph + Pydantic
  (ADR 0001), the LangChain chat-model interface, Langfuse from the first LLM stage, fake
  LLM in tests / evaluation outside `pytest`, the `app/extraction` module, and the five
  spec-004 rows (2026-09-28) already on this branch — step 22 adds only the ADR 0006 row.
- `docs/ROADMAP.md` — searched for "extraction", "Langfuse", "evaluation": Stage 1 item 3
  is this spec; the stage definition of done needs tracing + an evaluation set with results.
- `docs/BACKLOG.md` — read the table (11 lines): #7 stays deferred; #9 reserves ADR 0005;
  #10 is the known flaky warning in the worker concurrency test this plan copies.
- `docs/DEPLOYMENT.md` — searched for "TWEET", "variable": step 7 of the runbook is the
  pattern for an optional feature switched on by variables.
- `README.md` — read the `## Development` section in full (the tweet-ingest paragraph is
  the pattern; `tests/test_readme.py` asserts its content).
- `docs/adr/README.md` — read the head (format: Status, Context, Options, Decision,
  Consequences).
- Code read in full: `app/core/settings.py`, `app/core/errors.py`, `app/db/engine.py`,
  `app/tweets/{config,loop,models,store,cli,__main__}.py`,
  `app/worker/{cli,loop,models,store}.py`, `app/fpl/models/{reference,columns,__init__}.py`,
  `migrations/env.py`, `migrations/versions/0003_tweets.py`, `backend/pyproject.toml`,
  `backend/.env.example`, `backend/Dockerfile`, `tests/conftest.py`,
  `tests/db/test_migrations.py`, `tests/tweets/fakes.py`, `tests/test_env_example.py`,
  `tests/test_readme.py`, `tests/tweets/sources/test_dependency.py`; read the concurrency
  and shutdown tests of `tests/worker/test_cli.py` (lines 1–80 and 270–420) and the head of
  `tests/worker/sim.py` (`FakeClock`).
- Local data checked: 107 posts (2026-09-25 … 2026-09-28, 22 reposts, 2 replies), 667
  players in season `2026/27`. PyPI checked on 2026-09-28: langgraph 1.2.12,
  langchain-core 1.6.5, langchain-google-genai 4.4.0, langchain-openai 1.6.6,
  langchain-anthropic 1.7.4, langfuse 4.15.6 (the implementer pins what `uv` resolves).

### Patterns reused

- Optional feature switched on by variables, config error naming the variable never the
  value: `app/tweets/config.py` (`resolve_ingest`, `check_source`) + `TweetSettings` in
  `app/core/settings.py` (`env_ignore_empty=True`, `SecretStr`).
- A loop in its own daemon thread with a stop-aware clock and error-class-only logging:
  `app/tweets/loop.py` (`TweetPoller`, `StopAwareClock`, `start_poller`).
- Worker wiring through injectable `WorkerDeps` and the `status` command:
  `app/worker/cli.py` (`TweetIngest`, `_deps_from_settings`, the `finally` join).
- Typer CLI with injectable deps via `ctx.obj`: `app/tweets/cli.py` (`get_deps`).
- `DISTINCT ON` latest-row queries: `app/tweets/store.py` (`latest_success_by_source`).
- Tests: DB container fixtures `db` / `db_session` in `tests/conftest.py` (truncates every
  table, so new tables need nothing); the concurrency test
  `tests/worker/test_cli.py::test_polls_continue_while_a_deadline_snapshot_blocks` and
  `FakeClock` in `tests/worker/sim.py`; migration tests in `tests/db/test_migrations.py`;
  pin test `tests/tweets/sources/test_dependency.py`; docs tests `tests/test_env_example.py`
  and `tests/test_readme.py`.

### Module layout (binding)

```
backend/app/content/__init__.py            load_prompt(name) -> Prompt(name, version, text)
backend/app/content/prompts/extraction.md
backend/app/content/prompts/link_disambiguation.md
backend/app/extraction/__init__.py
backend/app/extraction/__main__.py         python -m app.extraction
backend/app/extraction/config.py           resolve_llm, resolve_tracing, provider table
backend/app/extraction/providers.py        build_chat_model(LlmConfig) -> ChatModelSpec
backend/app/extraction/schemas.py          Pydantic LLM outputs + result dataclasses
backend/app/extraction/tracing.py          Langfuse handler + run config
backend/app/extraction/linking.py          normalise, PlayerIndex, aliases, load_players
backend/app/extraction/aliases.toml        reviewed aliases (players and teams)
backend/app/extraction/pricing.py          cost from prices.toml
backend/app/extraction/prices.toml         USD per 1M tokens per provider:model
backend/app/extraction/flow.py             LangGraph graph: extract -> link
backend/app/extraction/models.py           Extraction, ExtractionEvent tables
backend/app/extraction/store.py            queries and writes
backend/app/extraction/service.py          extract_post: retries, storing
backend/app/extraction/loop.py             ExtractionLoop, start_extractor
backend/app/extraction/cli.py              reextract, prelabel, snapshot-players, evaluate
backend/app/extraction/evaluation/{__init__,cases,metrics,runner}.py
backend/evals/extraction/v1/cases.jsonl    the evaluation set (not in the Docker image)
backend/evals/extraction/v1/players-2026-27.json   the player/team snapshot
backend/evals/extraction/results/          evaluation results (JSON, committed with the report)
backend/tests/content/, backend/tests/extraction/, backend/tests/extraction/evaluation/
```

Aliases and prices live in `app/extraction/` (data the code needs in production, not
Polish product content); prompts live in `app/content/prompts/` (CONVENTIONS). The prompts
are written in English — they are instructions to the model about English posts, not text
shown to users.

### Configuration (binding for steps 2, 3, 8, 13, 22)

`ExtractionSettings(BaseSettings)` in `app/core/settings.py`, same `model_config` as
`TweetSettings` (`env_file=".env"`, `extra="ignore"`, `env_ignore_empty=True`):

| Variable | Field | Type / default |
|---|---|---|
| `LLM_PROVIDER` | `llm_provider` | `str = ""` — `google` / `openai` / `anthropic` / `openrouter`; empty = extraction disabled |
| `LLM_MODEL` | `llm_model` | `str = ""` — required while no default exists (step 22 adds one) |
| `GOOGLE_API_KEY` | `google_api_key` | `SecretStr \| None` |
| `OPENAI_API_KEY` | `openai_api_key` | `SecretStr \| None` |
| `ANTHROPIC_API_KEY` | `anthropic_api_key` | `SecretStr \| None` |
| `OPENROUTER_API_KEY` | `openrouter_api_key` | `SecretStr \| None` |
| `LANGFUSE_PUBLIC_KEY` | `langfuse_public_key` | `SecretStr \| None` |
| `LANGFUSE_SECRET_KEY` | `langfuse_secret_key` | `SecretStr \| None` |
| `LANGFUSE_HOST` | `langfuse_host` | `str = "https://cloud.langfuse.com"` (EU region) |
| `USD_PLN_RATE` | `usd_pln_rate` | `float \| None` — required only by `evaluate` |

- `resolve_llm(settings) -> LlmConfig | None`: `None` when `llm_provider` is empty; unknown
  provider → `ConfigError("LLM_PROVIDER must be one of: google, openai, anthropic,
  openrouter")`; missing key → `ConfigError("<VAR> must be set for LLM_PROVIDER=<p>")`;
  empty model with no default → `ConfigError("LLM_MODEL must be set")`. `LlmConfig` holds
  `provider`, `model`, `api_key: SecretStr`. `resolve_llm` also accepts provider/model
  overrides (the CLI's `--provider/--model`).
- `resolve_tracing(settings) -> TracingConfig | None`: both Langfuse keys set → config;
  either missing → `None` (the worker logs one warning, AC4).
- Messages never interpolate a value, only variable names; exceptions are logged as
  `type(exc).__name__` only (the ingest's rule).

### Providers (binding for step 3)

`build_chat_model(config: LlmConfig) -> ChatModelSpec` where
`ChatModelSpec(provider, model, chat_model: BaseChatModel, structured_kwargs: dict)`.
`google` → `ChatGoogleGenerativeAI`, `openai` → `ChatOpenAI`, `anthropic` →
`ChatAnthropic`, `openrouter` → `ChatOpenAI(base_url="https://openrouter.ai/api/v1")` with
`structured_kwargs={"method": "function_calling"}` (not every OpenRouter model supports
`json_schema`). Every model: `temperature=0` — except models that reject any temperature
other than the default (OpenAI reasoning families: a model name, or for OpenRouter the part
after `openai/`, starting with `gpt-5`, `o1`, `o3` or `o4`, kept in one
`NO_TEMPERATURE_PREFIXES` tuple), which get no temperature argument at all — the SDK's own retries off
(`max_retries=0`; the service counts attempts), a request timeout of 60 s, the key passed
explicitly (pydantic-settings reads `.env` without exporting to `os.environ`).

### Flow (binding for steps 5, 7, 8)

LLM output schemas (Pydantic, `schemas.py`):

```python
EventType = Literal["out", "doubt", "benched", "confirmed_starter"]
Certainty = Literal["confirmed", "likely", "rumour"]

class ExtractedEvent(BaseModel):
    player: str            # as written in the post
    team: str | None       # as written, when the post states it
    event_type: EventType
    certainty: Certainty

class ExtractionOutput(BaseModel):
    events: list[ExtractedEvent]

class Disambiguation(BaseModel):
    fpl_id: int | None
```

Result types (frozen dataclasses): `PostInput(x_id, author_handle, text, created_at,
is_repost, is_reply)`; `LinkedEvent(mention, team, player_season | None,
player_fpl_id | None, event_type, certainty)`; `Usage(input_tokens | None,
output_tokens | None)` with addition; `FlowResult(events: list[LinkedEvent], usage: Usage,
llm_calls: int)`.

Graph (`flow.py`): `build_flow(spec: ChatModelSpec, index: PlayerIndex) -> Flow`; state is
a `TypedDict` (post, extracted, events, usage, llm_calls). Node `extract` renders
`extraction.md` with the post (author handle, repost/reply flags, created_at, text) and
calls `spec.chat_model.with_structured_output(ExtractionOutput, include_raw=True,
**spec.structured_kwargs)`; a `parsing_error` or `parsed is None` raises
`ExtractionOutputError` (a validation failure the service retries). Node `link` resolves
each event with the index; when the index returns ≥ 2 candidates it calls the model with
`link_disambiguation.md` (post text, mention, team, candidates as `fpl_id — full name
(web name), team`) and `Disambiguation`; an `fpl_id` outside the candidates, `None`, or an
exception from that call → unlinked (the exception class logged). Zero candidates →
unlinked without a call. Usage is summed from `raw.usage_metadata` of every call.
`Flow.run(post, config: RunnableConfig) -> FlowResult` invokes the compiled graph once.

`PROMPT_VERSION` recorded on every extraction = `"extraction@<v>+link_disambiguation@<v>"`
from the two prompts' `version:` headers (a prompt file starts with a `version: N` line,
then a blank line, then the text; `load_prompt` fails on a missing header).

### Linking (binding for step 6)

- `normalise(s)`: `casefold`, NFKD with combining marks dropped, then a fixed map for
  letters NFKD does not decompose (`ø→o`, `æ→ae`, `œ→oe`, `ß→ss`, `đ→d`, `ł→l`, `ı→i`,
  `þ→th`), punctuation (`.`, `'`, `’`, `-`) → space, whitespace collapsed.
- `PlayerRecord(season, fpl_id, web_name, first_name, second_name, team_fpl_id)` and
  `TeamRecord(season, fpl_id, name, short_name)`. Keys per player: `web_name`,
  `first_name`, `second_name`, `first_name + second_name`, the last token of
  `second_name`, `first_name + last token of second_name`, plus aliases. Keys map to sets
  of players.
- `PlayerIndex.resolve(mention, team) -> list[PlayerRecord]`: candidates of
  `normalise(mention)`; if `team` resolves to a team of the season (name, short name or a
  team alias, normalised), keep candidates of that team — unless that leaves none, then
  keep them all. One candidate = linked without an LLM call.
- `aliases.toml`: `[[player]] alias, season, fpl_id` and `[[team]] alias, short_name`. A
  player alias whose (season, fpl_id) is not among the index's players is ignored and
  logged once per process (a module-level set of logged aliases). Initial content: common
  nicknames/short forms for players present in `2026/27` (checked against the local
  `player` table) and common team short forms ("Spurs", "Man Utd", "Wolves", "Forest" …).
- `load_players(session) -> (list[PlayerRecord], list[TeamRecord])`: only the latest
  season present in `player` (`max(season)`), AC13. The worker rebuilds the index for each
  post (667 rows, milliseconds) so players added by the hourly reference sync are seen.
- `load_snapshot(path)` builds the same records from the evaluation snapshot, so the
  evaluation runs the same linking offline.

### Tables (binding for step 9)

`extraction`: `id` int PK; `tweet_x_id` BigInteger FK → `tweet.x_id`, not null; `status`
str (`extracted` / `failed`); `provider` str; `model` str; `prompt_version` str;
`started_at`, `finished_at` UTC timestamps; `attempts` int; `error_class` str null;
`input_tokens`, `output_tokens` int null; `cost_usd` float null; `latency_seconds` float
null (post stored → extraction stored = `finished_at - tweet.first_fetched_at`, set by the
worker loop only; re-extractions leave it null); index
`ix_extraction_tweet_status_finished (tweet_x_id, status, finished_at)`.

`extraction_event`: `id` int PK; `extraction_id` int FK → `extraction.id`, not null, index;
`mention` str; `team_mention` str null; `player_season` str null, `player_fpl_id` int null
(composite FK → `player(season, fpl_id)`); `event_type` str; `certainty` str.

Cost: `cost_usd = input_tokens × input_price + output_tokens × output_price` (prices per 1M
tokens from `prices.toml`, key `"provider:model"`); null when the model is missing from the
file or the provider reports no usage. `prices.toml` entries carry a `checked` date; the
implementer adds entries only for prices it can read from the provider's pricing page, the
owner completes it before the comparison (manual scenario 3).

### Worker (binding for steps 12–14)

- `ExtractionLoop(engine, runtime, clock, stop_event)`: each iteration takes the oldest
  pending post (`next_pending`: posts with no `extraction` row, ordered by `created_at`,
  `x_id`); none → `clock.sleep(IDLE_POLL_SECONDS = 2.0)` (AC18's 5 s bound); an iteration
  error → log the class, `clock.sleep(30)`. `extract_post` stores `extracted` or `failed`,
  so a failed post is not picked again (the CLI `--failed` re-runs those).
- Retries in `extract_post`: up to 3 attempts; back-off `clock.sleep(2)` then
  `clock.sleep(4)`; stop between attempts when the stop event is set (nothing stored — the
  post stays pending). Any `Exception` from the flow counts (provider SDK errors, timeouts,
  rate limits, `ExtractionOutputError`); `error_class = type(last_exc).__name__`.
- `ExtractionRuntime(provider, model, make_spec: Callable[[], ChatModelSpec],
  tracing: TracingConfig | None, clock: Clock | None = None)` added to `WorkerDeps` as
  `extraction: ExtractionRuntime | None = None`. The thread is named `extractor`, started
  after the schedule lock next to the poller; the `finally` joins poller and extractor
  against one shared 5 s deadline (not 5 s each), so the spec-002 bound (< 10 s) holds.
- Log lines: `extraction disabled` (info, once) when `extraction is None`;
  `langfuse tracing disabled: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set` (warning,
  once) when extraction is on and tracing is `None`.
- `status` gains:

```
Extraction: disabled
```
or
```
Extraction:
  model: <provider>:<model>
  posts waiting: <n>
  failed posts: <n>                (posts whose latest extraction is failed)
  latest extraction: <finished_at Z> x_id=<id> status=<s> latency=<s|->   | never
```

### CLI `python -m app.extraction` (binding for steps 15, 18, 19)

- `reextract (--x-id N | --since ISO --until ISO | --failed) [--provider P --model M]`:
  exactly one selector; runs `extract_post` (no latency) per post, oldest first; prints
  `posts processed: n`, `events: n`, `failures: n`, `total cost: $x.xxxx` (or `n/a`).
- `snapshot-players --output PATH`: writes the latest season's players and teams as JSON.
- `prelabel --output PATH [--since/--until] [--limit N] --provider P --model M
  [--eval-set PATH]`: runs the flow on local posts whose X ID is not yet in the set and
  appends candidate cases with `reviewed: false`, `synthetic: false`, split by
  `x_id % 10 < 3 → dev` else `test`, expected events = the model's linked events, `tags: []`.
- `evaluate --split dev|test --provider P --model M [--run-name NAME]
  [--posts-per-month 1050] [--cases PATH] [--players PATH] [--output-dir DIR]`: checks in
  this order, before any model is built — refuses (exit 1, counts the cases) when any case
  of the split has `reviewed: false`; then requires `USD_PLN_RATE`; then resolves the LLM
  config. Each case runs with the service's retry rule (3 attempts, back-off 2 s / 4 s); a
  case still failing is recorded with its error class and no predicted events, counted in
  an `errored_cases` field of the results, and the run continues (one rate-limit response
  must not throw away a paid run); each case's run is traced with `run_name` as Langfuse session/tag; writes
  `<output-dir>/<run-name>.json` (metrics + per-case predictions) and prints a summary.
  Default `--posts-per-month 1050` = the SPEC's observed ~35 posts a day.

### Evaluation set and metrics (binding for steps 16, 17, 20)

Case (one JSON object per line): `id` (str: X ID or `syn-NNN`), `author_handle`, `text`,
`created_at` (ISO UTC), `is_repost`, `is_reply`, `split` (`dev`/`test`), `synthetic`,
`reviewed`, `tags` (list from: `international_injury`, `national_lineup`,
`womens_lineup`, `cup_european_lineup`, `multi_player`, `ambiguous_name`, `nickname`,
`accented_name`), `expected_events` (list of `mention`, `fpl_id | null`, `event_type`,
`certainty`). Snapshot season = `2026/27`.

Metrics (`metrics.py`, pure functions over expected vs predicted per case):

- Event key = `("id", fpl_id)` when linked, else `("mention", normalise(mention))`; an event
  matches on (key, event_type); per-case multiset intersection = TP; micro precision,
  recall, F1 over all cases (0 when the denominator is 0).
- Linking accuracy: expected events with an `fpl_id` are paired with a predicted event
  whose normalised mention equals it or whose token set contains / is contained in it, or
  whose `fpl_id` equals it; accuracy = paired with equal `fpl_id` / paired; unpaired
  expected mentions count against recall only.
- False-alarm rate = cases with no expected events and ≥ 1 predicted event / cases with no
  expected events.
- Certainty accuracy over TP pairs + a 3×3 confusion table (expected × predicted).
- Latency p50/p95 (nearest-rank) of per-case flow time; mean input/output tokens and cost
  per post; projected monthly cost in PLN = mean cost per post × posts per month ×
  `USD_PLN_RATE`; `passes` = F1 ≥ 0.85 and linking ≥ 0.95 and false alarms ≤ 0.05 and
  projected cost ≤ 5 PLN.

Evaluation set v1 content (step 20): every post in the local `tweet` table at
implementation time (107 today) as real cases, plus ~25 synthetic cases for rare
categories (Premier League leaked XIs with substitutes, `doubt` phrasing, `likely` /
`rumour` wording, nicknames, accented names, cup/European line-ups) — synthetic handles
are `synthetic_*`, texts invented. The candidate labels are written by the implementer
(a model pre-labelling, as the SPEC allows — no provider key is configured locally), all
with `reviewed: false`; the owner reviews them (manual scenario 1). Composition rules the
test enforces: ≥ 100 real and 20–40 synthetic cases; dev share of all cases between 25 %
and 35 %; in the test split every event type and every certainty level ≥ 5 times; ≥ 3
cases each tagged `international_injury` (with expected events), `national_lineup`,
`womens_lineup`, `cup_european_lineup` (with no expected events); ≥ 1 case each with
`is_repost`, `multi_player`, `ambiguous_name`, `nickname`, `accented_name`; ids unique;
every expected `fpl_id` exists in the snapshot.

### Variants considered

- Structured output: `with_structured_output(include_raw=True)` per provider vs. asking
  for JSON text and parsing it ourselves — chosen the former: typed outputs per ADR 0001,
  and `include_raw` keeps the token usage.
- Retries: our own loop with `max_retries=0` in the SDK vs. the SDK's retries — chosen our
  own: `attempts` must be stored exactly and the back-off must stop on shutdown.
- New-post pickup: polling the table every 2 s vs. an event from the poller thread —
  chosen polling: no coupling between the loops, posts stored by any path (CLI, a restart)
  are picked up, the query is one indexed lookup.
- Fake LLM: a `BaseChatModel` subclass implementing `bind_tools` vs. mocking the flow's
  call site — chosen the subclass: callbacks fire as for a real model, which AC3's test
  needs.

## AC → steps matrix

| AC | Steps | Proving test | Red before the change |
|----|-------|--------------|-----------------------|
| AC1 | 2, 3 | `tests/extraction/test_config.py`, `tests/extraction/test_providers.py` | |
| AC2 | 13 | `tests/worker/test_cli.py::test_run_without_llm_logs_extraction_disabled_once` | |
| AC3 | 8 | `tests/extraction/test_tracing.py` | |
| AC4 | 8, 13 | `tests/extraction/test_tracing.py::test_no_handler_without_keys`, `tests/worker/test_cli.py::test_run_without_langfuse_warns_once` | |
| AC5 | 2, 11, 13 | `tests/extraction/test_config.py::test_errors_never_carry_values`, `tests/extraction/test_service.py::test_credentials_never_logged_or_stored`, `tests/worker/test_cli.py::test_worker_rejects_llm_provider_without_key` | |
| AC6 | 5, 7 | `tests/extraction/test_flow.py::test_typed_result`, `::test_no_events_is_empty_result` | |
| AC7 | 7 | `tests/extraction/test_flow.py::test_leaked_xi_gives_starters_and_benched`, `::test_out_and_starts_gives_two_events` | |
| AC8 | 4, 20 | `tests/content/test_prompts.py::test_extraction_prompt_states_relevance_rule`, `tests/extraction/evaluation/test_eval_set.py::test_relevance_categories` | |
| AC9 | 7, 10 | `tests/extraction/test_flow.py::test_repost_is_extracted_with_author`, `tests/extraction/test_store.py::test_current_extraction_exposes_post_flags` | |
| AC10 | 6 | `tests/extraction/test_linking.py` | |
| AC11 | 7 | `tests/extraction/test_flow.py::test_disambiguation_*` | |
| AC12 | 6 | `tests/extraction/test_linking.py::test_alias_links`, `::test_stale_alias_ignored_and_logged_once`, `::test_committed_aliases_parse` | |
| AC13 | 6 | `tests/extraction/test_players.py::test_only_latest_season` | |
| AC14 | 9, 10, 11 | `tests/extraction/test_store.py::test_save_extraction_roundtrip`, `tests/extraction/test_service.py` | |
| AC15 | 10 | `tests/extraction/test_store.py::test_reextraction_keeps_history_current_is_latest_extracted` | |
| AC16 | 9 | `tests/db/test_migrations.py::test_extraction_migration_adds_only_new_tables` | |
| AC17 | 12, 13 | `tests/worker/test_cli.py::test_polls_continue_while_extraction_blocks` | |
| AC18 | 12 | `tests/extraction/test_loop.py::test_new_post_picked_within_5_s`, `::test_oldest_first` | |
| AC19 | 11, 12 | `tests/extraction/test_service.py::test_retries_*`, `tests/extraction/test_loop.py::test_failure_does_not_stop_the_loop` | |
| AC20 | 11, 14 | `tests/extraction/test_service.py::test_worker_extraction_records_latency`, `tests/worker/test_cli.py::test_status_shows_extraction*` | |
| AC21 | 13 | `tests/worker/test_cli.py::test_sigterm_with_extraction_exits_within_10_s` | |
| AC22 | 15 | `tests/extraction/test_cli.py::test_reextract_*` | |
| AC23 | 16, 20 | `tests/extraction/evaluation/test_cases.py`, `tests/extraction/evaluation/test_eval_set.py` | |
| AC24 | 18, 19 | `tests/extraction/test_cli.py::test_prelabel_*`, `::test_evaluate_refuses_unreviewed` | |
| AC25 | 17, 19 | `tests/extraction/evaluation/test_metrics.py`, `tests/extraction/test_cli.py::test_evaluate_*` | |
| AC26 | — | manual | manual — the owner's review and comparison run |
| AC27 | 22 | `tests/extraction/test_config.py::test_default_model_matches_adr_0006` | |
| AC28 | 2, 21 | `tests/test_env_example.py::test_every_extraction_setting_*`, `tests/test_readme.py` (extended) | |

## Steps

Every step writes its tests first and runs them red, then the change. Test commands run
from `backend/`.

### Group 1 — Dependencies, configuration, providers, prompts

- [x] 1. Add the dependencies with exact pins: `uv add` `langgraph`, `langchain-core`,
      `langchain-google-genai`, `langchain-openai`, `langchain-anthropic`, `langfuse`, then
      rewrite each to `==<resolved version>` in `backend/pyproject.toml` and `uv lock`.
      Test `tests/extraction/test_dependency.py` asserts each installed version equals the
      pin read from `pyproject.toml` (pattern: `tests/tweets/sources/test_dependency.py`).
      Files: `backend/pyproject.toml`, `backend/uv.lock`, `backend/tests/extraction/__init__.py`,
      `backend/tests/extraction/test_dependency.py`.
      Automatic verification: `cd backend && uv lock --check && uv run pytest -q tests/extraction/test_dependency.py && docker build -f Dockerfile -t presser-004 .. && docker run --rm presser-004 python -c "import langgraph, langfuse, langchain_google_genai, langchain_openai, langchain_anthropic"`
- [x] 2. `ExtractionSettings` and `app/extraction/config.py` (see Configuration). Tests:
      disabled when `LLM_PROVIDER` empty; each provider resolves with its key; unknown
      provider and each missing key → `ConfigError` naming the variable; overrides;
      `resolve_tracing` with both / one / no keys; `test_errors_never_carry_values` (set
      keys to a sentinel, assert the sentinel is absent from every error message). Add the
      ten variables to `backend/.env.example` as empty placeholders under an "Extraction —
      optional" block, and extend `tests/test_env_example.py` with an
      `ExtractionSettings` field → variable mapping and the same three checks as for
      `TweetSettings`.
      Files: `backend/app/core/settings.py`, `backend/app/extraction/__init__.py`,
      `backend/app/extraction/config.py`, `backend/tests/extraction/test_config.py`,
      `backend/.env.example`, `backend/tests/test_env_example.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_config.py tests/test_env_example.py tests/core/test_settings.py`
- [x] 3. `app/extraction/providers.py` (see Providers). Tests build each provider's model
      with a dummy key offline and assert the class, model name, `temperature == 0`,
      retries off, the OpenRouter base URL and `structured_kwargs`;
      `test_reasoning_models_get_no_temperature` (`openai` + `gpt-5-nano` and `openrouter` +
      `openai/o4-mini` → temperature not set; `openai` + `gpt-4.1-mini` → 0); no network.
      Files: `backend/app/extraction/providers.py`, `backend/tests/extraction/test_providers.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_providers.py`
- [x] 4. Prompts and the loader: `app/content/__init__.py` with `load_prompt(name)`
      (reads `prompts/<name>.md`, parses the `version: N` header, raises on a missing
      header or file); `extraction.md` (the four event types with definitions, the three
      certainty levels with the SPEC's wording cues, the relevance rule verbatim in
      substance, one event per player, the player named as written, the team only when
      stated, an empty list for irrelevant posts, reposts treated like posts) and
      `link_disambiguation.md`. Tests: header parsing, missing header fails, the
      extraction prompt names every event type and certainty level and the relevance terms
      (international duty, national team, women's, cup, European, next Premier League
      match) — keyword checks, not whole-text comparison.
      Files: `backend/app/content/__init__.py`, `backend/app/content/prompts/extraction.md`,
      `backend/app/content/prompts/link_disambiguation.md`, `backend/tests/content/__init__.py`,
      `backend/tests/content/test_prompts.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/content/test_prompts.py`
- [x] 5. `app/extraction/schemas.py` (see Flow) and the test doubles in
      `tests/extraction/fakes.py`: `FakeChatModel(BaseChatModel)` with a script of
      responses (a Pydantic object or dict → returned as a tool call named after the bound
      schema, with `usage_metadata`; an `Exception` → raised; a `threading.Event` → block
      until set), implementing `bind_tools` so the default `with_structured_output` works;
      it records the messages it received. `RecordingHandler(BaseCallbackHandler)` records
      `on_chat_model_start` (run id, parent run id, metadata, messages) and `on_llm_end`
      (token usage). Test that the fake returns `parsed` and `raw.usage_metadata` through
      `with_structured_output(ExtractionOutput, include_raw=True)`, and that a malformed
      script entry gives a `parsing_error`.
      Files: `backend/app/extraction/schemas.py`, `backend/tests/extraction/fakes.py`,
      `backend/tests/extraction/test_fakes.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_fakes.py && uv run ruff check . && uv run ruff format --check .`

### Group 2 — Linking and the flow

- [ ] 6. `app/extraction/linking.py` and `aliases.toml` (see Linking). Tests on a synthetic
      player table (invented players, including an "Ødegaard"-like accented name, two
      players sharing a surname in different teams, a web name like "B.Fernandes"):
      `test_accent_insensitive` ("Odegaard" → the "Ødegaard" player), web / first / last /
      full name, case-insensitive, `test_team_narrows`, `test_team_filter_empty_keeps_all`,
      `test_unknown_team_no_narrowing`, ambiguous → several candidates, no match → [];
      `test_alias_links`; `test_stale_alias_ignored_and_logged_once` (caplog: one line for
      two index builds); `test_committed_aliases_parse` (the real file parses, every entry
      has the required keys). DB test `tests/extraction/test_players.py::test_only_latest_season`
      (players in `2025/26` and `2026/27` → only `2026/27` loaded). For the committed
      aliases, look the FPL IDs up in the local `player` table (season `2026/27`).
      Files: `backend/app/extraction/linking.py`, `backend/app/extraction/aliases.toml`,
      `backend/tests/extraction/test_linking.py`, `backend/tests/extraction/test_players.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_linking.py tests/extraction/test_players.py`
- [ ] 7. `app/extraction/flow.py` (see Flow). Tests with `FakeChatModel` and a synthetic
      index: `test_typed_result`; `test_no_events_is_empty_result`;
      `test_leaked_xi_gives_starters_and_benched`; `test_out_and_starts_gives_two_events`;
      `test_repost_is_extracted_with_author` (the prompt the fake received carries the
      author handle and the repost flag); `test_unique_match_makes_no_llm_call`
      (`llm_calls == 1`); `test_disambiguation_picks_candidate`;
      `test_disambiguation_outside_list_unlinked`; `test_disambiguation_none_unlinked`;
      `test_disambiguation_error_unlinked` (event still returned, mention kept);
      `test_no_candidate_unlinked_without_call`; `test_usage_summed_over_calls`;
      `test_invalid_output_raises` (`ExtractionOutputError`).
      Files: `backend/app/extraction/flow.py`, `backend/tests/extraction/test_flow.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_flow.py`
- [ ] 8. `app/extraction/tracing.py`: `make_handler(tracing: TracingConfig | None)` →
      Langfuse's LangChain `CallbackHandler` (client built with the explicit keys and host,
      per the installed SDK's API) or `None`; `run_config(x_id, prompt_version, provider,
      model, handler, run_name=None) -> RunnableConfig` (callbacks, `run_name`
      `"extraction"`, metadata `x_id`, `prompt_version`, `provider`, `model`, and for an
      evaluation run the Langfuse session/tag keys set to `run_name`); `flush(handler)` for
      shutdown. Tests: with `RecordingHandler` in the config, a post with one ambiguous
      mention → two `on_chat_model_start` records (extraction, disambiguation), both with
      `x_id` and `prompt_version` in metadata, both descending from the same root run
      (one trace), two `on_llm_end` with token usage; `test_no_handler_without_keys`;
      `test_handler_built_offline` (fake keys, host `http://127.0.0.1:9`, no exception,
      nothing sent during the test).
      Files: `backend/app/extraction/tracing.py`, `backend/tests/extraction/test_tracing.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_tracing.py tests/extraction/test_flow.py`

### Group 3 — Storage and the extraction service

- [ ] 9. Data migration (separate step): models `Extraction`, `ExtractionEvent` in
      `app/extraction/models.py` (see Tables); migration `0004_extraction.py`
      (`down_revision = "0003"`, creates both tables and indexes; downgrade drops them);
      `import app.extraction.models` in `migrations/env.py` and in
      `tests/db/test_migrations.py`. Tests: `test_extraction_migration_adds_only_new_tables`
      (upgrade to `0003`, seed bootstrap + a `job_run` + a `tweet`, snapshot the other
      tables, upgrade head, tables present and others unchanged, downgrade -1, tables
      gone and others unchanged — pattern `test_tweet_migration_adds_only_new_tables`);
      update the table-set exclusions in `test_job_run_migration_keeps_collector_data` and
      `test_tweet_migration_adds_only_new_tables` to also exclude `extraction` and
      `extraction_event`; `test_models_match_migration` must stay green. Then apply locally:
      `uv run alembic upgrade head` (local database only).
      Files: `backend/app/extraction/models.py`, `backend/migrations/versions/0004_extraction.py`,
      `backend/migrations/env.py`, `backend/tests/db/test_migrations.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_migrations.py && uv run alembic upgrade head && uv run alembic current`
- [ ] 10. `app/extraction/store.py`: `save_extraction(session, record, events)`;
      `next_pending(session) -> PostInput | None` (oldest `created_at`, `x_id` with no
      extraction); `posts_for_reextract(session, x_id=None, since=None, until=None,
      failed=False) -> list[PostInput]` (failed = latest extraction per post is `failed`,
      one `DISTINCT ON` query); `current_extraction(session, x_id) -> CurrentExtraction |
      None` (latest `extracted`, one query joined with `tweet`, carrying
      `author_handle`, `is_repost`, `is_reply` and the events); `extraction_status(engine)
      -> (waiting, failed_posts, latest)`. DB tests:
      `test_save_extraction_roundtrip` (every AC14 field, events linked and unlinked);
      `test_reextraction_keeps_history_current_is_latest_extracted` (extracted, failed,
      extracted later → 3 rows, current = the last extracted; a later failed does not
      replace it); `test_current_extraction_exposes_post_flags`; `test_next_pending_oldest_first`;
      `test_posts_for_reextract_selectors`; `test_extraction_status_counts`.
      Files: `backend/app/extraction/store.py`, `backend/tests/extraction/test_store.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_store.py`
- [ ] 11. `app/extraction/pricing.py` + `prices.toml`, and `app/extraction/service.py`:
      `extract_post(engine, runtime, post, clock, stop_event, handler, record_latency) ->
      StoredOutcome` — builds the index (`load_players`), runs the flow with the run config,
      retries (see Worker), stores `extracted` / `failed` with `started_at` / `finished_at`
      taken from `clock.now()` (so the loop's fake-clock tests measure them), tokens, cost, attempts,
      prompt version, latency when `record_latency`. Tests (DB + `FakeChatModel`):
      `test_success_first_attempt`; `test_retries_then_success` (attempts = 2, sleeps
      [2]); `test_retries_exhausted_stores_failed` for a provider-like error, a timeout, a
      rate-limit error and a validation failure (attempts = 3, sleeps [2, 4],
      `error_class`); `test_stop_between_attempts_stores_nothing`;
      `test_cost_null_without_price`, `test_cost_from_price_table`;
      `test_worker_extraction_records_latency`; `test_credentials_never_logged_or_stored`
      (the fake raises an exception whose message holds a sentinel key; caplog and every
      stored column are free of it).
      Files: `backend/app/extraction/pricing.py`, `backend/app/extraction/prices.toml`,
      `backend/app/extraction/service.py`, `backend/tests/extraction/test_service.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_service.py tests/extraction/test_store.py`

### Group 4 — Worker loop, status and the re-extraction CLI

- [ ] 12. `app/extraction/loop.py`: `ExtractionLoop`, `start_extractor(engine, runtime,
      stop_event, clock=None) -> Thread` (name `extractor`, daemon, `Shutdown` swallowed
      like `start_poller`); the handler built once per loop and flushed on exit. Tests (DB,
      a scripted clock): `test_new_post_picked_within_5_s` (a post inserted during an idle
      sleep; its extraction's `started_at` − the insert time ≤ 5 s on the fake clock);
      `test_oldest_first`; `test_failure_does_not_stop_the_loop` (first post fails 3×,
      second extracted); `test_db_error_survives_iteration`; `test_stops_on_stop_event`.
      Files: `backend/app/extraction/loop.py`, `backend/tests/extraction/test_loop.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_loop.py`
- [ ] 13. Worker integration in `app/worker/cli.py` (see Worker): `ExtractionRuntime`,
      `WorkerDeps.extraction`, `_deps_from_settings` resolving `ExtractionSettings`
      (config errors → exit 1 like the ingest), start after the lock, the shared 5 s join
      deadline, the two log lines. Tests in `tests/worker/test_cli.py`:
      `test_run_without_llm_logs_extraction_disabled_once` (no extraction rows, FPL jobs and
      polls as before); `test_run_without_langfuse_warns_once`;
      `test_worker_rejects_llm_provider_without_key` (exit 1, variable named, sentinel
      value absent) and an unknown-provider variant;
      `test_polls_continue_while_extraction_blocks` (copy of
      `test_polls_continue_while_a_deadline_snapshot_blocks`: a pending tweet, a
      `FakeChatModel` blocking on an event, poller on `FakeClock` in the GW6 window → 9
      successful polls 20 s apart before the release);
      `test_sigterm_with_extraction_exits_within_10_s` (extractor idle and extractor
      blocked in a call: exit 0, elapsed < 10 s).
      Files: `backend/app/worker/cli.py`, `backend/tests/worker/test_cli.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/worker/test_cli.py tests/extraction`
- [ ] 14. `status` output (see Worker). Tests: `test_status_shows_extraction_disabled`,
      `test_status_shows_extraction_never`, `test_status_shows_extraction_counts_and_latest`
      (waiting, failed posts, latest line with latency).
      Files: `backend/app/worker/cli.py`, `backend/tests/worker/test_cli.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/worker/test_cli.py -k status`
- [ ] 15. `app/extraction/cli.py` + `__main__.py` with `reextract` (see CLI); deps injected
      through `ctx.obj` (`ExtractionCliDeps(engine, settings, build_spec, clock)`). Tests
      (DB + fake): `test_reextract_by_x_id` (a new extraction row, the old kept, summary
      lines), `test_reextract_range`, `test_reextract_failed_only`,
      `test_reextract_with_other_model` (provider/model recorded),
      `test_reextract_requires_one_selector`, `test_reextract_config_error_names_variable`,
      `test_help`.
      Files: `backend/app/extraction/cli.py`, `backend/app/extraction/__main__.py`,
      `backend/tests/extraction/test_cli.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_cli.py && uv run python -m app.extraction --help`

### Group 5 — Evaluation tooling, evaluation set v1 and documentation

- [ ] 16. `app/extraction/evaluation/cases.py`: the `EvalCase` / `ExpectedEvent` Pydantic
      models (see Evaluation set), `load_cases(path)`, `write_cases(path, cases)`,
      `composition_problems(cases, snapshot) -> list[str]` (the rules above); plus
      `snapshot-players` in the CLI. Tests: schema accepts/rejects (bad split, bad event
      type, missing field); each composition rule fails on a crafted small set;
      `snapshot-players` writes the latest season (DB).
      Files: `backend/app/extraction/evaluation/__init__.py`,
      `backend/app/extraction/evaluation/cases.py`, `backend/app/extraction/cli.py`,
      `backend/tests/extraction/evaluation/__init__.py`,
      `backend/tests/extraction/evaluation/test_cases.py`, `backend/tests/extraction/test_cli.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/evaluation/test_cases.py tests/extraction/test_cli.py`
- [ ] 17. `app/extraction/evaluation/metrics.py` (see Evaluation set and metrics). Tests
      on synthetic predictions with hand-computed values: perfect run; one missed event
      (recall), one extra event (precision), wrong event type; unlinked match by mention;
      linking accuracy with a wrong `fpl_id` and with an unpaired mention; false alarms on
      empty cases; certainty accuracy and the confusion table; p50/p95 on a known list;
      projected PLN cost; `passes` true/false at the thresholds' edges (F1 exactly 0.85
      passes).
      Files: `backend/app/extraction/evaluation/metrics.py`,
      `backend/tests/extraction/evaluation/test_metrics.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/evaluation/test_metrics.py`
- [ ] 18. `prelabel` command (see CLI). Tests (DB + fake): writes candidates with
      `reviewed: false`, the split rule, expected events from the linked result; skips X IDs
      already in the set; `--limit`; config error without a key.
      Files: `backend/app/extraction/cli.py`, `backend/tests/extraction/test_cli.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_cli.py -k prelabel`
- [ ] 19. `app/extraction/evaluation/runner.py` + the `evaluate` command (see CLI): runs
      the flow per case against the snapshot index (no database needed), timing each case,
      and calls `metrics`. Tests (fake model, a tiny case file and snapshot in `tmp_path`):
      `test_evaluate_refuses_unreviewed` (exit 1, count printed, no model call);
      `test_evaluate_requires_pln_rate`; `test_evaluate_writes_results` (JSON has every
      AC25 metric, per-case predictions, run name, provider, model, prompt version);
      `test_evaluate_only_selected_split`; `test_evaluate_case_error_is_recorded_and_run_continues`
      (a fake raising on every attempt for one case: `errored_cases == 1`, the other cases
      scored); `test_evaluate_traces_with_run_name`
      (`RecordingHandler` metadata carries the run name).
      Files: `backend/app/extraction/evaluation/runner.py`, `backend/app/extraction/cli.py`,
      `backend/tests/extraction/test_cli.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_cli.py -k evaluate`
- [ ] 20. Evaluation set v1 data (see Evaluation set): write the proving test
      `tests/extraction/evaluation/test_eval_set.py` first (`test_schema`,
      `test_composition` via `composition_problems` → `[]`, `test_relevance_categories`,
      `test_fpl_ids_in_snapshot`) and run it red (files missing); then
      `uv run python -m app.extraction snapshot-players --output evals/extraction/v1/players-2026-27.json`,
      export every local post (`x_id`, handle, text, `created_at`, flags) into cases, label
      each one (expected events per the SPEC's definitions and relevance rule, `tags`),
      add the synthetic cases, assign splits (real by the `x_id % 10` rule; each synthetic
      case's split chosen so that both the test-split minimums hold and the dev share of
      all cases lands in 25–35 % — the `x_id % 10` rule alone gives ~30 % of the real cases
      with a spread of several points, so an all-test synthetic block could push the share
      below 25 %), all `reviewed: false`. No real
      manager or league data enters the file (posts are public news posts).
      Files: `backend/evals/extraction/v1/cases.jsonl`,
      `backend/evals/extraction/v1/players-2026-27.json`,
      `backend/evals/extraction/results/.gitkeep`,
      `backend/tests/extraction/evaluation/test_eval_set.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/evaluation/test_eval_set.py`
- [ ] 21. Documentation: `docs/DEPLOYMENT.md` (a new optional step after the tweet ingest:
      the ten variables, placeholders only, Langfuse EU host, `status` output, credentials
      only in Railway variables), README `## Development` (running the extraction in the
      worker, `reextract`, `snapshot-players`, `prelabel`, the review flow for
      `reviewed`, `evaluate` with an example, results location); `docs/BACKLOG.md` rows:
      the GW6 evaluation-set extension (P1, trigger: the GW6 deadline 2026-10-10 passed with
      the ingest running) and automatic fallback to a second provider (P2, trigger:
      extractions fail for a whole deadline window because of the provider). Extend
      `tests/test_readme.py` with `EXTRACTION_VARIABLES` and the command names
      (`app.extraction reextract`, `prelabel`, `evaluate`) for both the README and
      DEPLOYMENT checks.
      Files: `docs/DEPLOYMENT.md`, `README.md`, `docs/BACKLOG.md`, `backend/tests/test_readme.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/test_readme.py tests/test_env_example.py && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`

### Group 6 — Comparison results, ADR 0006 and the default model (after the owner's run)

- [ ] 22. Precondition: the owner has reviewed every case (`reviewed: true` for all) and
      committed or left in the working tree the result files
      `backend/evals/extraction/results/*.json` for at least one model per provider on the
      test split. If either is missing, end the chunk with `RESULT: ESCALATE` naming what
      is missing (a planned owner gate, not a failure). Then: write
      `docs/reports/extraction-eval-v1.md` from the result files (per-model table of every
      AC25 metric, pass/fail, cost; the set's actual composition; prompt version; the
      certainty confusion tables; the posts-per-month and PLN rate used);
      `docs/adr/0006-default-extraction-llm.md` (the cheapest model passing all four
      thresholds, or — none passing — the best one as interim default plus a BACKLOG row),
      with a line `Default model: <provider>:<model>`; a DECISIONS row linking ADR 0006;
      in `app/extraction/config.py` the default (`LLM_MODEL` empty and `LLM_PROVIDER`
      equal to the chosen provider → the chosen model; other providers still require
      `LLM_MODEL`); `.env.example` / DEPLOYMENT / README name the recommended provider and
      model; tick Stage 1 item 3 in `docs/ROADMAP.md`. Test
      `tests/extraction/test_config.py::test_default_model_matches_adr_0006` (parses the
      ADR's `Default model:` line, asserts `resolve_llm` with only that provider and key
      set returns that model) — red before the config change.
      Files: `docs/reports/extraction-eval-v1.md`, `docs/adr/0006-default-extraction-llm.md`,
      `docs/DECISIONS.md`, `docs/ROADMAP.md`, `docs/BACKLOG.md` (only if none passes),
      `backend/app/extraction/config.py`, `backend/tests/extraction/test_config.py`,
      `backend/.env.example`, `docs/DEPLOYMENT.md`, `README.md`,
      `backend/evals/extraction/results/*.json`.
      Automatic verification: `cd backend && uv run pytest -q tests/extraction/test_config.py tests/extraction/evaluation/test_eval_set.py && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`

## Risks and traps

- **Evaluation set size.** 107 posts are stored today (SPEC: "about 150"); the set takes
  every post stored at implementation time and the test requires ≥ 100 real cases. The
  international break gives few Premier League line-up events — the synthetic cases must
  carry `confirmed_starter` / `benched` to reach 5 per type and level in the test split.
- **Structured output per provider.** Gemini rejects some JSON-schema shapes (keep the
  schemas flat: `Literal`, `str | None`, lists of models — no `dict`, no unions of models);
  OpenRouter models without tool calling fail — `function_calling` is set, and a model that
  cannot do it simply fails the evaluation. `include_raw=True` returns errors in
  `parsing_error` instead of raising — the flow must turn that into `ExtractionOutputError`.
- **Temperature on reasoning models.** OpenAI's cheap current models (`gpt-5-*`, `o*`)
  reject `temperature=0` with a 400; without the `NO_TEMPERATURE_PREFIXES` exception the
  OpenAI leg of the comparison (AC26) would fail on its first call. If the owner picks
  another model that rejects the argument, the prefix tuple is the one place to extend.
- **Langfuse SDK 4.x.** The handler's import path and client construction differ from
  v2-era docs; build it from the installed package, pass keys explicitly, flush on worker
  shutdown and after `evaluate`/`reextract`. Its exporter thread must not delay the 10 s
  shutdown bound. Tests never send anything (unreachable host, `RecordingHandler` for
  assertions).
- **Signals and threads.** Only the main thread receives SIGTERM (`Shutdown` is raised
  there); the extractor stops through `stop_event`. A call blocked inside an SDK cannot be
  interrupted — the daemon thread is abandoned after the shared join deadline; the post has
  no row and is picked up on the next start. BACKLOG #10 (the unraisable warning) may also
  show in the copied concurrency test.
- **Existing migration tests** subtract fixed table sets; without adding `extraction` and
  `extraction_event` there, they fail at revisions where those tables do not exist.
- **Secrets.** SDK exceptions may embed request details; only `type(exc).__name__` is ever
  logged or stored. `SecretStr` everywhere; `.env.example` placeholders empty.
- **Accents.** NFKD does not decompose `ø`, `ł`, `đ`, `æ` — the explicit map is required
  for "Odegaard" → "Ødegaard".
- **Team narrowing** can remove the right player after a transfer — the fallback keeps all
  candidates when the filter empties the set.
- **Costs.** `prices.toml` values are dated; a missing entry gives a null cost and `n/a` in
  the report, which blocks the projected-cost threshold — the owner completes it before
  the comparison.
- **The owner gate in Group 6** is expected: the implementer escalates there if results
  are missing instead of inventing numbers.
- **Docker image.** The new packages enlarge the image; step 1 builds it to catch
  resolution problems on `python:3.12.8-slim-bookworm` early.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

1. `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
   — all green.
2. `docker compose up -d && cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
   — succeeds on the local database; `\dt` shows `extraction`, `extraction_event`.
3. Worker without LLM variables (only when `backend/.env` sets no `LLM_PROVIDER` —
   `env_ignore_empty=True` means an empty override cannot switch it off, and a configured
   key would extract every local post at real cost; otherwise record "skipped — LLM
   configured in .env"):
   `cd backend && timeout -s INT 20 uv run python -m app.worker run` — logs
   `extraction disabled` once, exits 0; `uv run python -m app.worker status` prints
   `Extraction: disabled`.
4. Worker with `LLM_PROVIDER=openai` and no key:
   `LLM_PROVIDER=openai uv run python -m app.worker status` — exit 1,
   `OPENAI_API_KEY must be set for LLM_PROVIDER=openai`.
5. `uv run python -m app.extraction --help`, `reextract --help`, `prelabel --help`,
   `evaluate --help` — list the options.
6. `uv run python -m app.extraction evaluate --split test --provider openai --model x`
   with the committed set (still `reviewed: false`) — exit 1 with the refusal message and
   no network call.
7. If an LLM key is present in `backend/.env` (optional, cost ≤ 0.10 PLN): run
   `reextract --x-id <one local post>` and check the new row with
   `uv run python -m app.worker status`. Otherwise record "skipped — no key".

Record the results in this section under "End-to-end verification results".

### Manual (performed by the owner)

1. Review every case in `backend/evals/extraction/v1/cases.jsonl`: fix labels, set
   `reviewed: true` (≈ 130 cases).
2. Put the keys in `backend/.env` (four providers + Langfuse EU), fill `prices.toml` for
   the candidate models and set `USD_PLN_RATE`; tune `extraction.md` on the dev split only
   (`evaluate --split dev`), bumping its `version`.
3. Run `evaluate --split test` for at least one cheap model from each provider (≤ 10 PLN
   total), leaving the result files in `backend/evals/extraction/results/`; then resume
   `/pipeline:ship 004` so Group 6 writes the report, ADR 0006 and the default.
4. Run the worker locally with the chosen model and Langfuse keys for one ingest window;
   check in Langfuse that each post is one trace with the model, prompt version, tokens,
   cost and the X ID, and that `status` shows the latest extraction.

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated (Stage 1 item 3); `docs/DECISIONS.md` ADR 0006 row;
      `docs/BACKLOG.md` rows; `docs/DEPLOYMENT.md` and README updated
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation: date, stage, question, decision)_

## Review log

### 2026-09-28 — /pipeline:plan-review

Anti-anchoring leads (SPEC read before the plan): providers behind one factory with our own
retries; lookup-first linking; add-only migration; a polling loop in its own thread; an
evaluation set that the implementer can only pre-label, with the model choice gated on the
owner's run. The plan takes the same route; the differences examined were the dev/test
split arithmetic, the evaluation runner's error handling and provider parameter quirks.

Findings (severity counted before the fixes):

| # | Severity | Finding | Change |
|---|----------|---------|--------|
| 1 | `major` | Providers set `temperature=0` on every model; OpenAI's current cheap models (`gpt-5-*`, `o*`) reject it with a 400, so the OpenAI leg of the AC26 comparison would fail on its first call. | Providers section: a `NO_TEMPERATURE_PREFIXES` exception (also for `openai/` models via OpenRouter); step 3 gains `test_reasoning_models_get_no_temperature`; a risk entry. |
| 2 | `minor` | Step 20 put synthetic cases "mostly to test" while the composition test requires a 25–35 % dev share; with ~107 real cases split by `x_id % 10` the share can fall below 25 %, leaving a red test with no permitted fix. | Step 20: each synthetic case's split is chosen to satisfy both the test-split minimums and the dev-share window. |
| 3 | `minor` | The `evaluate` runner did not say what happens when a case's model call fails; one rate-limit response could abort a paid comparison run. | CLI section: the service's retry rule per case, an errored case recorded with its error class and counted in `errored_cases`, the run continues; step 19 gains `test_evaluate_case_error_is_recorded_and_run_continues`. |
| 4 | `minor` | The order of `evaluate`'s checks was open, while E2E automatic 6 expects the unreviewed refusal with no key and no `USD_PLN_RATE` set. | CLI section: reviewed check → `USD_PLN_RATE` → LLM config, all before a model is built. |
| 5 | `minor` | AC18's test measures `started_at` on the fake clock, but the plan did not say the service takes timestamps from the injected clock. | Step 11: `started_at` / `finished_at` from `clock.now()`. |
| 6 | `minor` | E2E automatic 3 ("worker without LLM variables") cannot blank a value set in `backend/.env` (`env_ignore_empty=True`); after the owner adds keys (manual 2) it would extract every local post at real cost. | E2E 3 runs only when `.env` sets no `LLM_PROVIDER`, otherwise recorded as skipped. |

Checked and found correct (later stages need not repeat it):

- **Coverage:** AC1–AC28 each have steps and a named proving test; the matrix matches the
  steps; AC26 is the owner's manual run; AC27 is gated in Group 6 with a red-first test.
- **Compliance:** CONVENTIONS (prompts in `app/content/` loaded by name, keyword checks
  instead of whole-text comparison, fake model in `pytest`, evaluation outside `pytest`,
  DB tests on the container, exact pins, credentials only as `SecretStr`, error class only
  in logs) and DECISIONS rows 2026-09-26 (ADR 0001, LangChain interface, Langfuse, fake
  LLM, module layout, one worker) and the five spec-004 rows of 2026-09-28 — none broken.
  English prompts in `app/content/` are consistent with the rule (they are model
  instructions, not user-facing Polish content).
- **Code facts verified:** `tweet.x_id` is the BigInteger PK; `player` has the composite PK
  (season, fpl_id) and rows are never deleted (the FK cannot break the reference sync);
  migration `0003` is the head; `ConfigError` subclasses `CollectorError`, which
  `_deps_from_settings` already turns into exit 1; the tweet thread's `join(timeout=5)` is
  the spot the shared deadline replaces; the Dockerfile copies `backend/app` only, so
  `aliases.toml`, `prices.toml` and the prompts ship and `backend/evals/` does not; the
  cited existing tests exist; `compose.yaml` exists at the root.
- **Minimality:** no `rapidfuzz`; `tomllib` from the standard library; the ingest's config,
  loop, CLI and `DISTINCT ON` patterns reused; polling the table rather than coupling the
  loops.
- **Feasibility:** no forward dependencies between steps (schemas and fakes in Group 1
  before the flow; `cli.py` created in step 15 before steps 16, 18, 19 extend it); the
  migration is its own step, applied to the local database only; the SIGTERM bound is kept
  by one shared join deadline.
- **E2E:** automatic checks are runnable without keys or spend; the manual part is only
  what needs the owner's keys, judgement or money.
- **Testability:** every step has an `Automatic verification:` line with exact test paths.
- **Groups:** six groups, each ending with finished work; Group 6 is an intended owner gate
  (the implementer escalates when the review or result files are missing).
- **Test-first:** the matrix has the fourth column; every step writes its test first.
- **Owner summary:** the dependency and migration flags match the SPEC's owner decisions.
- **Language:** English throughout, as `language: en` requires.

Decision: the plan is ready — every AC is covered by a runnable proving test, the new
dependencies and the add-only migration are accepted in SPEC → "Owner decisions", and all
findings were fixable and fixed in the plan itself.

## Chunk notes

_(filled in by /pipeline:implement in chunk mode — one entry per chunk that ends at a group boundary)_

## Deviations

_(filled in by /pipeline:implement — every deviation from the plan with its rationale)_

## Final review

_(filled in by /pipeline:final-review)_
