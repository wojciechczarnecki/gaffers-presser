# PLAN 003 — Tweet sources, ingest and latency measurement

## Owner summary

- **Approach:** A new business module `app/tweets/` holds a `TweetSource` interface with
  three adapters (twscrape, twitterapi.io, official X API v2) that all return the same
  normalised post, two new tables (posts keyed by X ID, a poll log), a pure polling schedule
  (20 s in the 90 min before a deadline, 30 min otherwise) and a polling loop. The existing
  worker starts that loop in its own thread once it holds the schedule lock, so a long FPL
  job never delays a poll; with no `TWEET_SOURCE` set the worker behaves exactly as today.
  A `python -m app.tweets measure` command polls every configured source side by side and
  writes per-post detection latency; `summary` turns that file into the per-source table for
  the report and the ADR.
- **Main risks:** twscrape depends on X's internal GraphQL and a logged-in account — it can
  break or get the dedicated account locked (the measurement tells us); twscrape ships
  opt-out telemetry, which the adapter switches off; the two loops share one process, so
  shutdown must stop the polling thread within the existing 10 s bound; the official X API
  bills per post read, so its adapter asks for small pages.
- **New dependency:** yes — `twscrape==0.20.1` (the scraper adapter; pulls in `aiosqlite`,
  `beautifulsoup4`, `fake-useragent`, `loguru`, `py-machineid`, `pyotp`). Accepted in SPEC →
  "Owner decisions". The official X API adapter uses the existing `httpx` instead of tweepy
  or `xdk` — one fewer dependency than the owner accepted (reason under "Approach").
- **Data migration:** yes — `0003_tweets` adds the tables `tweet` and `tweet_poll` with
  their indexes; no existing table changes. Accepted in SPEC → "Owner decisions"; agents
  apply it only to the test container and the local database, production gets it from the
  Railway pre-deploy after the owner's merge.
- **Manual scenarios for the owner:** 2 — the pre-merge latency measurement with ≥ 20
  controlled posts (AC18, which also feeds the ADR of AC19), and a short local run of the
  worker with a real source to see posts and polls land in the database.

## Approach

What the plan rests on:

- `specs/003-tweet-ingest/SPEC.md` — read in full, including "Owner decisions".
- `docs/CONVENTIONS.md` — read in full.
- `docs/adr/0003-swappable-tweet-source.md` — read in full.
- `docs/DECISIONS.md` — searched for tweet, twscrape, source, worker, advisory, pin,
  payload, module, adr, thread, X API, latency: the rows for ADR 0003, one long-running
  worker without cron, modules per business area (`tweets`), advisory-lock serialisation,
  the 60 s detection-only target and the 2026-09-28 row that already records this spec's
  polling cadence and separate loop. No row covers the adapters' libraries or the storage
  key — step 17 adds one.
- `docs/ROADMAP.md` — searched for stage 1, tweet, latency, spike, source, ingest: Stage 1
  item "Swappable tweet source … (spec 003)" — ticked by step 18.
- `docs/BACKLOG.md` — read in full (short): #2 (switch to the official X API) is the one
  this spec touches.
- `docs/PROJECT.md` — searched for FR-1, tweet, latency, 60 s, swapp, privacy, credential,
  UTC: FR-1.1, FR-1.4, the latency, swappability, privacy and UTC requirements.
- `docs/DEPLOYMENT.md` and `README.md` — read in full (both short; step 17 edits them).
- Code read in full: `backend/app/worker/{cli,loop,jobs,schedule,store,models,__init__,__main__}.py`,
  `app/core/{settings,errors}.py`, `app/db/{engine,locks,upsert}.py`, `app/fpl/client.py`,
  `app/fpl/errors.py`, `app/fpl/models/columns.py`, `migrations/env.py`,
  `migrations/versions/0002_job_run.py`, `pyproject.toml`, `.env.example`, `Dockerfile`,
  `.gitignore`, `.github/workflows/ci.yml`, `compose.yaml`, and the tests
  `tests/conftest.py`, `tests/worker/{sim,test_loop,test_cli}.py`,
  `tests/db/test_migrations.py`, `tests/core/test_settings.py`, `tests/test_readme.py`,
  `tests/test_deployment.py`, `tests/fpl/fakes.py`, `tests/fpl/payloads/__init__.py`.
- External, checked 2026-09-28: PyPI metadata of twscrape 0.20.1, tweepy 4.17.0,
  xdk 0.10.6; twscrape 0.20.1 source (`api.py` `list_timeline_raw` / `_gql_items`,
  `models.py` `parse_tweets` accepting a dict, `accounts_pool.py` `add_account_cookies`,
  `NoAccountError`, `raise_when_no_account`, `next_available_at`, `telemetry.py` opt-out
  `TWS_TELEMETRY=0`, `logger.py` `set_log_level`) and its MIT-licensed test data
  `tests/mocked-data/raw_list_timeline.json` in the sdist; the twitterapi.io reference for
  `GET /twitter/list/tweets`; the X API v2 `GET /2/lists/:id/tweets` shape.

Patterns to reuse:

- HTTP client with injectable transport and sleep: `app/fpl/client.py` `FplClient`
  (`httpx.Client(transport=…)`); test stub `tests/fpl/fakes.py` `FakeFpl` with
  `httpx.MockTransport` — the tweet tests get their own small `FakeHttp` built the same way.
- Gzipped recorded payloads: `tests/fpl/payloads/__init__.py` `load(name)` — copied as
  `tests/tweets/payloads/__init__.py`.
- Run log written in its own transaction after the work, error class only:
  `app/worker/jobs.py` `_write_log_row` / `run_job`; "latest per key in one query":
  `app/worker/store.py` `latest_runs_by_job` (`SELECT DISTINCT ON …`).
- Clock protocol and fake clocks: `app/worker/loop.py` `Clock`, `SystemClock`;
  `tests/worker/sim.py` `FakeClock` (raises `Shutdown` at its end).
- CLI dependency injection: `app/worker/cli.py` `WorkerDeps` + `get_deps(ctx)` +
  `CliRunner().invoke(app, args, obj=deps)`; logging setup `_configure_logging`.
- UTC columns `app/fpl/models/columns.py` `utc_column()`; `ConfigError` in
  `app/core/errors.py`; settings in `app/core/settings.py`.
- Migration tests: `tests/db/test_migrations.py` (own container, `run_alembic`,
  `compare_metadata`, `table_contents`).

Design choices:

- **Module layout `app/tweets/`**: `models.py` (tables), `sources/` package (`base.py` —
  normalised post, protocol, errors; `paging.py`; `twitterapi_io.py`; `x_api.py`;
  `twscrape_source.py`; `__init__.py` — `SOURCE_NAMES`, `build_source`), `config.py`,
  `store.py`, `schedule.py`, `ingest.py` (one poll), `loop.py` (the polling loop),
  `measure.py`, `cli.py`, `__main__.py`, `__init__.py` (registers the tables like
  `app/worker/__init__.py`). The twscrape adapter file is not named `twscrape.py`, so it
  never shadows the library in tracebacks or tooling.
