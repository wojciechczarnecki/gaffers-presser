# PLAN 008 — Delivery adapter (e-mail first)

## Owner summary

- **Approach:** a new `app/delivery/` module: a channel-agnostic `Message` and `Channel`
  protocol, a Resend adapter over `httpx` and a `file` adapter writing `.eml` files, chosen by
  `DELIVERY_PROVIDER` with start-up validation. One `DeliveryService.send(key, kind, message)`
  writes a `delivery_log` row per idempotency key and holds that row's lock during the provider
  call, so a repeated or concurrent send with the same key never sends twice. The existing
  retry helper moves from `app/llm/retry.py` to `app/core/retry.py` and learns "which errors to
  retry" and "how long to wait" hooks, so delivery reuses it instead of a second retry loop.
  Then a `python -m app.delivery` CLI (`send-test`, `status`), a `Delivery:` line in the worker
  status, and the docs.
- **Main risks:** a database transaction stays open for the whole provider call (up to ~50 s
  with retries) — accepted for the guarantee against double sends; the existing migration tests
  list "tables that do not exist yet" and must learn the new table; config tests must not read
  the process environment (the cloud-session trap from the owner decisions).
- **New dependency:** no — `httpx` and the standard library `email` package (SPEC → "Owner
  decisions").
- **Data migration:** yes — `0007` adds the `delivery_log` table (new table only, no change to
  existing data); accepted in SPEC → "Owner decisions".
- **Manual scenarios for the owner:** 2 — a real `send-test` through Resend from Railway
  (`railway ssh`) landing in the inbox, and opening a `file`-adapter `.eml` in a mail client.

## Approach

What the plan rests on:

- `specs/008-delivery-adapter/SPEC.md` — read in full.
- `docs/CONVENTIONS.md` — read in full.
- `docs/DECISIONS.md` — searched for "retry", "delivery", "0004", "e-mail", "refactor a
  feature", "module", "testcontainers", "advisory": the 2026-10-01 delivery row is already
  recorded with this SPEC (nothing to add for it); the 2026-09-30 row puts "one retry-with-back-off
  helper" in `app/llm/retry.py`; the 2026-09-30 row "a refactor a feature needs is done in that
  feature's spec"; the 2026-09-29 row puts the clock in `app/core`.
- `docs/ROADMAP.md` — searched for "deliver", "e-mail", "webhook": Stage 2 item at line 48
  links this spec.
- `docs/BACKLOG.md` — searched for "deliver", "e-mail", "webhook" and read the table: highest
  number is 20, no webhooks item yet → the new item is #21.
- `docs/DEPLOYMENT.md` — searched for numbered steps: steps 1–10, optional features (7–10) use
  "With no X set, … disabled" wording → the new step is 11.
- `backend/.env.example` — read in full.
- Code read in full: `app/core/{settings,errors,clock,local_time}.py`, `app/tweets/config.py`,
  `app/tweets/sources/{__init__,base,twitterapi_io}.py`, `app/llm/retry.py`,
  `app/content/__init__.py`, `app/worker/cli.py`, `app/retrieval/{config,cli}.py`,
  `app/tweets/{models,store}.py`, `app/db/{engine,upsert,locks}.py`, `migrations/env.py`,
  `migrations/versions/0006_repost_author.py`; tests `tests/conftest.py`,
  `tests/db/test_migrations.py`, `tests/test_env_example.py`, `tests/test_deployment.py`,
  `tests/test_docs.py`, `tests/test_module_boundaries.py`, `tests/test_shared_code.py`,
  `tests/llm/test_retry.py`, `tests/tweets/payloads/__init__.py`; searched
  `tests/worker/test_cli.py` (the `cli` fixture, `_no_extraction_variables`, status tests) and
  `tests/test_readme.py` (the DEPLOYMENT checks).

Patterns reused:

- Adapter shape: `app/tweets/sources/base.py` (frozen dataclass + `Protocol` + typed errors
  derived from `CollectorError`), `twitterapi_io.py` (`httpx.Client` with injectable
  `transport`, `_retry_after` parsing, `httpx`/`httpcore` loggers at WARNING), factory in
  `app/tweets/sources/__init__.py`.
- Configuration: `pydantic-settings` class with `env_file=".env", extra="ignore",
  env_ignore_empty=True` and `SecretStr` (as `TweetSettings`), kept in the module like
  `app/retrieval/config.py`; a `resolve_…() -> Config | None` returning `None` when disabled and
  raising `ConfigError` naming the variable (as `app/tweets/config.py`).
- Tables: SQLModel with `utc_column()` from `app/fpl/models/columns.py`; store functions in a
  `store.py` taking an `Engine` (as `app/tweets/store.py`); `insert(...).on_conflict_do_nothing`
  from `sqlalchemy.dialects.postgresql`.
- CLI: Typer app with `get_deps(ctx)` injection (`ctx.obj` set by tests), `fail()`,
  `__main__.py` (as `app/retrieval/cli.py`); UTC times printed `%Y-%m-%dT%H:%M:%SZ`.
- Content: a TOML file next to `app/content/prompts/` read with `tomllib`, as
  `app/retrieval/evaluation/queries.py` reads `retrieval_query_templates.toml`.
- Tests: `MockTransport` against recorded payloads (`tests/tweets/sources/test_twitterapi_io.py`),
  `db` fixture from `tests/conftest.py`, migration test pattern in `tests/db/test_migrations.py`.

Choices with real alternatives:

1. **Retry (SPEC open question).** (a) Move `with_retries` to `app/core/retry.py` and add two
   optional hooks — `retryable(exc) -> bool` and `wait(exc, default_delay) -> float`; (b) a
   delivery-own retry loop. **Chosen (a):** the 2026-09-30 decision forbids a second retry loop
   ("one retry-with-back-off helper") and asks for the refactor in the spec that needs it;
   delivery is not an AI feature, so the helper belongs in `app/core` next to the clock. Existing
   callers pass no hooks and behave exactly as before. A DECISIONS row records the move
   (superseding the `retry.py` path in the 2026-09-30 row).
2. **At most one provider call per key, also concurrently (AC9, AC10).** (a) Insert-or-lock the
   row in one transaction and hold the row lock while calling the provider; (b) commit a
   `sending` claim first and send outside the transaction. **Chosen (a):** PostgreSQL itself
   releases the lock when a process dies, so there is no stuck `sending` state to expire; a
   concurrent second sender blocks on the unique index / `FOR UPDATE` and then sees `sent`. The
   cost — a transaction open for up to ~50 s — is negligible at this volume. A DECISIONS row
   records it.
3. **`attempts` on a re-tried `failed` row (AC10).** Cumulative over all sends of the key
   (row attempts += attempts of this call), so the row tells the whole history; `requested_at`,
   `accepted_at`, status, provider ID, error class and HTTP status describe the latest send.

Design (signatures where precision matters):

- `app/delivery/channels/base.py`:
  - `@dataclass(frozen=True) class Message: title: str; text: str; html: str | None = None` —
    `__post_init__` raises `InvalidMessageError(ValueError)` when `title.strip()` or
    `text.strip()` is empty (AC2: raised at construction, so before any provider call or row).
  - `class Channel(Protocol): name: str; def send(self, message: Message) -> str | None: ...;
    def close(self) -> None: ...`
  - errors (all `CollectorError` subclasses, each with `http_status: int | None`):
    `ChannelUnavailableError` (timeout, connection error, 5xx), `ChannelRateLimitedError`
    (429, `retry_after: float | None`), `ChannelRejectedError` (any other non-2xx),
    `ChannelPayloadError` (2xx with no readable `id`).
- `app/delivery/channels/resend.py`: `ResendChannel(api_key, email_from, email_to, transport=None,
  base_url="https://api.resend.com/", timeout=10.0)`, `name = "resend"`; one
  `POST emails` with `Authorization: Bearer <key>` and JSON `{"from", "to": [to], "subject":
  title, "text": text}` plus `"html"` only when present; returns `body["id"]`.
- `app/delivery/channels/file.py`: `FileChannel(directory: Path, clock: Clock)`, `name = "file"`;
  builds an `email.message.EmailMessage` (`From`/`To` fixed synthetic
  `presser@localhost.invalid` / `owner@localhost.invalid`, `Subject`, `Date`, `Message-ID`),
  `set_content(text)`, then `add_alternative(html, subtype="html")` or `make_alternative()` so
  it is always `multipart/alternative`; writes `<UTC %Y%m%dT%H%M%S%fZ>-<8 hex>.eml` with
  `as_bytes()`; creates the directory if missing; returns `None`.
- `app/delivery/config.py`: `DeliverySettings(BaseSettings)` fields `delivery_provider: str = ""`,
  `resend_api_key: SecretStr | None = None`, `delivery_email_to: str = ""`,
  `delivery_email_from: str = "onboarding@resend.dev"`, `delivery_file_dir: str = "./outbox"`;
  `@dataclass(frozen=True) DeliveryConfig(provider: str, settings: DeliverySettings)`;
  `resolve_delivery(settings) -> DeliveryConfig | None` (empty → `None`; unknown →
  `ConfigError("DELIVERY_PROVIDER must be one of: resend, file (empty disables delivery)")`;
  `resend` without `RESEND_API_KEY` / `DELIVERY_EMAIL_TO` →
  `ConfigError("<VARIABLE> must be set for DELIVERY_PROVIDER=resend")`).
  `app/delivery/channels/__init__.py`: `build_channel(config, clock) -> Channel`.
- `app/delivery/models.py`: `DeliveryLog` table `delivery_log`: `id` PK; `idempotency_key: str`
  (unique constraint `uq_delivery_log_idempotency_key`); `kind`, `channel`, `title`,
  `text_body`, `html_body: str | None`, `status` (`sent` | `failed`; `sending` exists only
  inside the uncommitted send transaction), `provider_message_id: str | None`, `attempts: int`,
  `requested_at` (`utc_column()`), `accepted_at` (nullable UTC), `error_class: str | None`,
  `http_status: int | None`; index `ix_delivery_log_requested_at`.
- `app/delivery/service.py`:
  - `Kind = Literal["alert", "presser", "test"]`;
    `@dataclass(frozen=True) SendOutcome(status: Literal["sent", "already_sent", "failed",
    "disabled"], log_id: int | None = None, provider_message_id: str | None = None,
    attempts: int = 0, requested_at: datetime | None = None, accepted_at: datetime | None = None,
    error_class: str | None = None, http_status: int | None = None)`.
  - `DeliveryService(engine, channel: Channel | None, clock: Clock, stop_event:
    threading.Event | None = None)`; `send(key: str, kind: Kind, message: Message) ->
    SendOutcome`. An empty key or unknown kind raises `ValueError` before anything (AC7).
    Disabled → `SendOutcome("disabled")`, no row, a module-level once-per-process
    `logger.info("delivery disabled: DELIVERY_PROVIDER is empty")` guarded by a lock.
  - Flow in one `Session` transaction: `INSERT … ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id` with status `sending`; then `SELECT … FOR UPDATE` the row; `sent` →
    `already_sent` from the row, nothing written; otherwise call
    `with_retries(lambda: channel.send(message), clock, stop_event or threading.Event(),
    what="delivery", retryable=…, wait=…)` and update the row before commit. A re-tried
    `failed` row also takes the channel, title, text and HTML of this send, so the row always
    holds the message that was last attempted.
  - Reading the `RetryOutcome`: success is `error is None and not stopped` — **never**
    `result is not None`, because the `file` channel returns `None` on success. `stopped`
    (the worker's stop event set between attempts) ends as `failed` with the class of the last
    error, or `DeliveryStopped` when no attempt was made; `attempts` is the outcome's count.
  - Retry policy: 3 attempts; `retryable` = `ChannelUnavailableError | ChannelRateLimitedError`;
    `wait` = `min(retry_after, 10.0)` for a rate limit with `Retry-After`, else
    `min(default, 10.0)` (defaults 2 s, 4 s); any other exception, including an unexpected one,
    is not retried and ends as `failed` with its class name — never raised to the caller.
  - One log line per send: `delivery kind=%s log_id=%s channel=%s status=%s attempts=%s
    error=%s` — nothing else (AC14).
- `app/delivery/store.py`: `recent_rows(engine, limit=10)`, `delivery_summary(engine, now) ->
  DeliverySummary(last_sent_at, last_sent_kind, failed_last_24h)`.
- `app/content/delivery_test_message.toml`: `title` and `text` (Polish), `text` with a `{time}`
  placeholder; `app/delivery/content.py: load_test_message(now) -> Message` formats `time`
  with `format_local` (Europe/Warsaw).
- `app/delivery/cli.py` + `__main__.py`: `DeliveryCliDeps(engine: Engine | None, config:
  DeliveryConfig | None, make_channel: Callable[[], Channel], clock: Clock)`. `send-test`:
  disabled → `error: delivery is disabled (DELIVERY_PROVIDER is empty)`, exit 1; else key
  `test:<UTC %Y%m%dT%H%M%SZ>-<8 hex>`, prints `status: <status>`, `provider id: <id or ->`,
  `log id: <id>`; exit 1 on `failed`. `status`: `channel: <name|disabled>`, then up to 10 rows
  `<requested_at UTC>  <kind>  <status>  attempts=<n>  provider_id=<id or ->` or `no sends yet`.
  A bad configuration fails on start (`fail(str(exc))`); the delivery configuration is
  resolved before the engine is built, so the error names the delivery variable even with no
  `DATABASE_URL`.
- Worker: `WorkerDeps.delivery_channel: str | None = None`; `_deps_from_settings` calls
  `resolve_delivery(DeliverySettings())` inside the existing `try`, before `load_settings()`
  (AC3 on worker start);
  `status` prints after the Extraction block either `Delivery: disabled` or
  `Delivery: <channel>  last sent: <UTC time> (<kind>)|never  failed in 24 h: <n>`.

## AC → steps matrix

| AC | Steps | Proving test | Red before the change |
|----|-------|--------------|-----------------------|
| AC1 | 2, 3, 4 | `tests/delivery/test_message.py::test_message_shape_and_channel_protocol`, `tests/delivery/channels/test_resend.py::test_sends_one_post_and_returns_the_id`, `tests/delivery/channels/test_file.py::test_returns_no_provider_id` | `uv run pytest -q tests/delivery/test_message.py::test_message_shape_and_channel_protocol` → n/a for the shape test (new type, written with its stub-free definition; the empty-message rows above are its red); adapters in steps 3, 4 |
| AC2 | 2, 7 | `tests/delivery/test_message.py::test_empty_title_or_text_is_rejected`, `tests/delivery/test_service.py::test_invalid_message_writes_no_row_and_calls_nothing` | `uv run pytest -q tests/delivery/test_message.py` → `Failed: DID NOT RAISE InvalidMessageError`; service test in step 7 |
| AC3 | 5, 9, 10 | `tests/delivery/test_config.py::test_provider_selects_or_disables`, `::test_unknown_provider_names_the_variable`, `tests/delivery/test_cli.py::test_bad_provider_fails_on_start`, `tests/worker/test_cli.py::test_bad_delivery_provider_fails_worker_start` | `uv run pytest -q tests/delivery/test_config.py` (stub `resolve_delivery` returning `None`) → `Failed: DID NOT RAISE ConfigError` (unknown provider), `assert None is not None` (file/resend selected); CLI test in step 9 (`tests/delivery/test_cli.py::test_bad_provider_fails_on_start` → `assert 0 == 1` with the stub commands); worker test in step 10: `uv run pytest -q tests/worker/test_cli.py::test_bad_delivery_provider_fails_worker_start` → `Failed: DID NOT RAISE <class 'click.exceptions.Exit'>` (reported as the `typer.Exit` raises check) |
| AC4 | 5 | `tests/delivery/test_config.py::test_resend_requires_key_and_recipient`, `::test_resend_from_defaults_to_testing_domain` | `uv run pytest -q tests/delivery/test_config.py` → `Failed: DID NOT RAISE ConfigError` (missing key / recipient), `assert None is not None` (from default) |
| AC5 | 4, 5 | `tests/delivery/test_config.py::test_file_dir_defaults_to_outbox`, `tests/delivery/channels/test_file.py::test_creates_missing_directory` | `uv run pytest -q tests/delivery/test_config.py::test_file_dir_defaults_to_outbox` → `assert None is not None`; `tests/delivery/channels/test_file.py::test_creates_missing_directory` red as in AC13 |
| AC6 | 7 | `tests/delivery/test_service.py::test_disabled_returns_disabled_writes_nothing_logs_once` | `uv run pytest -q tests/delivery/test_service.py::test_disabled_returns_disabled_writes_nothing_logs_once` (stub `send` returning `disabled`) → `assert len(notices) == 1` / `assert 0 == 1` |
| AC7 | 7 | `tests/delivery/test_service.py::test_key_and_kind_are_required_and_stored` | `uv run pytest -q tests/delivery/test_service.py::test_key_and_kind_are_required_and_stored` → `assert [] == [('alert:0', 'alert'), ...]` |
| AC8 | 7 | `tests/delivery/test_service.py::test_first_send_writes_full_row`, `::test_file_channel_success_with_no_provider_id_is_sent` | `uv run pytest -q tests/delivery/test_service.py::test_first_send_writes_full_row` → `ValueError: not enough values to unpack (expected 1, got 0)` (no row written by the stub) |
| AC9 | 7 | `tests/delivery/test_service.py::test_sent_key_is_not_sent_again` | `uv run pytest -q tests/delivery/test_service.py::test_sent_key_is_not_sent_again` → `assert 0 == 1` (provider calls) |
| AC10 | 7 | `tests/delivery/test_service.py::test_failed_key_is_retried_in_the_same_row`, `::test_concurrent_sends_make_one_provider_call` | `uv run pytest -q tests/delivery/test_service.py::test_failed_key_is_retried_in_the_same_row` → `assert 'disabled' == 'failed'`; `test_concurrent_sends_make_one_provider_call` → `assert False` (`entered.wait(10)`) |
| AC11 | 1, 7, 8 | `tests/core/test_retry.py::test_non_retryable_error_stops_at_once`, `::test_wait_hook_overrides_the_delay`, `tests/delivery/test_service.py::test_transient_errors_retried_three_times`, `::test_rate_limit_waits_retry_after_capped`, `::test_other_4xx_not_retried`, `::test_unexpected_error_becomes_failed`, `::test_stop_event_ends_as_failed` | `uv run pytest -q tests/core/test_retry.py` → `assert 3 == 1` (non-retryable), `assert [2.0, 4.0] == [21.0, 41.0]` (wait hook); service tests in step 8: `uv run pytest -q tests/delivery/test_service.py::test_transient_errors_retried_three_times` → `assert 1 == 3` (provider calls); `::test_rate_limit_waits_retry_after_capped` → `AssertionError: assert 'failed' == 'sent'` |
| AC12 | 3 | `tests/delivery/channels/test_resend.py` (200, 403, 422, 429 with `Retry-After`, 500, timeout) | `uv run pytest -q tests/delivery/channels/test_resend.py` (stub `send` returning `"stub"`) → `assert 'stub' == '49a3999c-0ce1-4ea6-ab68-afcd6dc2e794'`; `Failed: DID NOT RAISE ChannelRejectedError` (403/422), `ChannelRateLimitedError` (429), `ChannelUnavailableError` (500, timeout) |
| AC13 | 4 | `tests/delivery/channels/test_file.py::test_writes_multipart_eml_with_text_part`, `::test_html_part_when_present`, `::test_files_sort_by_send_time` | `uv run pytest -q tests/delivery/channels/test_file.py` (stub writing nothing) → `assert 0 == 1` (one .eml expected); `assert [] == ['Title 0', 'Title 1', 'Title 2']` |
| AC14 | 8 | `tests/delivery/test_service.py::test_logs_carry_no_addresses_title_body_or_key` | `uv run pytest -q tests/delivery/test_service.py::test_logs_carry_no_addresses_title_body_or_key` → `assert 0 == 2` (no per-send log line yet) |
| AC15 | 9 | `tests/delivery/test_cli.py::test_send_test_prints_outcome_and_provider_id`, `::test_send_test_disabled_exits_non_zero`, `tests/content/test_delivery_test_message.py::test_renders_with_time` | `uv run pytest -q tests/delivery/test_cli.py tests/content/test_delivery_test_message.py` (stub commands, stub loader) → `assert 'status: sent' in ''`, `assert 0 == 1` (disabled / failed exit codes), `assert '2026-10-01 12:30' in 'stub'` |
| AC16 | 9 | `tests/delivery/test_cli.py::test_status_prints_channel_and_last_rows` | `uv run pytest -q tests/delivery/test_cli.py::test_status_prints_channel_and_last_rows` (stub) → `IndexError: list index out of range` |
| AC17 | 10 | `tests/worker/test_cli.py::test_status_shows_delivery_line`, `::test_status_shows_delivery_disabled` | `uv run pytest -q tests/worker/test_cli.py -k delivery` (field added, no status line) → `assert 'Delivery: disabled' in [...]`, `assert 'Delivery: file  last sent: ...' in [...]` |
| AC18 | 6 | `tests/db/test_migrations.py::test_delivery_migration_adds_only_new_table` (plus the existing `test_models_match_migration`) | `uv run pytest -q tests/db/test_migrations.py::test_delivery_migration_adds_only_new_table` (no-op `0007` stub) → `assert 'delivery_log' in [...]`; `test_models_match_migration` → `assert [('add_table', Table('delivery_log' ...)] == []` |
| AC19 | 3 | `tests/delivery/test_synthetic_data.py::test_only_synthetic_addresses_and_keys` | n/a — guard over test files; nothing to be red against before they exist |
| AC20 | 11 | `tests/test_env_example.py::test_every_delivery_variable_is_an_empty_placeholder`, `tests/test_readme.py::test_deployment_documents_delivery`, `tests/test_docs.py::test_backlog_has_delivery_webhooks_entry` | `uv run pytest -q tests/test_env_example.py tests/test_readme.py tests/test_docs.py` → `AssertionError: assert set() == {'DELIVERY_EM...SEND_API_KEY'}`, `AssertionError: 'DELIVERY_PROVIDER' missing from docs/DEPLOYMENT.md`, and `ValueError: not enough values to unpack (expected 1, got 0)` for the backlog row |

## Steps

### Group 1 — Shared retry and the channels

- [x] 1. Move the retry helper to `app/core/retry.py` and add the hooks — files:
      `backend/app/core/retry.py` (new; content of `app/llm/retry.py` plus keyword-only
      `retryable: Callable[[Exception], bool] | None = None` — a `False` ends the loop at once
      with that error — and `wait: Callable[[Exception, float], float] | None = None` — the delay
      before the next attempt, given the default from `backoff`), delete `backend/app/llm/retry.py`,
      update imports in `app/extraction/service.py`, `app/llm/structured.py`,
      `app/retrieval/evaluation/runner.py`, `app/retrieval/indexing.py`,
      `tests/extraction/test_service.py`; move `tests/llm/test_retry.py` →
      `tests/core/test_retry.py` and add `test_non_retryable_error_stops_at_once`,
      `test_wait_hook_overrides_the_delay`; `tests/test_module_boundaries.py`
      (`test_shared_layer_lives_in_app_llm_and_core` also imports `app.core.retry.with_retries`
      and asserts `not (APP / "llm" / "retry.py").exists()`); `tests/test_shared_code.py` (add
      `"delivery"` to the packages checked for retry constants and retry loops — keep the
      chat-model checks on `extraction`/`retrieval`); `docs/DECISIONS.md` (row: the retry helper
      lives in `app/core/retry.py` with `retryable`/`wait` hooks, superseding the `retry.py` path
      in the 2026-09-30 row). Write the two new tests first and run them red (import error /
      unexpected keyword).
      Automatic verification: `cd backend && uv run pytest -q tests/core/test_retry.py tests/test_module_boundaries.py tests/test_shared_code.py tests/extraction/test_service.py tests/llm tests/retrieval/test_indexing.py tests/retrieval/evaluation/test_runner.py && uv run ruff check . && uv run ruff format --check .`
- [x] 2. Message, channel protocol and errors — files: `backend/app/delivery/__init__.py`,
      `backend/app/delivery/channels/__init__.py` (empty for now),
      `backend/app/delivery/channels/base.py`, tests `backend/tests/delivery/__init__.py`,
      `backend/tests/delivery/test_message.py` (`test_message_shape_and_channel_protocol`:
      fields are exactly `title`, `text`, `html`, `html` defaults to `None`, a fake channel
      satisfying `Channel` receives the same `Message` object; `test_empty_title_or_text_is_rejected`:
      `""` and whitespace-only for each raise `InvalidMessageError`). Tests first, red.
      Automatic verification: `cd backend && uv run pytest -q tests/delivery/test_message.py`
- [x] 3. Resend adapter against recorded responses — files:
      `backend/app/delivery/channels/resend.py`, `backend/tests/delivery/payloads/__init__.py`
      (`load(name)` reading plain `.json`), `backend/tests/delivery/payloads/resend-200.json`,
      `resend-403-testing-domain.json`, `resend-422.json`, `resend-429.json`, `resend-500.json`
      (bodies in Resend's documented error shape `{"statusCode", "name", "message"}`, synthetic
      addresses only), `backend/tests/delivery/channels/__init__.py`,
      `backend/tests/delivery/channels/test_resend.py` — via `httpx.MockTransport`: one
      `POST /emails`, bearer header, `from`, `to == [to]`, `subject == title`, `text`, `html`
      present only when given; 200 → returns the `id`; 403 and 422 →
      `ChannelRejectedError` with `http_status`; 429 + `Retry-After: 3` →
      `ChannelRateLimitedError(retry_after=3.0)`; 500 → `ChannelUnavailableError(http_status=500)`;
      `httpx.ReadTimeout` raised by the transport → `ChannelUnavailableError(http_status=None)`;
      200 without `id` → `ChannelPayloadError`. Also
      `backend/tests/delivery/test_synthetic_data.py` (every e-mail address in the git-tracked
      files under `backend/app/`, `backend/tests/`, `backend/.env.example` and `docs/` — today
      there are none — has a domain in `{example.com, example.test, localhost.invalid,
      resend.dev}`; no string matching `re_[A-Za-z0-9]{20,}` — the shape of a real Resend key —
      in those files; AC19 speaks of the repository, not only `tests/delivery/`).
      Tests first, red.
      Automatic verification: `cd backend && uv run pytest -q tests/delivery/channels/test_resend.py tests/delivery/test_synthetic_data.py`
- [x] 4. File adapter — files: `backend/app/delivery/channels/file.py`,
      `backend/tests/delivery/channels/test_file.py` (`tmp_path`, a fixed clock): parsing the
      file with `email.message_from_bytes(data, policy=email.policy.default)` gives
      `multipart/alternative`, `Subject == title`, `get_body(("plain",)).get_content()` equals the
      text (the `email` package appends one `\n` when the text lacks it — compare against
      `text if text.endswith("\n") else text + "\n"`), Polish characters survive; an HTML part
      only when `html` is given; two sends at later clock times sort by name in send order;
      a missing nested directory is created; `send` returns `None`. Tests first, red.
      Automatic verification: `cd backend && uv run pytest -q tests/delivery/channels/test_file.py`
- [x] 5. Configuration and the channel factory — files: `backend/app/delivery/config.py`,
      `backend/app/delivery/channels/__init__.py` (`build_channel`),
      `backend/tests/delivery/test_config.py`. Tests build settings with explicit values and
      `_env_file=None`, and an autouse fixture deletes every `DELIVERY_*` / `RESEND_*` variable
      from the process environment (monkeypatch) so a cloud session's variables cannot leak in.
      Cases: empty / unset provider → `None`; `resend` and `file` → config and the right channel
      class from `build_channel`; `smtp` → `ConfigError` naming `DELIVERY_PROVIDER` and both
      allowed values; `resend` without `RESEND_API_KEY`, without `DELIVERY_EMAIL_TO` → error
      naming that variable and not containing the other values given; `DELIVERY_EMAIL_FROM`
      defaults to `onboarding@resend.dev`; `DELIVERY_FILE_DIR` defaults to `./outbox` and
      resolves relative to the working directory (`monkeypatch.chdir(tmp_path)`). Tests first, red.
      Automatic verification: `cd backend && uv run pytest -q tests/delivery && uv run ruff check . && uv run ruff format --check .`

### Group 2 — Delivery log and the service

- [x] 6. Data migration: the `delivery_log` table — files: `backend/app/delivery/models.py`,
      `backend/migrations/versions/0007_delivery_log.py` (`revision = "0007"`,
      `down_revision = "0006"`, `create_table` with the unique constraint and the index;
      downgrade drops the table), `backend/migrations/env.py` (import `app.delivery.models`),
      `backend/tests/db/test_migrations.py`: import `app.delivery.models`; add `"delivery_log"`
      to the "not yet existing" table sets in `test_job_run_migration_keeps_collector_data`,
      `test_tweet_migration_adds_only_new_tables`, `test_extraction_migration_adds_only_new_tables`,
      `test_retrieval_migration_keeps_data_and_downgrades` and
      `test_repost_author_migration_backfills_and_downgrades` (they compute `other_tables` from
      the whole metadata and would query a table missing at their revision); new
      `test_delivery_migration_adds_only_new_table` (upgrade `0006`, seed a `job_run` and a
      `tweet` row, snapshot the other tables, upgrade `0007` → `delivery_log` exists with a
      unique index on `idempotency_key`, other tables unchanged; `downgrade -1` → table gone,
      other tables unchanged). Test first, red.
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_migrations.py`
- [x] 7. The service: log, idempotency, disabled — files: `backend/app/delivery/service.py`,
      `backend/app/delivery/store.py` (the insert-or-lock and update helpers; the status queries
      come in step 9), `backend/tests/delivery/fakes.py` (`FakeChannel` with a scripted list of
      results/exceptions and a `calls` list; `FixedClock` with recorded sleeps),
      `backend/tests/delivery/test_service.py` (uses the `db` fixture):
      `test_invalid_message_writes_no_row_and_calls_nothing`;
      `test_key_and_kind_are_required_and_stored` (empty key, kind `"other"` → `ValueError`, no
      row; valid kinds stored);
      `test_first_send_writes_full_row` (every AC8 column, `attempts == 1`, `requested_at` and
      `accepted_at` from the clock, outcome `sent` with the provider ID);
      `test_sent_key_is_not_sent_again` (second call: no channel call, row unchanged, outcome
      `already_sent` with the original ID and times);
      `test_failed_key_is_retried_in_the_same_row` (first call ends `failed` on a
      `ChannelRejectedError`, second call with a changed title sends: one row, status `sent`,
      `attempts` cumulative, `error_class` / `http_status` cleared, the row holds the second
      title);
      `test_file_channel_success_with_no_provider_id_is_sent` (a channel returning `None` →
      outcome and row `sent`, `provider_message_id` `None`);
      `test_stop_event_ends_as_failed` (stop event set while the first attempt fails → `failed`,
      no exception, the row committed as `failed`);
      `test_concurrent_sends_make_one_provider_call` (two threads, the channel's `send` blocks on
      a `threading.Event` until the test sees the second session waiting on a lock in
      `pg_stat_activity` (`wait_event_type = 'Lock'`, polled with a 10 s timeout), then releases;
      `FakeChannel.calls == 1`, outcomes `{sent, already_sent}`, one row);
      `test_disabled_returns_disabled_writes_nothing_logs_once` (channel `None`: two sends →
      `disabled`, no rows, exactly one "delivery disabled" record in `caplog`; a fixture resets
      the module-level once-flag). Tests first, red.
      Automatic verification: `cd backend && uv run pytest -q tests/delivery/test_service.py`
- [x] 8. Retries and log hygiene — files: `backend/app/delivery/service.py`,
      `backend/tests/delivery/test_service.py`: `test_transient_errors_retried_three_times`
      (`ChannelUnavailableError` ×3 → `failed`, `attempts == 3`, sleeps `[2.0, 4.0]`, row has
      `error_class == "ChannelUnavailableError"` and the HTTP status; unavailable then success →
      `sent` after 2 attempts); `test_rate_limit_waits_retry_after_capped` (`retry_after=3` →
      sleep 3.0; `retry_after=60` → 10.0; `None` → the default back-off);
      `test_other_4xx_not_retried` (`ChannelRejectedError(http_status=422)` → 1 attempt, no sleep,
      `failed`, `http_status == 422`); `test_unexpected_error_becomes_failed` (`RuntimeError` →
      `failed` outcome, `error_class == "RuntimeError"`, no exception raised);
      `test_logs_carry_no_addresses_title_body_or_key` (a real `ResendChannel` over
      `MockTransport` with a sentinel API key, sender, recipient, title, text and HTML; one
      success, one 500×3 failure; `caplog` at DEBUG for the root logger; none of the six
      sentinels in `caplog.text`; the record for each send carries kind, log ID, channel, status,
      attempts). Tests first, red.
      Automatic verification: `cd backend && uv run pytest -q tests/delivery && uv run ruff check . && uv run ruff format --check .`

### Group 3 — CLI, worker status and documentation

- [x] 9. Test message content and the delivery CLI — files:
      `backend/app/content/delivery_test_message.toml` (Polish `title` and `text` with `{time}`),
      `backend/app/delivery/content.py`, `backend/app/delivery/store.py` (`recent_rows`,
      `delivery_summary`), `backend/app/delivery/cli.py`, `backend/app/delivery/__main__.py`,
      `backend/tests/content/test_delivery_test_message.py` (`test_renders_with_time`: the
      loaded message has a non-empty title and the Warsaw-formatted time in its text; no
      whole-text comparison), `backend/tests/delivery/test_cli.py` (CliRunner with injected
      `DeliveryCliDeps`, `db` fixture, `FakeChannel`):
      `test_send_test_prints_outcome_and_provider_id` (exit 0, `status: sent`, the provider ID,
      a row with kind `test` and a key starting `test:`; two runs → two different keys);
      `test_send_test_failed_exits_non_zero`; `test_send_test_disabled_exits_non_zero` (says
      delivery is disabled, no row);
      `test_status_prints_channel_and_last_rows` (12 seeded rows → the newest 10 in order with
      time, kind, status, attempts, provider ID; no title, body or address in the output;
      `channel: disabled` when disabled; `no sends yet` on an empty log);
      `test_bad_provider_fails_on_start` (env `DELIVERY_PROVIDER=smtp`, no injected deps →
      exit 1, message names `DELIVERY_PROVIDER`). Every test here runs under
      `monkeypatch.chdir(tmp_path)` (no `backend/.env` read) with `DELIVERY_*` / `RESEND_*`
      removed from the environment, as in step 5; plus a `subprocess` smoke run of
      `python -m app.delivery --help` (exit 0). Tests first, red.
      Automatic verification: `cd backend && uv run pytest -q tests/delivery/test_cli.py tests/content/test_delivery_test_message.py`
- [x] 10. Worker: start-up validation and the `Delivery:` status line — files:
      `backend/app/worker/cli.py`, `backend/tests/worker/test_cli.py`: extend the autouse
      fixture's regex with `DELIVERY_.*` (`.*_API_KEY` already covers `RESEND_API_KEY`); the
      `cli` fixture gains `delivery_channel=None`; `test_status_shows_delivery_disabled`;
      `test_status_shows_delivery_line` (seed a `sent` row 2 h ago kind `presser`, a newer
      `failed` row 1 h ago, a `failed` row 25 h ago → `Delivery: file  last sent:
      <time> (presser)  failed in 24 h: 1`; with no rows → `last sent: never`);
      `test_bad_delivery_provider_fails_worker_start` (env `DELIVERY_PROVIDER=smtp`,
      `worker_cli._deps_from_settings()` → `typer.Exit` with the message naming the variable,
      in the style of the existing `_deps_from_settings` tests, under
      `monkeypatch.chdir(tmp_path)`). Existing status tests that
      slice the output by `"Extraction: disabled"` keep passing because the Delivery line comes
      after the Extraction block. Tests first, red.
      Automatic verification: `cd backend && uv run pytest -q tests/worker/test_cli.py`
- [x] 11. Documentation — files: `.gitignore` (`backend/outbox/`, so `.eml` files from local
      `file`-adapter runs are never committed), `backend/.env.example` (a "Delivery — optional" block:
      `DELIVERY_PROVIDER=`, `RESEND_API_KEY=`, `DELIVERY_EMAIL_TO=`, `DELIVERY_EMAIL_FROM=`,
      `DELIVERY_FILE_DIR=` with comments: empty disables, allowed values, the testing-domain
      default sender and its owner-only recipient limit, the `./outbox` default),
      `docs/DEPLOYMENT.md` (step 11 "Delivery (optional)": the variables, Resend free-plan and
      testing-domain limits (sender `onboarding@resend.dev`, recipient only the Resend account
      owner's address, 403 otherwise), `railway ssh` then `python -m app.delivery send-test` and
      `python -m app.delivery status`, the `Delivery:` line in `python -m app.worker status`),
      `docs/BACKLOG.md` (#21, P3: Resend delivery webhooks (delivered, bounced, opened); trigger:
      alerts reported as sent never reach the inbox), `docs/DECISIONS.md` (row: at most one
      provider call per idempotency key through the row lock held during the call, chosen over a
      committed `sending` claim), tests `backend/tests/test_env_example.py`
      (`_DELIVERY_FIELD_TO_VARIABLE` covering `DeliverySettings.model_fields`;
      `test_every_delivery_variable_is_an_empty_placeholder`), `backend/tests/test_readme.py`
      (`test_deployment_documents_delivery`: the five variables, `send-test`, `onboarding@resend.dev`
      in DEPLOYMENT.md), `backend/tests/test_docs.py` (`test_backlog_has_delivery_webhooks_entry`:
      one row mentioning "webhook", `P3`, non-empty trigger). Tests first, red.
      Automatic verification: `cd backend && uv run pytest -q tests/test_env_example.py tests/test_readme.py tests/test_docs.py && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`

## Risks and traps

- **Environment leaking into tests.** Cloud sessions set provider variables in the process;
  every delivery config/CLI/worker test must delete `DELIVERY_*` and `RESEND_*` and pass
  `_env_file=None` where it builds settings (the owner decided the analogous tweet tests are
  fixed separately — do not touch them here).
- **Migration tests' table sets.** Five existing tests derive "other tables" from the whole
  metadata; without `"delivery_log"` in their exclusions they query a table that does not exist
  at their revision.
- **Long transaction.** The row lock spans the provider call; keep the HTTP timeout at 10 s and
  the retry waits capped at 10 s so the worst case stays near 50 s. Never call the provider
  outside that transaction.
- **Unique-index wait.** A concurrent `INSERT … ON CONFLICT DO NOTHING` blocks until the first
  transaction ends and then inserts nothing; the following `SELECT … FOR UPDATE` must run in
  the same transaction so it sees the committed row (READ COMMITTED does this per statement).
- **Retry move.** `tests/extraction/test_service.py` imports `RETRY_BACKOFF_SECONDS` from the
  old path; grep for `app.llm.retry` after the move must return nothing. Existing callers pass
  no hooks — behaviour unchanged.
- **Logs.** `httpx` logs request lines at INFO; set the `httpx` and `httpcore` loggers to
  WARNING in the Resend adapter (as `twitterapi_io.py`) and never log exception messages, only
  class names.
- **`.eml` text part.** The `email` package appends a trailing newline; compare accordingly
  rather than weakening the check.
- **Times.** All stored times UTC-aware; Warsaw only in the test message text.
- **`RetryOutcome.result` is not the success signal.** The `file` channel returns `None` on
  success; decide by `error is None and not stopped` (see Design).
- **Timeout after acceptance.** AC11 retries a timeout; when Resend accepted the request but
  the response was lost, the retry sends a second e-mail. Accepted by the SPEC (retry on
  timeout, the interface carries the message only); Resend's `Idempotency-Key` header would
  close it but needs the key in the channel interface — a question for the owner at the final
  review, not a change here.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

Against a throw-away PostgreSQL (the same image as the tests):

```bash
docker run -d --rm --name e2e-008 -e POSTGRES_PASSWORD=e2e -p 55432:5432 pgvector/pgvector:pg16
export DATABASE_URL=postgresql+psycopg://postgres:e2e@localhost:55432/postgres
cd backend && until uv run alembic upgrade head; do sleep 1; done
OUT=$(mktemp -d)
DELIVERY_PROVIDER=file DELIVERY_FILE_DIR=$OUT uv run python -m app.delivery send-test   # exit 0, "status: sent", "provider id: -"
ls $OUT/*.eml | wc -l                                                                     # 1
uv run python -c "import email,email.policy,glob,sys; m=email.message_from_bytes(open(glob.glob(sys.argv[1]+'/*.eml')[0],'rb').read(),policy=email.policy.default); print(m.get_content_type(), m['Subject'])" $OUT   # multipart/alternative <title>
DELIVERY_PROVIDER=file DELIVERY_FILE_DIR=$OUT uv run python -m app.delivery status        # "channel: file", one row: test sent attempts=1
DELIVERY_PROVIDER=file DELIVERY_FILE_DIR=$OUT uv run python -m app.worker status | grep '^Delivery:'   # "Delivery: file  last sent: … (test)  failed in 24 h: 0"
env -u DELIVERY_PROVIDER uv run python -m app.delivery send-test; echo $?                 # disabled message, 1 (the session may set DELIVERY_PROVIDER)
DELIVERY_PROVIDER=smtp uv run python -m app.delivery status; echo $?                      # error naming DELIVERY_PROVIDER, 1
DELIVERY_PROVIDER=resend uv run python -m app.worker status; echo $?                      # error naming RESEND_API_KEY, 1
uv run alembic downgrade -1 && uv run alembic upgrade head                                 # both succeed
docker stop e2e-008
```

Record the outputs in this section. Then `<verify.command>` in full.

Performed 2026-10-01 against a throw-away `pgvector/pgvector:pg16` container (port 55432). The
migration commands ran as `alembic -x url=<literal localhost URL> …` because the command guard
refuses `alembic` with a URL taken from an environment variable. Outputs:

```
$ send-test (file)
status: sent
provider id: -
log id: 1
exit=0
$ ls eml
1
$ parse
multipart/alternative Konferencja prasowa: wiadomość testowa
Trener melduje: kanał dostawy działa.
Ta wiadomość poszła z konferencji prasowej o 2026-10-01 23:48 czasu warszawskiego.
Jeśli ją czytasz, alerty i konferencja trafią w to samo miejsce.
$ status
channel: file
2026-10-01T21:48:45Z  test  sent  attempts=1  provider_id=-
$ worker status
Delivery: file  last sent: 2026-10-01T21:48:45Z (test)  failed in 24 h: 0
$ disabled
error: delivery is disabled (DELIVERY_PROVIDER is empty)
exit=1
$ smtp
error: DELIVERY_PROVIDER must be one of: resend, file (empty disables delivery)
exit=1
$ resend no key
error: RESEND_API_KEY must be set for DELIVERY_PROVIDER=resend
exit=1
$ alembic downgrade -1 && alembic upgrade head
Running downgrade 0007 -> 0006, delivery log
Running upgrade 0006 -> 0007, delivery log
```

Result: all expected outcomes (exit codes, `status: sent`, `provider id: -`, one multipart
`.eml`, both status outputs, disabled/bad-provider/missing-key errors naming the variable,
downgrade and upgrade). `<verify.command>`: final full run: 1136 passed (ruff check and format clean).

### Manual (performed by the owner)

1. On Railway set `DELIVERY_PROVIDER=resend`, `RESEND_API_KEY`, `DELIVERY_EMAIL_TO` (the Resend
   account owner's address); after deploy, `railway ssh` → `python -m app.delivery send-test`:
   the e-mail arrives in the inbox with the Polish test text and the Warsaw time;
   `python -m app.delivery status` shows the row with a provider ID (AC15, AC12 live).
2. Locally, run `send-test` with `DELIVERY_PROVIDER=file` and open the `.eml` from `./outbox`
   in a mail client: subject and Polish text display correctly (AC13 "opens in a mail client").

## Definition of Done

- [x] all steps ticked
- [x] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` fully green
- [x] end-to-end verification (automatic) performed, result recorded here
- [x] `docs/ROADMAP.md` updated (Stage 2 delivery item ticked); `docs/DECISIONS.md`
      (retry helper move, row-lock idempotency), `docs/DEPLOYMENT.md`, `docs/BACKLOG.md`,
      `backend/.env.example` updated
- [x] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation: date, stage, question, decision)_

- 2026-10-01 — final review (GATE 2, /pipeline:ship): decisions on findings F1–F4.
  - F1 (`worth-fixing`, a retry after a lost Resend reply sends a duplicate e-mail) —
    accepted: the delivery idempotency key goes to the channel and to Resend as the
    `Idempotency-Key` header; the AC1 wording change is accepted; a test against a recorded
    request asserts the header.
  - F2 (`nit`, `Retry-After` of `-1`/`nan`) — rejected.
  - F3 (`nit`, no service test for a stop after a failed attempt) — rejected.
  - F4 (`nit`, `deviations_minor` disagrees with `## Deviations`) — rejected.

## Review log

### 2026-10-01 — plan review (/pipeline:plan-review under /pipeline:ship)

Findings (severity counted before the fixes: 0 `blocker`, 1 `major`, 6 `minor`):

1. `major` — the service design did not say how a `RetryOutcome` becomes `sent`/`failed`.
   `with_retries` returns `result=None` both on failure and on a successful `file`-channel
   send (no provider ID), and `stopped=True` with possibly no error when the stop event fires;
   a `result is not None` check would record every `file` send as failed, and a stopped send
   had no defined row state. Fixed: Design → "Reading the `RetryOutcome`" (success =
   `error is None and not stopped`; stopped → `failed` with the last error class or
   `DeliveryStopped`), `stop_event or threading.Event()` (the helper requires one), new tests
   `test_file_channel_success_with_no_provider_id_is_sent` and `test_stop_event_ends_as_failed`
   in step 7, matrix rows AC8 and AC11 updated, a Risks entry.
2. `minor` — a re-tried `failed` row kept the first send's title and bodies, so the log could
   hold a message different from the one delivered. Fixed: the re-try also writes channel,
   title, text and HTML; `test_failed_key_is_retried_in_the_same_row` changes the title.
3. `minor` — AC3 tests could report `DATABASE_URL` instead of `DELIVERY_PROVIDER` if the
   engine/settings were built first, and the CLI/worker tests could read `backend/.env`.
   Fixed: delivery configuration resolved before `load_settings()` / the engine; steps 9 and
   10 run under `monkeypatch.chdir(tmp_path)` with the variables removed.
4. `minor` — the AC19 guard scanned only `tests/delivery/`, while AC19 speaks of the
   repository. Fixed: step 3 scans the tracked files under `backend/app/`, `backend/tests/`,
   `backend/.env.example` and `docs/` (checked: no e-mail address in them today, so no false
   positives).
5. `minor` — local `file`-adapter runs (manual scenario 2) write `backend/outbox/`, which was
   not ignored. Fixed: step 11 adds `backend/outbox/` to `.gitignore`.
6. `minor` — the E2E "disabled" run assumed `DELIVERY_PROVIDER` unset in the session. Fixed:
   `env -u DELIVERY_PROVIDER`.
7. `minor` — not recorded: a timeout after Resend accepted the request leads to a second
   e-mail on retry. It follows from AC11 and AC1 (the channel receives the message only), so
   it is a Risks entry and a question for the final review (Resend `Idempotency-Key`), not a
   plan change.

Checked and found correct (later stages need not repeat it):

- Coverage: every AC1–AC20 has steps and a proving test; the matrix matches the steps; the
  fourth column is present (AC19 `n/a` justified; AC13 "opens in a mail client" and live
  AC12/AC15 in the manual scenarios).
- Compliance: CONVENTIONS (content in `app/content/` as TOML, no whole-text comparison,
  recorded-payload `MockTransport` tests, UTC storage, no addresses/credentials in logs, tests
  first). DECISIONS searched for retry, delivery, 0004, module, clock, advisory, refactor: the
  2026-10-01 delivery row is already recorded; moving `with_retries` to `app/core/retry.py`
  honours the 2026-09-30 "one retry helper" and "refactor in the feature's spec" rows, with a
  superseding row planned; the row-lock idempotency gets its own row.
- Retry move: the importers listed in step 1 are exactly the ones in the tree
  (`extraction/service.py`, `llm/structured.py`, `retrieval/evaluation/runner.py`,
  `retrieval/indexing.py`, `tests/extraction/test_service.py`, `tests/llm/test_retry.py`);
  the test paths in its verification command exist; `test_shared_code.py` tolerates a missing
  `app/delivery/` at step 1 (`rglob` yields nothing). The helper's own warning logs only the
  class name — consistent with AC14.
- Concurrency (AC10): `INSERT … ON CONFLICT DO NOTHING` blocks on the unique index until the
  first transaction ends; the following `SELECT … FOR UPDATE` in READ COMMITTED sees the
  committed row; a crashed sender's rollback frees the key. The `pg_stat_activity`
  `wait_event_type = 'Lock'` probe matches a transaction-ID wait.
- Migration (AC18): `0007` after `0006`; the five existing tests deriving `other_tables` from
  the whole metadata are all listed; the change is a new table only and is accepted in SPEC →
  Owner decisions. No new dependency (`httpx`, standard library `email`).
- Worker: `status` ends with the Extraction block, so the `Delivery:` line after it does not
  break the existing slicing; the autouse regex `.*_API_KEY` already covers `RESEND_API_KEY`.
- Docs: BACKLOG highest number is 20 → #21; DEPLOYMENT steps end at 10 → step 11; ROADMAP
  Stage 2 item at line 48 is the one to tick.
- Groups: three groups, each ending with its work complete; `implement.chunked` is false.
- E2E: the automatic part runs against a throw-away pgvector container and covers send, file
  content, both status outputs, disabled, bad provider and missing key, downgrade/upgrade; the
  manual part holds only what needs a real inbox or a mail client.
- Owner summary: dependency "no" and migration "yes, accepted" match the plan; language `en`.

Decision: the plan is ready — no blocker remains, the only new schema is the accepted
migration, there is no new dependency, and every AC has a step and a proving test.

## Chunk notes

_(filled in by /pipeline:implement in chunk mode — one entry per chunk that ends at a group boundary)_

## Deviations

_(filled in by /pipeline:implement — every deviation from the plan with its rationale)_

- Step 6 (minor): `test_repost_author_migration_backfills_and_downgrades` upgraded to `head` and then
  ran `downgrade -1`, assuming `0006` is the head; with `0007` that would undo the wrong
  revision. The test now upgrades to `"0006"` explicitly. Its assertions are unchanged.

### Converge pass 1 — 2026-10-01

The `Agent` tool is not available to this stage agent, so no fresh subagent could be started;
the pass was done by the implementer reading the SPEC's ACs against `git diff origin/main...HEAD`
(excluding the spec directory). AC1–AC20 each map to code and a passing proving test (matrix
above). Gaps found: 0. `unrequested`: only the `error class:` line `send-test` prints on a
non-`sent` outcome and the `test_status_disabled_and_empty_log`, `test_module_entry_point_runs`
tests, all within AC15/AC16's behaviour. No steps added, so no second pass is required.

## Final review

### 2026-10-01 — report (/pipeline:final-review under /pipeline:ship)

Material: `git diff origin/main...HEAD` (55 files). The `Agent` tool is not available to this
stage agent, so the three perspectives (SPEC/PLAN compliance, quality and maintainability,
tests) were run by the reviewer one after another rather than as independent subagents.
`<verify.command>`: ruff check and format clean; pytest 1135 passed, 1 failed, 1 error — the
two were `tests/worker/test_cli.py::test_failing_embedder_does_not_stop_polls_or_extraction`
and `::test_sigterm_with_embedder_blocked_in_a_call_exits_within_10_s`, timing tests this
branch does not touch; they passed in three isolated reruns and in a run of the whole
`tests/worker/test_cli.py` (48 passed), so this is load-dependent flakiness, not a defect of
this feature (for the apply stage: watch them in CI, BACKLOG if they flake there).
`workflow_metrics.py --check`: clean.

AC → evidence:

| AC | Code | Proving tests |
|----|------|---------------|
| AC1 | `app/delivery/channels/base.py` (`Message`, `Channel`) | `tests/delivery/test_message.py`, `channels/test_resend.py::test_sends_one_post_and_returns_the_id`, `channels/test_file.py::test_returns_no_provider_id` |
| AC2 | `Message.__post_init__` | `test_message.py::test_empty_title_or_text_is_rejected`, `test_service.py::test_invalid_message_writes_no_row_and_calls_nothing` |
| AC3 | `app/delivery/config.py`, `delivery/cli.py::_deps_from_settings`, `worker/cli.py::_deps_from_settings` | `test_config.py::test_provider_selects_or_disables`, `::test_unknown_provider_names_the_variable`, `test_cli.py::test_bad_provider_fails_on_start`, `worker/test_cli.py::test_bad_delivery_provider_fails_worker_start` |
| AC4 | `resolve_delivery` | `test_config.py::test_resend_requires_key_and_recipient`, `::test_resend_from_defaults_to_testing_domain` |
| AC5 | `DeliverySettings.delivery_file_dir`, `FileChannel` | `test_config.py::test_file_dir_defaults_to_outbox`, `channels/test_file.py::test_creates_missing_directory` |
| AC6 | `service.py::_note_disabled` | `test_service.py::test_disabled_returns_disabled_writes_nothing_logs_once` |
| AC7 | `DeliveryService.send` (key, kind checks) | `test_service.py::test_key_and_kind_are_required_and_stored` |
| AC8 | `store.py::claim_row`, `service.py` | `test_service.py::test_first_send_writes_full_row`, `::test_file_channel_success_with_no_provider_id_is_sent` |
| AC9 | `service.py` (`already_sent` path) | `test_service.py::test_sent_key_is_not_sent_again` |
| AC10 | `claim_row` (`ON CONFLICT DO NOTHING` + `FOR UPDATE`) | `test_service.py::test_failed_key_is_retried_in_the_same_row`, `::test_concurrent_sends_make_one_provider_call` |
| AC11 | `app/core/retry.py` hooks, `service.py::_retryable`/`_wait` | `core/test_retry.py::test_non_retryable_error_stops_at_once`, `::test_wait_hook_overrides_the_delay`, `test_service.py::test_transient_errors_retried_three_times`, `::test_rate_limit_waits_retry_after_capped`, `::test_other_4xx_not_retried`, `::test_unexpected_error_becomes_failed`, `::test_stop_event_ends_as_failed` |
| AC12 | `channels/resend.py` | `channels/test_resend.py` (200, 403, 422, 429 ± `Retry-After`, 500, timeout, connection error, missing id) |
| AC13 | `channels/file.py` | `channels/test_file.py` (multipart, text part, HTML part, sort order, same-instant, directory); opening in a mail client — manual scenario 2 |
| AC14 | `DeliveryService._log`, `httpx`/`httpcore` at WARNING | `test_service.py::test_logs_carry_no_addresses_title_body_or_key` |
| AC15 | `delivery/cli.py::send_test`, `app/content/delivery_test_message.toml` | `test_cli.py::test_send_test_prints_outcome_and_provider_id`, `::test_send_test_failed_exits_non_zero`, `::test_send_test_disabled_exits_non_zero`, `content/test_delivery_test_message.py::test_renders_with_time`; live Resend — manual scenario 1 |
| AC16 | `delivery/cli.py::status`, `store.py::recent_rows` | `test_cli.py::test_status_prints_channel_and_last_rows`, `::test_status_disabled_and_empty_log` |
| AC17 | `worker/cli.py::status`, `store.py::delivery_summary` | `worker/test_cli.py::test_status_shows_delivery_line`, `::test_status_shows_delivery_disabled`, `::test_status_shows_delivery_line_with_no_rows` |
| AC18 | `migrations/versions/0007_delivery_log.py` | `db/test_migrations.py::test_delivery_migration_adds_only_new_table`, `::test_models_match_migration` |
| AC19 | — | `test_synthetic_data.py::test_only_synthetic_addresses_and_keys` |
| AC20 | `.env.example`, `docs/DEPLOYMENT.md` step 11, `docs/DECISIONS.md` (3 rows), `docs/BACKLOG.md` #21 | `test_env_example.py::test_every_delivery_variable_is_an_empty_placeholder`, `test_readme.py::test_deployment_documents_delivery`, `test_docs.py::test_backlog_has_delivery_webhooks_entry` |

Plan steps 1–11 are ticked with the code and tests in the branch; the one recorded deviation
(step 6, the repost migration test pinned to `0006`) is justified; nothing outside the scope
got in (extra tests stay within AC12, AC13, AC15, AC16, AC3). Every row of the AC → steps
matrix has its red record or an `n/a` with a reason.

Findings:

- **F1** `worth-fixing` — `backend/app/delivery/channels/resend.py:61` (and
  `channels/base.py:27`). Resend accepts the `POST /emails`, the response is lost (read
  timeout or a dropped connection) → `ChannelUnavailableError` → the retry sends a second,
  identical e-mail; the delivery log, whose purpose is "a repeated send sends nothing", records
  one `sent` row for two delivered e-mails. Flagged by the plan (Risks, review log item 7) as a
  question for the owner at this review. Fix: pass the delivery idempotency key to the channel
  (e.g. `Channel.send(message, key)`; the key is not e-mail specific, so the interface stays
  channel-agnostic) and send it as Resend's `Idempotency-Key` header, with a test asserting the
  header is the same on every attempt; this changes AC1's "receives exactly this", so it needs
  the owner's acceptance. Alternative: defer to BACKLOG (P3, trigger: a duplicate alert or
  presser e-mail is observed).
- **F2** `nit` — `backend/app/delivery/channels/resend.py:17` and
  `backend/app/delivery/service.py:58`. A 429 with `Retry-After: -1` or `nan` gives
  `retry_after=-1.0`/`nan`, `_wait` passes it through `min(…, 10.0)`, and `SystemClock.sleep`
  → `time.sleep` raises `ValueError` outside the `try` in `with_retries`, so `send()` raises to
  the caller and the transaction rolls back — against AC11's "a `failed` outcome rather than an
  exception". Unlikely with Resend's integer header. Fix: `_retry_after` returns a value only
  when it is finite and `>= 0` (else `None`), and `_wait` clamps with `max(0.0, …)`; a
  parametrised case in `test_resend.py`.
- **F3** `nit` — `backend/tests/delivery/test_service.py:153`.
  `test_stop_event_ends_as_failed` sets the stop event before the first attempt, so only the
  `DeliveryStopped` branch is covered; the branch the plan describes, "stop set while the first
  attempt fails → `failed` with the class of the last error" and the attempts counted, is not
  tested at service level (a regression there would leave a row with `error_class` from the
  wrong branch). Fix: a `FakeChannel` whose first result sets the stop event and raises
  `ChannelUnavailableError` → outcome `failed`, `error_class == "ChannelUnavailableError"`,
  `attempts == 1`, no sleep.
- **F4** `nit` — `specs/008-delivery-adapter/SPEC.md` frontmatter `deviations_minor: 4`, while
  `## Deviations` records one minor deviation (step 6). The metric and the record disagree, so
  the workflow report over-counts deviations. Fix: set `deviations_minor: 1`, or record the
  other three deviations in `## Deviations`.

Rejected:

- An e-mail sent but no log row when the database commit fails after the provider accepted
  (connection lost during the up to ~50 s transaction) — the chosen and recorded design
  (DECISIONS 2026-10-01, row lock held during the call); the residual risk was accepted there.
- A title with a line break makes the `file` channel's `EmailMessage` raise `ValueError` — it
  ends as a `failed` outcome with `error_class == "ValueError"`, not a crash; titles come from
  later specs' templates.

Left out: 0 nit findings.

### 2026-10-01 — apply (/pipeline:final-review under /pipeline:ship)

Owner decisions: F1 accepted; F2, F3, F4 rejected (see `## Owner decisions`).

Fixed:

- **F1** → `Channel.send(message, idempotency_key)` in `app/delivery/channels/base.py`; the
  service passes its key on every attempt (`service.py`); `ResendChannel` sends it as the
  `Idempotency-Key` header (`channels/resend.py`); `FileChannel` accepts and ignores it (the
  service log already deduplicates; a file write has no lost reply). AC1 in SPEC.md reworded;
  the 2026-10-01 delivery row in `docs/DECISIONS.md` names the key in the interface. Tests:
  `channels/test_resend.py::test_sends_one_post_and_returns_the_id` asserts the header on the
  recorded 200 request, `::test_every_attempt_carries_the_same_idempotency_key` (read timeout,
  then 200: both requests carry the same key and the same body),
  `test_service.py::test_retry_after_a_lost_reply_reuses_the_idempotency_key` (service +
  Resend adapter over `MockTransport`) and `::test_transient_errors_retried_three_times`
  (the fake channel sees the same key on each attempt).
  Residual, not verified against Resend: its idempotency keys expire after 24 hours, and a
  re-try of a `failed` key with a changed message may be answered with a 4xx by Resend
  (a different payload under one key) — that ends as `failed`, not a duplicate.

`<verify.command>` after the fixes: ruff check and format clean; pytest 1138 passed.