- **Official X API through plain `httpx`**, rather than tweepy or `xdk`: the adapter needs
  one GET endpoint with a bearer token; `httpx` is already pinned and gives the raw JSON we
  store and the `MockTransport` recorded-payload tests the FPL client already uses; tweepy
  brings `requests` + `oauthlib` and hides the raw page behind its paginator; `xdk` is 0.x.
  twitterapi.io is plain `httpx` for the same reason (it has no official client).
- **One normalised form, one paging rule for all adapters**: every adapter exposes a page
  iterator (newest first) that yields `list[FetchedPost]`; `paging.collect_new` applies the
  "newer than the last seen ID, stop at it or at the page limit" rule once. twscrape is
  async; its adapter keeps one `asyncio.Runner` for its life and pulls pages from
  `list_timeline_raw` one `runner.run(anext(gen))` at a time, so it fits the same sync
  iterator and the stop rule stops fetching instead of discarding pages.
- **Polling in a thread of the worker process**, started after the schedule lock is taken
  (so an overlapping old and new deployment never poll together), with its own clock whose
  `sleep` is `stop_event.wait`: the FPL loop stays unchanged and single-threaded; the SPEC
  and DECISIONS already fixed "one process, separate loop". **A source is built, used and
  closed in the thread that polls it** (the poller thread, or one measurement thread per
  source): twscrape's `add_account_cookies` is a coroutine and its `aiosqlite` pool and the
  adapter's `asyncio.Runner` must stay on one thread, and closing an `httpx.Client` from
  another thread races an in-flight poll. Other threads only get a `make_source` callable
  and run `check_source` up front.
- **Schedule derived from the poll log** (the newest `tweet_poll` row of the active source)
  and the gameweek calendar, re-read on every wake-up, as the FPL schedule is: a restart
  and `status` compute the same next poll the loop acts on.
- **Posts keyed by X ID with `ON CONFLICT DO NOTHING`** (not `app/db/upsert.py`, which
  updates non-key columns and would move `first_fetched_at`): the first fetch wins (AC6).
- **The last seen ID is `max(tweet.x_id)`**: one list, one active source in the worker;
  the measurement command keeps its own in-memory last seen ID per source and never touches
  the database.
- **Secrets as `pydantic.SecretStr`** in settings, sent only in request headers or the
  twscrape account store, never in URLs; adapter errors carry fixed messages (endpoint name,
  HTTP status) and never response bodies or `httpx` exception text.

### Configuration (binding for steps 2, 7, 13, 16, 17)

`TweetSettings(BaseSettings)` in `app/core/settings.py` (`env_file=".env"`,
`extra="ignore"`, no `DATABASE_URL` — the measurement runs without a database):

| Variable | Field | Notes |
|---|---|---|
| `TWEET_SOURCE` | `tweet_source: str = ""` | `twscrape` \| `twitterapi_io` \| `x_api`; empty = ingest disabled |
| `X_LIST_ID` | `x_list_id: str = ""` | digits only |
| `TWSCRAPE_USERNAME` | `twscrape_username: str = ""` | the dedicated account's handle |
| `TWSCRAPE_COOKIES` | `twscrape_cookies: SecretStr \| None` | must hold `auth_token` and `ct0` |
| `TWSCRAPE_ACCOUNTS_DB` | `twscrape_accounts_db: str` | default `<tempfile.gettempdir()>/twscrape-accounts.db` (the image's `/app` is not writable by the `app` user) |
| `TWITTERAPI_IO_KEY` | `twitterapi_io_key: SecretStr \| None` | header `X-API-Key` |
| `X_API_BEARER_TOKEN` | `x_api_bearer_token: SecretStr \| None` | header `Authorization: Bearer …` |

`app/tweets/config.py`: `SOURCE_REQUIREMENTS = {"twscrape": ["TWSCRAPE_USERNAME",
"TWSCRAPE_COOKIES"], "twitterapi_io": ["TWITTERAPI_IO_KEY"], "x_api":
["X_API_BEARER_TOKEN"]}`; `check_source(settings, name) -> None` raises
`ConfigError("<VAR> must be set for TWEET_SOURCE=<name>")` naming the first missing
variable, and for twscrape `ConfigError("TWSCRAPE_COOKIES must include auth_token and ct0")`
when the cookie string lacks either name (checked on the parsed names, never echoing the
value — the same rule twscrape's `add_account_cookies` enforces with a `ValueError` later); `resolve_ingest(settings) -> IngestConfig | None` returns `None` for an empty
`TWEET_SOURCE`, raises `ConfigError("TWEET_SOURCE must be one of: twscrape, twitterapi_io,
x_api")` for an unknown name (the given value is not echoed), `ConfigError("X_LIST_ID must
be set to the numeric ID of the watched X List")` for a missing or non-numeric list ID, and
otherwise `IngestConfig(source_name, list_id: int, settings)`. Error messages never contain
a variable's value.

### Normalised post and paging (binding for steps 3–7)

- `FetchedPost` (frozen dataclass): `x_id: int`, `author_handle: str` (without `@`),
  `text: str`, `created_at: datetime` (aware UTC), `is_repost: bool`, `is_reply: bool`,
  `raw: dict` (JSON-serialisable).
- `TweetSource` (Protocol): `name: str`; `pages(list_id: int) -> Iterator[list[FetchedPost]]`
  (newest first, lazily fetched); `max_pages: int`; `close() -> None`.
- Errors (`app/tweets/sources/base.py`, subclasses of `CollectorError`):
  `SourceUnavailableError` (network error, 5xx, 401/403, unexpected status),
  `SourcePayloadError` (a page that does not parse), `SourceRateLimitedError(retry_after:
  float | None)` (HTTP 429 or twscrape reporting no available account with a next-available
  time). Messages: fixed text + source name + HTTP status only.
- `collect_new(source, list_id, since_id: int | None) -> list[FetchedPost]`:
  `since_id is None` → the first page only; otherwise iterate pages, keep posts with
  `x_id > since_id`, stop after a page containing any `x_id <= since_id`, when the iterator
  ends (no next cursor), or after `source.max_pages` pages — the last case logs
  `tweet poll reached the page limit: source=<name>` (a possible gap). Duplicates within a
  poll are dropped by `x_id`. Result ordered by `x_id` ascending.
- Per adapter:
  - **twitterapi.io** — `GET https://api.twitterapi.io/twitter/list/tweets?listId=…&cursor=…`
    (empty cursor first), header `X-API-Key`; page = `tweets[]`, next = `next_cursor` while
    `has_next_page` is true and the cursor is non-empty; `status == "error"` →
    `SourceUnavailableError`. Mapping: `id`, `author.userName`, `text`, `createdAt`
    (`%a %b %d %H:%M:%S %z %Y`), `is_repost = retweeted_tweet is not None`,
    `is_reply = isReply`; `raw` = the tweet object. `max_pages = 5` (20 posts a page).
  - **X API v2** — `GET https://api.x.com/2/lists/{id}/tweets` with `max_results=20`,
    `tweet.fields=created_at,author_id,referenced_tweets`, `expansions=author_id`,
    `user.fields=username`, `pagination_token` from `meta.next_token`; header
    `Authorization: Bearer …`. Mapping: `id`, the `includes.users[]` username of
    `author_id`, `text`, `created_at` (ISO 8601), `is_repost` / `is_reply` from
    `referenced_tweets[].type` (`retweeted` / `replied_to`); `raw = {"tweet": …,
    "author": …}`. 429 → `SourceRateLimitedError(retry_after = x-rate-limit-reset − now)`
    when the header is present. `max_pages = 3` (billed per post read — small pages).
  - **twscrape** — set `os.environ.setdefault("TWS_TELEMETRY", "0")` before importing
    twscrape and `twscrape.logger.set_log_level("ERROR")`; `API(pool=accounts_db,
    raise_when_no_account=True)`; on construction
    `runner.run(pool.add_account_cookies(username, cookies))` (a coroutine in 0.20.1; it
    upserts the account and re-activates it, so a restart heals an account twscrape marked
    inactive); pages from
    `list_timeline_raw(list_id)`; each response's JSON parsed with
    `twscrape.models.parse_tweets(page_dict)`. Mapping: `id`, `user.username`,
    `rawContent`, `date`, `is_repost = retweetedTweet is not None`,
    `is_reply = inReplyToTweetId is not None`; `raw = json.loads(tweet.json())`.
    `NoAccountError` → `SourceRateLimitedError` with the seconds until
    `runner.run(pool.next_available_at("ListLatestTweetsTimeline"))`, which in 0.20.1 is a
    coroutine returning `None` (no active account → `SourceUnavailableError` instead),
    `"now"` (→ `retry_after = 0`) or a **local wall-clock string `"%H:%M:%S"`** — not a
    datetime: the adapter converts it against local `datetime.now()`, rolling to the next
    day when the time has passed, and falls back to `retry_after=None` on anything
    unparsable.
    `max_pages = 5`. For tests the constructor accepts an injected `api` object (a fake with
    an async `list_timeline_raw` yielding `httpx.Response`s).
- HTTP adapters: `httpx.Client(transport=…, timeout=10.0)` injectable like `FplClient`; no
  retries inside a poll (the next poll is the retry); the `httpx` and `httpcore` loggers stay
  at WARNING as in `app/fpl/client.py`.

### Tables (binding for step 8)

- `tweet`: `x_id BIGINT` PK (no autoincrement), `author_handle` str, `text` str,
  `created_at` UTC, `first_fetched_at` UTC, `source` str, `is_repost` bool, `is_reply`
  bool, `raw JSONB` not null; index `ix_tweet_created_at` on `created_at`.
- `tweet_poll`: `id` int PK, `source` str, `started_at` UTC, `finished_at` UTC, `outcome`
  str (`succeeded` | `failed` | `rate_limited`), `new_posts` int (0 on failure),
  `error_class` str null, `retry_after_seconds` float null; index
  `ix_tweet_poll_source_outcome_started` on (`source`, `outcome`, `started_at`).

### Polling schedule (binding for steps 10–14)

Constants in `app/tweets/schedule.py`: `WINDOW = 90 min`, `WINDOW_INTERVAL = 20 s`,
`SPARSE_INTERVAL = 30 min`, `MAX_SLEEP = 60 s`.

- `next_deadline_after(deadlines, t)` = the smallest deadline `> t`.
- `mode(deadlines, t)` = `window` when a next deadline `D` exists and `D − 90 min <= t < D`,
  else `sparse`. `interval(deadlines, t)` = 20 s in `window`, 30 min in `sparse`.
- `next_poll_at(deadlines, last: PollRecord | None, now)`: no last poll → `now`;
  otherwise `candidate = last.started_at + interval(deadlines, last.started_at)`; if the
  window of the deadline after `last.started_at` opens strictly between `last.started_at`
  and `candidate`, `candidate = D − 90 min` (a sparse poll never skips the window start);
  if `last.outcome == "rate_limited"` and `retry_after_seconds` is set,
  `candidate = max(candidate, last.finished_at + retry_after)`.
- Deadlines = all `gameweek.deadline_at` values after `now − 1 day` (any season), read on
  every loop iteration.
- The loop: re-read deadlines and the source's latest poll; if `next_poll_at <= now` → one
  poll; else `clock.sleep(min(next − now, MAX_SLEEP))`, so a calendar change is seen within
  a minute.

### Status output (binding for step 14)

After the existing sections of `python -m app.worker status`:

```
Tweet ingest: disabled
```

or

```
Tweet ingest:
  source: twitterapi_io
  last successful poll: 2026-10-10T08:31:20Z      (or: never)
  next poll: 2026-10-10T08:31:40Z                 (or: due now)
  mode: window                                    (or: sparse)
```

### Measurement (binding for steps 15–16)

- `python -m app.tweets measure --interval-seconds 20 --duration-minutes 60
  --output PATH [--source NAME …]` (default: all three). Each source whose `check_source`
  fails is skipped with `skipped <name>: <config error message>`; the rest are polled in one
  thread per source, each on its own interval, with its own in-memory last seen ID
  (`collect_new`). A post is recorded once per source, on its first fetch, only if its
  `created_at >= ` the measurement start (older posts were already there). Failed polls are
  counted per source. At the end the summary is printed.
- Record (one JSON object per line): `source`, `x_id` (string), `author_handle`,
  `created_at`, `first_fetched_at` (ISO 8601 UTC), `latency_seconds` (float,
  `first_fetched_at − created_at`). A final line per source
  `{"source": …, "failed_polls": n, "polls": m}` carries the error counts.
- `python -m app.tweets summary PATH [--markdown]` prints per source: posts seen, p50, p95,
  max latency, polls, failed polls. Percentile = nearest rank:
  `sorted[ceil(p / 100 · n) − 1]`; no posts → `-`.

## AC → steps matrix

| AC | Steps | Proving test | Red before the change |
|----|-------|--------------|-----------------------|
| AC1 | 4, 5, 6 | `tests/tweets/sources/test_twitterapi_io.py::test_page_normalised`, `test_x_api.py::test_page_normalised`, `test_twscrape_source.py::test_page_normalised` | |
| AC2 | 3, 4, 5, 6 | `tests/tweets/sources/test_paging.py`, `test_twitterapi_io.py::test_follows_cursor_until_last_seen`, `test_x_api.py::test_follows_token_until_last_seen`, `test_twscrape_source.py::test_stops_at_last_seen` | |
| AC3 | 2, 7 | `tests/tweets/test_config.py`, `tests/tweets/sources/test_factory.py` | |
| AC4 | 2, 13 | `tests/tweets/test_config.py::test_empty_source_disables_ingest`, `tests/worker/test_cli.py::test_run_without_tweet_source_logs_disabled_once` | |
| AC5 | 4, 5, 6, 11 | `test_twitterapi_io.py::test_errors_never_carry_the_key`, `test_x_api.py::test_errors_never_carry_the_token`, `test_twscrape_source.py::test_errors_never_carry_the_cookies`, `tests/tweets/test_ingest.py::test_failed_poll_record_and_logs_carry_no_secret` | |
| AC6 | 9 | `tests/tweets/test_store.py::test_same_post_stored_once_first_fetch_kept` | |
| AC7 | 8, 9 | `tests/tweets/test_store.py::test_stored_post_fields` | |
| AC8 | 8 | `tests/db/test_migrations.py::test_tweet_migration_adds_only_new_tables` (+ existing `test_upgrade_downgrade_upgrade`, `test_models_match_migration`) | |
| AC9 | 10 | `tests/tweets/test_schedule.py::test_interval_at_window_boundaries` | |
| AC10 | 12, 13 | `tests/worker/test_cli.py::test_polls_continue_while_a_deadline_snapshot_blocks` | |
| AC11 | 10, 11, 12 | `tests/tweets/test_ingest.py::test_failed_poll_is_recorded`, `test_schedule.py::test_rate_limit_delays_next_poll`, `tests/tweets/test_loop.py::test_failures_do_not_stop_polling` | |
| AC12 | 9, 11 | `tests/tweets/test_ingest.py::test_every_poll_leaves_a_record`, `tests/tweets/test_store.py::test_latest_success_per_source` | |
| AC13 | 14 | `tests/worker/test_cli.py::test_status_shows_tweet_ingest` | |
| AC14 | 12, 13 | `tests/tweets/test_loop.py::test_stop_event_ends_loop_promptly`, `tests/worker/test_cli.py::test_sigterm_with_tweet_ingest_exits_within_10_s` | |
| AC15 | 16 | `tests/tweets/test_cli.py::test_measure_writes_one_record_per_source_and_post` | |
| AC16 | 15 | `tests/tweets/test_measure.py::test_summary_percentiles` | |
| AC17 | 16 | `tests/tweets/test_cli.py::test_measure_skips_source_without_credentials` | |
| AC18 | 18 | manual — the owner's measurement (manual scenario M1) | manual |
| AC19 | 18 | n/a — an ADR written from the owner's measurement; checked in the final review | n/a — a document from measured data |
| AC20 | 17 | `tests/test_readme.py::test_development_section_lists_commands`, `test_deployment_doc_is_a_runbook`, `tests/test_env_example.py` | |

## Steps

Every step writes its tests first and runs them red, then makes the change. Test commands
run from `backend/`. Every step also ends with `<verify.command>` green before its commit.

### Group 1 — Tweet sources

- [x] 1. Add the dependency `twscrape==0.20.1` to `[project].dependencies` (exact pin), run
      `uv lock`, `uv sync --all-extras`. Add `tests/tweets/__init__.py`,
      `tests/tweets/sources/__init__.py`, and `tests/tweets/sources/test_dependency.py`
      asserting `importlib.metadata.version("twscrape") == "0.20.1"` — files:
      `backend/pyproject.toml`, `backend/uv.lock`, the test files.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/sources/test_dependency.py && uv lock --check`
- [x] 2. Settings and configuration: `TweetSettings` in `app/core/settings.py`; new
      `app/tweets/__init__.py` (empty until step 8), `app/tweets/config.py` with
      `SOURCE_REQUIREMENTS`, `check_source`, `resolve_ingest`, `IngestConfig` as in
      "Configuration". Tests (`tests/tweets/test_config.py`, settings built with
      `_env_file=None` and `monkeypatch.setenv`): empty `TWEET_SOURCE` → `None`
      (`test_empty_source_disables_ingest`); unknown name → error naming `TWEET_SOURCE`
      without the given value; per source, each missing variable → error naming it; missing
      or non-numeric `X_LIST_ID`; `TWSCRAPE_COOKIES` without `auth_token` or `ct0` →
      error naming the variable; secret values set to a sentinel (`"sentinel-secret-…"`)
      never appear in `str(error)` or `repr(settings)` — files: `app/core/settings.py`,
      `app/tweets/__init__.py`, `app/tweets/config.py`, `tests/tweets/test_config.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/test_config.py tests/core/test_settings.py`
- [x] 3. Normalised form, protocol, errors and paging: `app/tweets/sources/__init__.py`
      (empty for now), `app/tweets/sources/base.py`, `app/tweets/sources/paging.py`
      (`collect_new`) as in "Normalised post and paging". Tests
      `tests/tweets/sources/test_paging.py` with an in-memory fake source (pages of
      `FetchedPost`): `since_id=None` → first page only; stops after the page holding the
      last seen ID and requests no further page (count page pulls); stops at `max_pages`
      and logs the page-limit line; iterator end; duplicates dropped; ascending order; no
      new posts → `[]` — files: those three modules, the test file,
      `tests/tweets/fakes.py` (`FakeSource` — scripted pages or exceptions per call,
      reused later).
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/sources/test_paging.py`
- [x] 4. twitterapi.io adapter. Recorded payloads (synthetic, shaped on the documented
      response; handles like `synthetic_leaker_1`, texts in English/Polish without real
      people): `tests/tweets/payloads/twitterapi_io-page-1.json.gz`, `-page-2.json.gz`
      (page 1 has `has_next_page: true` and a cursor; includes one repost and one reply),
      loader `tests/tweets/payloads/__init__.py` (copy of the FPL one), `FakeHttp` in
      `tests/tweets/fakes.py` (routes by path + query on `httpx.MockTransport`, records
      requests). Tests `tests/tweets/sources/test_twitterapi_io.py`:
      `test_page_normalised` (every field, UTC, repost/reply flags, raw kept);
      `test_follows_cursor_until_last_seen` (second request carries the cursor; stops at the
      last seen ID on page 2); 429 with and without `Retry-After` →
      `SourceRateLimitedError(retry_after)`; 500, 401, `ConnectError`, `status: "error"`,
      malformed JSON → the right error; `test_errors_never_carry_the_key` (sentinel key
      absent from every error's `str`, `caplog` at DEBUG, and the stored `raw`; key sent
      only in the `X-API-Key` header, never in the URL) — files:
      `app/tweets/sources/twitterapi_io.py`, the payloads, loader, fakes, test file.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/sources/test_twitterapi_io.py`
- [x] 5. Official X API v2 adapter (plain `httpx`). Synthetic payloads on the documented
      v2 shape: `tests/tweets/payloads/x_api-page-1.json.gz` (with `meta.next_token`),
      `-page-2.json.gz`; one post with `referenced_tweets` `retweeted`, one `replied_to`.
      Tests `tests/tweets/sources/test_x_api.py`: `test_page_normalised` (author resolved
      through `includes.users`), `test_follows_token_until_last_seen` (request params:
      `max_results=20`, the fields and expansions, `pagination_token` on page 2), 429 with
      `x-rate-limit-reset` → `retry_after` from a fixed injected `now`, other errors as in
      step 4, `test_errors_never_carry_the_token` (bearer only in the `Authorization`
      header) — files: `app/tweets/sources/x_api.py`, payloads, test file.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/sources/test_x_api.py`
- [x] 6. twscrape adapter. Payloads: take `tests/mocked-data/raw_list_timeline.json` from
      the twscrape 0.20.1 sdist (MIT; the sdist URL is in
      `https://pypi.org/pypi/twscrape/0.20.1/json` — fetch it with `curl` into the
      scratchpad), trim it to ~5 tweet entries plus the bottom cursor, replace every handle,
      name and text with synthetic ones, keep IDs, and save as
      `tests/tweets/payloads/twscrape-page-1.json.gz`; derive page 2 with older IDs and no
      bottom cursor; record the provenance and MIT notice in
      `tests/tweets/payloads/README.md`. Tests `tests/tweets/sources/test_twscrape_source.py`
      with an injected fake `api` (async `list_timeline_raw` yielding `httpx.Response`
      objects; a fake pool recording `add_account_cookies` and answering
      `next_available_at`): `test_page_normalised`; `test_stops_at_last_seen` (the fake
      generator is not advanced past the page holding the last seen ID and is closed);
      `NoAccountError` with the fake pool answering `"now"`, a local `"%H:%M:%S"` string
      (a fixed injected local clock; one case past midnight), `None` (→
      `SourceUnavailableError`) and garbage (→ `retry_after=None`); construction runs
      `add_account_cookies` as a coroutine; a page that fails to parse →
      `SourcePayloadError`; `test_errors_never_carry_the_cookies` (sentinel cookies absent
      from errors, `caplog` and `capfd` — loguru writes to stderr); `TWS_TELEMETRY` is `"0"`
      after importing the adapter module; no test builds a real `twscrape.API` against the
      network — files: `app/tweets/sources/twscrape_source.py`, payloads, README, test file.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/sources/test_twscrape_source.py`
- [ ] 7. Factory: `app/tweets/sources/__init__.py` `SOURCE_NAMES` and
      `build_source(name, settings) -> TweetSource` (calls `check_source` first). Tests
      `tests/tweets/sources/test_factory.py`: with `TWEET_SOURCE` set to each name (and its
      credentials via `monkeypatch.setenv`, `TWSCRAPE_ACCOUNTS_DB` in `tmp_path`),
      `resolve_ingest` + `build_source` return that adapter class — switching needs only the
      environment; missing credentials → `ConfigError` naming the variable; the twscrape
      build writes the account only to the configured SQLite file and makes no request
      (a transport/API stub is patched in) — files: `app/tweets/sources/__init__.py`,
      the test file.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/sources/`

### Group 2 — Storage and polling

- [ ] 8. Data migration (own step): `app/tweets/models.py` (`Tweet`, `TweetPoll` as in
      "Tables"; `raw` as `Column(JSONB, nullable=False)`, `x_id` as
      `Column(BigInteger, primary_key=True, autoincrement=False)`),
      `app/tweets/__init__.py` re-exports them (registers the tables),
      `migrations/versions/0003_tweets.py` (`down_revision = "0002"`), and
      `import app.tweets.models  # noqa: F401` in `migrations/env.py`,
      `tests/fpl/fakes.py`, `tests/db/test_migrations.py`. Tests: new
      `test_tweet_migration_adds_only_new_tables` (container at `0002` with bootstrap data
      and one `job_run` row → `upgrade head` → the two tables exist, collector and
      `job_run` contents unchanged → `downgrade -1` → the two tables gone, contents
      unchanged); adjust `test_job_run_migration_keeps_collector_data` to upgrade to
      `0002` (not `head`) and exclude the tweet tables, because `downgrade -1` from `head`
      now removes 0003, not `job_run`. Apply to the local database only if it is on a
      `migrations.localHosts` host: `uv run alembic upgrade head` — files: the models,
      migration, `env.py`, `tests/fpl/fakes.py`, `tests/db/test_migrations.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_migrations.py`
- [ ] 9. Store: `app/tweets/store.py` — `store_posts(session, posts, source, fetched_at)
      -> int` (`insert … ON CONFLICT (x_id) DO NOTHING RETURNING x_id`, returns the new
      count), `last_seen_id(session) -> int | None`, `write_poll(engine, record)` (own
      transaction, `SQLAlchemyError` logged by class only, like `_write_log_row`),
      `latest_poll(engine, source) -> PollRecord | None`,
      `latest_success_by_source(engine) -> dict[str, TweetPoll]` (one
      `SELECT DISTINCT ON (source) … WHERE outcome = 'succeeded'` query),
      `upcoming_deadlines(session, now) -> list[datetime]`. Tests
      `tests/tweets/test_store.py` (`db` fixture): `test_stored_post_fields` (all AC7
      fields, `raw` round-trips as JSON); `test_same_post_stored_once_first_fetch_kept`
      (same post again from another source and a later `fetched_at` → 1 row, original
      `first_fetched_at` and `source`, returned count 0); `test_latest_success_per_source`
      (mixed outcomes and two sources, one query — assert with a SQLAlchemy
      `before_cursor_execute` counter); `last_seen_id` empty / max; deadlines filter —
      files: `app/tweets/store.py`, the test file.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/test_store.py`
- [ ] 10. Pure schedule: `app/tweets/schedule.py` (`PollRecord` dataclass, `mode`,
      `interval`, `next_poll_at` as in "Polling schedule"). Tests
      `tests/tweets/test_schedule.py`: `test_interval_at_window_boundaries` (T−90 min − 1 s
      → 30 min / sparse; T−90 min → 20 s / window; deadline − 1 s → 20 s; deadline → 30 min;
      no future deadline → 30 min); a sparse poll at T−100 min → next at T−90 min;
      `test_rate_limit_delays_next_poll` (retry 120 s in the window → `finished_at + 120 s`;
      retry shorter than the interval → the interval); no last poll → `now` — files: the
      module and the test file.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/test_schedule.py`
- [ ] 11. One poll: `app/tweets/ingest.py` `poll_once(engine, source, list_id, now_fn) ->
      PollRecord` — `started_at`; `last_seen_id`; `collect_new`; `fetched_at = now_fn()`
      right after the fetch returns; `store_posts` in one transaction; record `succeeded`
      with `new_posts`; `SourceRateLimitedError` → `rate_limited` with
      `retry_after_seconds`; any other `Exception` (source or database) → `failed` with the
      error class; the poll row always written; one log line
      `tweet poll: source=… outcome=… new_posts=… duration=…s[ error=…]` without texts,
      handles or secrets. Tests `tests/tweets/test_ingest.py` with `FakeSource`:
      `test_every_poll_leaves_a_record` (success, rate limit, failure — all fields);
      `test_failed_poll_is_recorded` (network error, payload error, a database error while
      storing → `failed`, no posts stored); second poll passes the stored max ID as
      `since_id`; `test_failed_poll_record_and_logs_carry_no_secret` (a fake raising an
      exception whose message holds a sentinel secret → absent from the poll row and
      `caplog`) — files: the module, the test file.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/test_ingest.py`

### Group 3 — Worker integration

- [ ] 12. Polling loop: `app/tweets/loop.py` — `StopAwareClock(stop_event)` (`now` = UTC,
      `sleep` = `stop_event.wait(seconds)`), `TweetPoller(engine, make_source, list_id,
      clock, stop_event)` with `run()` as in "Polling schedule" (builds the source lazily
      inside the per-iteration `try`, so a build failure is logged by class and retried
      after `MAX_SLEEP`; catches `Exception` per iteration, logs the class, sleeps
      `MAX_SLEEP` and continues; returns when `stop_event` is set; `Shutdown` from a fake
      clock ends it; closes the source in its own `finally`, on the poller thread), and
      `start_poller(...) -> threading.Thread` (daemon, named `tweet-poller`, target catches
      `Shutdown`). Tests
      `tests/tweets/test_loop.py` (`db` fixture, gameweek rows seeded with `Season` +
      `Gameweek`, `FakeClock` from `tests/worker/sim.py`): polls every 20 s from T−10 min to
      the deadline, then 30 min; `test_failures_do_not_stop_polling` (scripted failures and a
      429 with `retry_after=60` → rows keep coming at the right times); a deadline added to
      the calendar mid-run is picked up within 60 s; `make_source` raising once → logged,
      next iteration builds and polls; the fake source's `close()` is called on the poller
      thread (record `threading.current_thread().name`); `test_stop_event_ends_loop_promptly`
      (real `StopAwareClock`, thread joined < 1 s after `stop_event.set()`) — files: the
      module, the test file.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/test_loop.py`
- [ ] 13. Worker wiring in `app/worker/cli.py`: `WorkerDeps` gains
      `tweet_ingest: TweetIngest | None = None` (`source_name`, `list_id`,
      `make_source: Callable[[], TweetSource]`, `clock: Clock | None` — `None` means
      `StopAwareClock(stop_event)`); `_deps_from_settings` loads `TweetSettings`, calls
      `resolve_ingest` (a `ConfigError` → `fail(...)`, exit 1) and wraps `build_source`.
      In `run`: after the schedule lock is taken, if `tweet_ingest is None` log
      `tweet ingest disabled` once; else `start_poller` with `make_source` (the source is
      built and closed on the poller thread — see "Design choices"); `worker.run()` as
      before; in `finally` (before releasing the lock): `stop_event.set()`,
      `thread.join(timeout=5)` — the main thread never touches the source. Tests in `tests/worker/test_cli.py`:
      `test_run_without_tweet_source_logs_disabled_once` (and no `tweet_poll` rows; the
      existing worker tests stay unchanged and green);
      `test_polls_continue_while_a_deadline_snapshot_blocks` (calendar seeded with D6; FPL
      clock starts at D6 − 5 min so the T−5 snapshot is due after the start reference
      sync; the `bootstrap-static/` route blocks on its second call until the poller
      thread has finished or 10 s pass; the poller's own `FakeClock` runs D6 − 5 min → D6 −
      2 min; assert the route was released by the poller finishing, not the timeout, and
      `tweet_poll` holds 9 `succeeded` rows 20 s apart);
      `test_sigterm_with_tweet_ingest_exits_within_10_s` (copy of the idle SIGTERM test with
      a fake source and the default `StopAwareClock`; exit 0, < 10 s, the poller thread is
      not alive); a `TWEET_SOURCE=x_api` without a token → exit 1 and the message names
      `X_API_BEARER_TOKEN` (settings-driven, `monkeypatch.chdir(tmp_path)` as in
      `tests/core/test_settings.py`) — files: `app/worker/cli.py`,
      `tests/worker/test_cli.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/worker/ tests/tweets/`
- [ ] 14. `status`: append the "Tweet ingest" section as in "Status output", using
      `latest_success_by_source`, `latest_poll`, `upcoming_deadlines`, `next_poll_at` and
      `mode` (no source is built — no network in `status`). Tests in
      `tests/worker/test_cli.py`: `test_status_shows_tweet_ingest` (seeded gameweek and
      poll rows at a fixed clock inside the window → exact four lines; never polled →
      `never` / `due now`); disabled → `Tweet ingest: disabled`; the existing status tests
      still pass — files: `app/worker/cli.py`, `tests/worker/test_cli.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/worker/test_cli.py`

### Group 4 — Measurement and documentation

- [ ] 15. Latency records and summary: `app/tweets/measure.py` — `LatencyRecord`,
      `record_to_json` / `read_records(path)`, `summarise(records, poll_counts) ->
      list[SourceSummary]`, `format_summary(summaries, markdown: bool) -> str`, nearest-rank
      percentile as in "Measurement". Tests `tests/tweets/test_measure.py`:
      `test_summary_percentiles` (latencies 1…100 → p50 50, p95 95, max 100; 1 record;
      0 records → `-`; two sources kept apart; failed polls counted); markdown table header
      and row format — files: the module, the test file.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/test_measure.py`
- [ ] 16. Commands: `app/tweets/cli.py` (Typer, `measure` and `summary` as in
      "Measurement"; injectable deps via `ctx.obj` like `WorkerDeps`: source builders,
      clock, sleep; each builder is called, used and closed inside its source's thread,
      `check_source` runs up front in the main thread for the skip messages) and `app/tweets/__main__.py`. Tests `tests/tweets/test_cli.py` with two
      `FakeSource`s and a fake clock: `test_measure_writes_one_record_per_source_and_post`
      (a post seen by both sources gives two records with each source's own first fetch; a
      post seen again gives no new record; a post older than the start is not recorded;
      latency = fetch − `created_at`); `test_measure_skips_source_without_credentials`
      (settings-driven: only `TWITTERAPI_IO_KEY` set → `skipped twscrape: …` and
      `skipped x_api: …` printed, the other measured); a source that raises on every poll →
      counted as failed polls, the command still finishes; `summary` on a written file;
      `python -m app.tweets --help` via subprocess — files: the two modules, the test file.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/test_cli.py`
- [ ] 17. Documentation: `backend/.env.example` (the seven variables with empty values and
      comments — placeholders only), `docs/DEPLOYMENT.md` (the new worker variables as an
      optional step: no `TWEET_SOURCE` = ingest off; which variables per source; secrets
      only in Railway variables; `status` shows the ingest), `README.md` "Development"
      (running the ingest with the worker, `python -m app.tweets measure` and `summary`,
      the measurement output directory), `.gitignore` (`backend/measurements/`),
      `docs/DECISIONS.md` row (2026-09-28: adapters — twscrape library, twitterapi.io and the
      official X API v2 through plain `httpx`; posts keyed by X ID, first fetch wins; the
      last seen ID from the stored posts; twscrape telemetry off). Tests: extend
      `tests/test_readme.py` (`app.tweets measure`, `app.tweets summary` and all seven
      variables of "Configuration" in the Development section — AC20 asks the README to
      list them; the same seven in `docs/DEPLOYMENT.md`); new
      `tests/test_env_example.py` (every `TweetSettings` field's variable is listed in
      `backend/.env.example` and every tweet variable there has an empty value) — files:
      those documents and tests.
      Automatic verification: `cd backend && uv run pytest -q tests/test_readme.py tests/test_env_example.py`

### Group 5 — Measurement report and ADR (after the owner's run)

- [ ] 18. Precondition: the owner's measurement output from manual scenario M1 is at
      `backend/measurements/latency.jsonl` (gitignored). If it is missing, stop with
      `RESULT: ESCALATE` asking the owner to run M1 — do not invent numbers. Then: run
      `uv run python -m app.tweets summary measurements/latency.jsonl --markdown`; write
      `docs/reports/003-tweet-source-latency.md` (date, method: interval 20 s, number of
      controlled posts, the sources measured and why the official API was or was not; the
      summary table; observations; the open questions from the SPEC left for the first real
      window); write `docs/adr/0005-default-tweet-source.md` (the chosen default and its
      measured p95 detection latency; if no candidate reaches p95 ≤ 60 s, say so and that
      BACKLOG #2 is triggered); add the DECISIONS row linking ADR 0005; update the BACKLOG
      #2 trigger note if triggered; `docs/adr/README.md` index if it lists ADRs; tick the
      Stage 1 item in `docs/ROADMAP.md` — files: those documents.
      Automatic verification: `cd backend && uv run python -m app.tweets summary measurements/latency.jsonl && cd .. && grep -q "0005" docs/DECISIONS.md && grep -q "\[x\] Swappable tweet source" docs/ROADMAP.md`

## Risks and traps

- **twscrape telemetry and logs.** twscrape sends aggregated usage to PostHog unless
  `TWS_TELEMETRY=0`; the adapter sets it before the import, and no test may construct a
  networked `API`. twscrape logs through loguru to stderr (not `logging`), so AC5 tests use
  `capfd`, and the adapter raises its log level to ERROR.
- **twscrape blocking on rate limits.** Without `raise_when_no_account=True` a locked
  single account makes twscrape wait until the lock ends (up to ~15 min) inside a poll;
  with it, the adapter turns `NoAccountError` into a rate-limit outcome.
- **twscrape's async API in a thread.** One `asyncio.Runner` per adapter instance, created
  and used only in the thread that polls (the poller thread, or one measurement thread per
  source); the adapter is constructed (async `add_account_cookies`) and `close()`d (the
  generator and the runner) in that same thread — never from the worker's main thread.
- **twscrape hides some breakages.** Its `QueueClient` swallows an unexpected HTTP status
  (e.g. a 404 after X rotates a GraphQL ID) by locking the account for 15 min, which our
  adapter then sees as `NoAccountError` → `rate_limited` with ~900 s; a GraphQL error
  response without data is logged by twscrape and parsed as an empty page → `succeeded`
  with 0 posts. Both are recorded in `tweet_poll` (a long `rate_limited` streak, or no new
  posts for a whole window), which is the input Stage 5 alerting needs; M1 and M2 show
  whether 0.20.1's IDs still work.
- **Migration test coupling.** `test_job_run_migration_keeps_collector_data` downgrades
  `-1` from `head` and expects `job_run` to vanish — after 0003 it must upgrade to `0002`
  instead (step 8). `test_upgrade_downgrade_upgrade` and `test_models_match_migration`
  catch any model/migration drift (JSONB, BigInteger, index names must match exactly).
- **Shutdown bound.** A poll in flight can hold an HTTP request up to its 10 s timeout; the
  worker joins the thread for 5 s and the thread is a daemon, so the process still exits
  within the 10 s of spec 002. The signal handler still raises `Shutdown` only in the main
  thread; the poller learns about it from `stop_event`.
- **Two workers during a Railway restart.** The poller starts only after the schedule lock,
  so the waiting new deployment does not poll alongside the old one.
- **Clock accuracy.** Latency compares our clock with X's `created_at` (second precision);
  the owner's machine must sync time (NTP) during M1. A sub-second error does not matter
  against 60 s.
- **X API cost.** Billed per post returned; `max_results=20` and `max_pages=3` cap a poll.
  The measurement of the official API happens only if the owner buys credits.
- **Synthetic payloads vs the real ones.** twitterapi.io and X API payloads are built from
  the documented shape, not recorded from the live service (no credentials in agent
  sessions); M1 is the first contact with the live APIs — a mapping failure there is fixed
  as a deviation with a new recorded (anonymised) payload.
- **`test_database_url_not_read_by_tests`** greps the tests for that variable name — new
  settings tests must not mention it outside `tests/core/test_settings.py`.
- **Private data.** Stored posts are public posts; logs carry only counts, source names and
  error classes — no texts, handles, list ID or secrets.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

- `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` —
  fully green.
- Against the local Compose database (`docker compose up -d`, `uv run alembic upgrade head`
  → revision `0003`): `uv run python -m app.worker status` with no `TWEET_SOURCE` → ends
  with `Tweet ingest: disabled`; with `TWEET_SOURCE=twitterapi_io` and no key → exit 1,
  stderr names `TWITTERAPI_IO_KEY`.
- `uv run python -m app.tweets measure --interval-seconds 1 --duration-minutes 1 --output
  /tmp/…/m.jsonl` with no credentials at all → every source printed as skipped, exit 0, an
  empty summary.
- `docker build -f backend/Dockerfile -t gaffers-presser-worker:local . && docker run --rm
  gaffers-presser-worker:local python -m app.tweets --help` → exit 0 (twscrape installs in
  the image).
- Record the results in "Definition of Done".

### Manual (performed by the owner)

- **M1 — latency measurement (AC18).** With the dedicated account's cookies, the
  twitterapi.io key (and the X API token only if credits were bought) and `X_LIST_ID` in
  `backend/.env`: run `uv run python -m app.tweets measure --interval-seconds 20
  --duration-minutes 45 --output measurements/latency.jsonl`, publish ≥ 20 posts from the
  dedicated account (a member of the watched list) spread over the run, then leave the file
  in `backend/measurements/latency.jsonl` for step 18.
- **M2 — worker with a real source.** `TWEET_SOURCE=<chosen>` and `uv run python -m
  app.worker run` for a few minutes: `status` shows the source, a last successful poll and
  `sparse` mode (or `window` near a deadline); the `tweet` table holds the list's recent
  posts.

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md` rows (step 17, step 18), ADR 0005,
      the report, `docs/DEPLOYMENT.md`, `README.md`, `backend/.env.example`
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation: date, stage, question, decision)_

## Review log

### 2026-09-28 — /pipeline:plan-review

Anti-anchoring leads (from the SPEC alone): module `app/tweets` with a protocol + three
adapters on recorded payloads; posts keyed by X ID with insert-or-ignore; a pure schedule
function tested at the boundaries; the poll loop in a thread of the worker; twscrape's async
API as the main integration risk; AC18/AC19 need a pause for the owner's run. The plan
matches all of them; the differences below came from checking the twscrape 0.20.1 wheel
(`accounts_pool.py`, `queue_client.py`, `api.py`, `logger.py`, `telemetry.py`) against the
plan's assumptions.

Findings:

| # | Severity | Finding | Change |
|---|---|---|---|
| 1 | `major` | Steps 12/13/16 built the source on one thread and polled it on another, and step 13 called `source.close()` from the main thread. In twscrape 0.20.1 `add_account_cookies` is a coroutine (aiosqlite), so construction already needs the adapter's `asyncio.Runner`; closing the runner or an `httpx.Client` from the main thread while the poller may be mid-request is a race. | "Design choices": a source is built, used and closed in its polling thread; step 12 `TweetPoller` takes `make_source`, builds lazily inside the per-iteration `try`, closes in its own `finally`; step 13 main thread only sets the event and joins; step 16 builds in each measurement thread; tests added; risk updated. |
| 2 | `major` | `AccountsPool.next_available_at` returns `None`, `"now"` or a local `"%H:%M:%S"` string, not a datetime; the plan's "seconds to next_available_at" and a fake pool answering a time would pass tests while the real adapter mis-computes `retry_after` (AC11). | twscrape adapter mapping spelt out (queue name, the three return forms, local-time conversion with midnight rollover, fallback `None`); step 6 tests each form. |
| 3 | `minor` | The Configuration table says `TWSCRAPE_COOKIES` must hold `auth_token` and `ct0`, but `check_source` did not check it; twscrape would raise a `ValueError` later on the poller thread instead of the clear configuration error AC3 asks for. | `check_source` raises `ConfigError` naming the variable; step 2 test added. |
| 4 | `minor` | AC20 requires the README to list the new variables; step 17's test checked only `TWEET_SOURCE` in the README and five of the seven variables in DEPLOYMENT. | Step 17 test checks all seven variables in both. |
| 5 | `minor` | Not mentioned: twscrape turns an unexpected HTTP status into a 15-min account lock (→ our `rate_limited`) and a GraphQL error without data into an empty page (→ `succeeded`, 0 posts). | Risk added; no code change — the poll log already records both for Stage 5. |

Checked and found correct (later stages need not repeat):

- **coverage:** every AC1–AC20 has steps and a named proving test; the matrix matches the
  steps; AC18 manual, AC19 `n/a` with a reason.
- **compliance:** CONVENTIONS (recorded synthetic payloads, no network in pytest, container
  DB, exact pin, UTC, logs without handles/list ID/secrets, no docstrings, one module
  `app/tweets`); DECISIONS rows for ADR 0003, one long-running worker, modules per area,
  advisory locks (poller starts after the schedule lock), 60 s detection-only target and the
  2026-09-28 polling-cadence row — none broken; PROJECT.md already carries the detection-only
  wording.
- **minimality:** plain `httpx` for twitterapi.io and the X API reuses the pinned client and
  the `FplClient`/`MockTransport` pattern; one paging rule for all adapters; no scope beyond
  the SPEC.
- **feasibility:** no forward dependencies (config → base/paging → adapters → factory →
  models/migration → store → schedule → ingest → loop → worker → status → measure → docs →
  report); migration 0003 only adds tables and the `test_job_run_migration_keeps_collector_data`
  coupling is handled; `x_id` BIGINT fits X IDs; UTC parsing per source; the 10 s shutdown
  bound holds with a 5 s join of a daemon thread; the AC10 test is deterministic (poller on
  its own `FakeClock`: 9 polls from D−5 min to D−2 min, as `FakeClock` raises `Shutdown` at
  its end); twscrape telemetry is read at run time (`TWS_TELEMETRY == "0"`), `set_log_level`
  exists, `list_timeline_raw` / `raise_when_no_account` / `add_account_cookies` exist in 0.20.1.
- **E2E:** automatic part runnable locally (Compose DB, CLI error paths, measure with no
  credentials, Docker build); manual part is only what needs the owner's X account and keys
  (M1, M2).
- **testability:** every step has an `Automatic verification:` line with exact test paths.
- **groups:** five groups, every step in exactly one, no boundary leaves work half done;
  Group 5 waits for the owner's M1 by design (step 18 escalates without the file).
- **test-first:** the preamble and every AC step write the proving test first; the fourth
  matrix column is present.
- **summary:** new dependency (twscrape only, fewer than the owner accepted) and the
  add-only migration are flagged and both covered by SPEC → "Owner decisions".
- **language:** the plan is in English, as `language: "en"`.

Decision: the plan is ready for implementation — both majors were fixable in the plan and
are fixed, no blocker remains, and the only new dependency and the migration are accepted in
SPEC → "Owner decisions".

## Chunk notes

_(filled in by /pipeline:implement in chunk mode — one entry per chunk that ends at a group boundary)_

## Deviations

- Step 6 payloads: the trimmed twscrape sdist fixture (`tests/mocked-data/raw_list_timeline.json`,
  MIT) has no reply tweet among its entries (no `in_reply_to_status_id_str`), so
  `twscrape-page-1.json.gz`'s reply example (`x_id=3002`) is a duplicate of a real entry with
  `in_reply_to_status_id_str`/`in_reply_to_screen_name`/`in_reply_to_user_id_str` added
  manually, keeping the rest of the entry's real (redacted) structure. Every handle, display
  name, bio, profile URL and post text (including the one long-form `note_tweet` post) in both
  twscrape payloads is replaced by a synthetic value; only numeric IDs, timestamps and engagement
  counts are kept from the original fixture. Minor — no scope or architecture change.

## Final review

_(filled in by /pipeline:final-review)_
