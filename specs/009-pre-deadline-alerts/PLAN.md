# PLAN 009 — Pre-deadline alert e-mails

## Owner summary

- **Approach:** A new module `app/alerts/` runs as a fourth worker thread. Before each deadline
  (a real one from the gameweek table, or the rehearsal moment from `ALERT_REHEARSAL_DEADLINE`)
  it sends a digest at the first slot and news at the later slots, both with full corroboration,
  then polls every 5 s for newly extracted posts and sends a breaking e-mail per post with SQL
  rules only. Every alert goes through `DeliveryService` with a deterministic key and is
  recorded, with every post it included, in two new tables keyed by the alert deadline; that
  log drives "new since the previous alert", the restart catch-up, the status lines and the
  latency report. The text is a Polish TOML template; `player.selected_by_percent` is added and
  refreshed by every reference sync; fast tweet polling starts at the first slot plus 10 min.
- **Main risks:** a news slot with many players runs one corroboration (search plus up to 10
  judge calls) per player, so breaking e-mails at the start of the T-30 window wait for the
  news to finish; detecting "a repost of an included post" reads the original post ID from
  each source's raw payload; the migration tests must be updated for the new column and tables.
- **New dependency:** no.
- **Data migration:** yes — `0008` adds the nullable `player.selected_by_percent`; `0009`
  adds the alert log tables `alert` and `alert_post` (new tables only). Both accepted in
  SPEC → "Owner decisions" (2026-10-02).
- **Manual scenarios for the owner:** 3 — a rehearsal deadline run locally with real e-mails
  (digest, news, breaking, content and links), the latency report after it, and the first
  real deadline (GW6).

## Approach

What the plan rests on:

- `specs/009-pre-deadline-alerts/SPEC.md` — read in full.
- `docs/CONVENTIONS.md` — read in full.
- `docs/DECISIONS.md` — searched for "alert", "delivery", "module", "thread", "deadline":
  the module list includes `alerts` (2026-09-26); the worker schedule (2026-09-27); the 60 s
  target covers detection only (2026-09-28); corroboration on demand (2026-09-30); shared code
  in `app/llm`/`app/core`, the current-extraction query lives once in `app/extraction/store.py`,
  deadline helpers in `app/fpl/deadlines.py` (2026-09-30); a refactor a feature needs is done
  in that feature (2026-09-30); delivery and its idempotency (2026-10-01); the spec 009 row
  (2026-10-02) already exists.
- `docs/ROADMAP.md` — searched for "alert", "Stage 2", "deploy": the Stage 2 item and its
  preconditions line.
- `docs/BACKLOG.md` — searched for rows 11, 19, 20 (unchanged by this plan).
- `docs/PROJECT.md` — searched for "alert": FR-2.3.
- `docs/DEPLOYMENT.md` — searched for the numbered steps and "Delivery": step 11 is the last.
- `README.md` — searched for the section headings and the CLI examples (`## Development`).
- Code read in full: `app/core/{clock,errors,local_time,retry,settings}.py`,
  `app/content/__init__.py`, `app/delivery/*` (all files), `app/db/*`, `app/worker/*`,
  `app/tweets/{schedule,loop,config,models,store}.py`, `app/tweets/sources/base.py`,
  `app/fpl/{deadlines,reference,schemas}.py`, `app/fpl/models/*`,
  `app/extraction/{models,store,loop,config}.py`, `app/corroboration/{service,schemas,rules,
  sources,config,cli}.py`, `migrations/versions/0006*`, `0007*`, `migrations/env.py`;
  tests: `tests/conftest.py`, `tests/db/test_migrations.py`, `tests/delivery/fakes.py`,
  `tests/corroboration/{fakes,helpers}.py`, `tests/worker/sim.py`, `tests/test_env_example.py`,
  `tests/test_module_boundaries.py`, `tests/test_shared_code.py`, `tests/test_docs.py`,
  `tests/test_deployment.py`; searched: `app/tweets/sources/{x_api,twscrape_source,
  twitterapi_io}.py` for the repost fields, `tests/worker/test_cli.py` for the `cli` fixture and
  the status tests, `tests/test_readme.py` for what it checks.

Patterns reused:

- Settings and validation: `app/delivery/config.py` (`BaseSettings` with `env_ignore_empty`, a
  `resolve_…` function raising `ConfigError` that names the variable).
- Background thread: `app/extraction/loop.py` / `app/tweets/loop.py` (a loop class with a
  `StopAwareClock`, `start_…` returning a daemon thread, `Shutdown` swallowed, each iteration in
  `try/except` that logs only the exception class).
- Sending: `DeliveryService(engine, channel, clock, stop_event).send(key, "alert", Message)`;
  its outcome `already_sent` is how a restart after a lost record finds the e-mail it already
  sent.
- Content: `app/delivery/content.py` loads a TOML file from `app/content/`; tested as in
  `tests/content/test_delivery_test_message.py` (fields render, no whole-text comparison).
- CLI: `app/delivery/cli.py` (Typer, injected deps in `ctx.obj`, `fail()`), times parsed with
  `app.core.local_time.parse_local` and shown with `format_local` as in
  `app/corroboration/cli.py`.
- Corroboration: `corroborate(...)`; `sources.window_start` for the window;
  `rules.account_of` for the independent account; `current_extractions(...)` for the window's
  claims (the only current-extraction query).
- Tests: the `db` fixture, `tests/corroboration/helpers.py` (`seed_reference`, `add_claim`),
  `tests/delivery/fakes.py` (`FakeChannel`, `FixedClock`), `tests/worker/sim.FakeClock`
  (a sleep that advances time), `tests/fpl/fakes.table_contents` in migration tests.

Design choices:

1. **One alerts thread with a `tick()`** that handles the current alert deadline: first every
   due slot in order (digest, then news), then — inside the breaking window — the breaking
   candidates; it sleeps 5 s in the breaking window and at most 60 s otherwise. Rejected: a
   separate breaking thread — it races the news slot for the same posts (a post the news is
   still corroborating is not yet recorded as included) and would need a lock between the two.
   `tick()` takes no wall-clock time from outside, so tests drive it directly.
2. **The alert log is the state.** Table `alert` (one row per attempted or skipped alert, unique
   `key`) and `alert_post` (each post the alert included, per player, `new` or `context`).
   Due slots, "already included", restart catch-up, status and latency are all queries on it,
   always filtered by `deadline_key`. Keys: deadline key `"<season>:gw<N>"` for a real deadline,
   `"rehearsal:<UTC deadline %Y-%m-%dT%H:%MZ>"` for a rehearsal; alert key
   `"alert:<deadline key>:digest:<slot minutes>"`, `"…:news:<slot minutes>"`,
   `"…:breaking:<x_id>"` — the same string is the delivery idempotency key.
3. **Send first, then record.** The alert is built, sent with its key, then the `alert` row and
   its `alert_post` rows are written in one transaction. A crash between the two makes the slot
   due again; the rebuilt alert gets `already_sent` from `DeliveryService` and is recorded as
   sent with that delivery log row, so nothing goes out twice (AC15). A `skipped` news slot is
   recorded with no delivery row.
4. **What counts as included.** The posts of every recorded alert of the deadline key with status
   `sent` or `failed` (AC17: a failed alert is not retried, by its own slot or by a later one, so
   its posts are not offered again as new; the failure shows in the status lines). For each
   reported player an alert includes all of his claim posts in the window up to `as_of` (also
   the older posts of an account that corroboration collapses to its newest) plus every judged
   citation shown, so the next news slot does not report a player for a post the owner already
   had in front of him. A **breaking** alert records its trigger post as `new` and every other
   post it shows as `context`, and only its `new` rows count as included: a post shown as
   support in another post's breaking e-mail (two posts extracted in the same tick) still gets
   its own breaking e-mail (AC12, owner decision "one e-mail per new post"). So: included =
   every `alert_post` row of `sent`/`failed` digest and news alerts plus the `new` rows of
   `sent`/`failed` breaking alerts. A post is "already included" when its `x_id` or its
   original's `x_id` (for a repost) equals the `x_id` or the original's `x_id` of an included
   post.
5. **The original of a repost** is read from the stored raw payload by a new pure function
   `app/tweets/reposts.py: reposted_x_id(raw: dict) -> int | None` (twscrape
   `raw["retweetedTweet"]["id"]`, twitterapi.io `raw["retweeted_tweet"]["id"]`, X API
   `raw["tweet"]["referenced_tweets"]` with `type == "retweeted"`). Rejected: matching reposts by
   author and text (twscrape prefixes and truncates the text); a new column on `tweet` (a third
   migration for a value the payload already holds).
6. **Breaking candidates** are the current extractions whose `finished_at` is at or after the
   `as_of` of the last slot's alert row, whose post was created in `[window start, now)`, that
   carry an event for a listed player, and that are not already included (rule 4) and have no
   `breaking` alert row. One e-mail per post (key per post); a post naming several listed players
   has one section per player. Candidates are handled in `(created_at, x_id)` order, and the
   included set is extended after each send, so a post and its repost in the same tick give one
   e-mail.
7. **Breaking corroboration = `corroborate` with SQL only and a given anchor.** `corroborate`
   gets a keyword `anchor_x_id: int | None = None` (when the claim with that post exists it is the
   anchor; otherwise the newest, as today); the breaking runtime is
   `CorroborationRuntime(embedder=None, judge=None, skipped_reason=…)`, so no search or judge
   call runs (AC12). The same SQL-only runtime is the fallback when a full corroboration raises
   (AC17). Rejected: a second SQL-only path in `app/alerts` duplicating the labelling and
   grading.
8. **Shared corroboration runtime builder.** `_runtime_from_settings` moves from
   `app/corroboration/cli.py` to `app/corroboration/runtime.py: build_runtime(settings, clock)`,
   used by the corroboration CLI, the alerts CLI and the worker (DECISIONS 2026-09-30: the
   refactor a feature needs is done in that feature).
9. **Polling window as parameters.** `app/tweets/schedule.py` functions take `window: timedelta =
   WINDOW` and the poller takes `window` and `extra_deadlines`; the worker computes them from the
   alert config (`app.alerts.schedule.polling_window`, `max(90 min, first slot + 10 min)`) and
   passes the rehearsal moment as an extra deadline. `app/tweets` imports nothing from
   `app/alerts`.
10. **Plain-text e-mail only.** `Message(title, text)` with every link on its own line; no HTML
    body (the SPEC does not ask for one and HTML adds an escaping surface).
11. **"No news" line** in the digest gives the number of listed players with no claim, not their
    names (AC6 allows a mention only there; a list of 100+ names would bury the news).
12. **Ownership order** (AC9): `selected_by_percent` descending, `NULL` last, then `web_name`.

## AC → steps matrix

| AC | Steps | Proving test | Red before the change |
|----|-------|--------------|-----------------------|
| AC1 | 4, 14 | `tests/alerts/test_config.py::test_alerts_disabled_reasons`, `tests/worker/test_cli.py::test_status_shows_alerts_disabled_with_reason`, `::test_run_starts_alerts_only_when_enabled` | `uv run pytest -q tests/alerts/test_config.py` (stub config) → `assert None == 'delivery disabled'` |
| AC2 | 4, 14, 15 | `tests/alerts/test_config.py::test_invalid_values_name_the_variable` (parametrised), `tests/worker/test_cli.py::test_worker_rejects_invalid_alert_slots_naming_the_variable`, `tests/alerts/test_cli.py::test_cli_rejects_invalid_alert_variable` | `uv run pytest -q tests/alerts/test_config.py` (stub config) → `Failed: DID NOT RAISE ConfigError` |
| AC3 | 6 | `tests/alerts/test_players.py::test_league_owned_uses_latest_synced_picks_with_managers` | `uv run pytest -q tests/alerts/test_players.py -k league_owned` (stub `listed_players`) → `assert set() == {2, 3}` |
| AC4 | 6 | `tests/alerts/test_players.py::test_widely_owned_at_threshold` | `uv run pytest -q tests/alerts/test_players.py` (stub) → `assert set() == {2}` |
| AC5 | 6 | `tests/alerts/test_players.py::test_trending_counts_independent_accounts_with_reposts` | `uv run pytest -q tests/alerts/test_players.py` (stub) → `assert [] == [(1, 3)]` |
| AC6 | 6, 10 | `tests/alerts/test_players.py::test_only_players_with_a_claim_are_reported_once_with_all_categories`, `tests/alerts/test_slots.py::test_digest_without_claims_says_no_news` | `uv run pytest -q tests/alerts/test_players.py` (stub) → `assert [] == [1]`; `uv run pytest -q tests/alerts/test_slots.py` (stub `run_slot`) → `assert 'stub' == 'sent'` (`test_digest_without_claims_says_no_news`) |
| AC7 | 1, 2 | `tests/db/test_migrations.py::test_ownership_migration_keeps_rows_and_downgrades`, `tests/fpl/test_reference_sync.py::test_selected_by_percent_written_and_refreshed` | `uv run pytest -q tests/db/test_migrations.py -k ownership` (stub migration 0008) → `assert "selected_by_percent" in columns`; `uv run pytest -q tests/fpl/test_reference_sync.py -k selected` (column added, sync not yet writing it) → `assert rows == {el["id"]: Decimal(el["selected_by_percent"]) ...}` |
| AC8 | 10 | `tests/alerts/test_slots.py::test_digest_covers_previous_deadline_to_slot_with_full_corroboration`, `::test_digest_without_claims_says_no_news` | `uv run pytest -q tests/alerts/test_slots.py` (stub `run_slot`) → `assert 'stub' == 'sent'` |
| AC9 | 5, 6 | `tests/alerts/test_render.py::test_player_section_fields`, `tests/alerts/test_players.py::test_order_by_ownership_nulls_last` | `uv run pytest -q tests/alerts/test_render.py` (stub `render_alert`) → `assert ('Saka' in 'x')`; `uv run pytest -q tests/alerts/test_players.py` (stub) → `assert [] == ['Isak', 'Saka', 'Gabriel', 'Jesus']` |
| AC10 | 5 | `tests/alerts/test_render.py::test_times_are_warsaw`, `tests/content/test_alert_email.py::test_template_has_every_key_and_renders`, `tests/alerts/test_render.py::test_alerts_code_has_no_polish_literals` | `uv run pytest -q tests/alerts/test_render.py tests/content/test_alert_email.py` (stub `render_alert`) → `assert '2026-10-04 16:00' in 'x'`; `assert set() == {('category', ...), ...}` |
| AC11 | 10 | `tests/alerts/test_slots.py::test_news_reports_only_players_with_unincluded_posts_and_marks_new`, `::test_news_with_nothing_new_is_skipped` | `uv run pytest -q tests/alerts/test_slots.py` (stub `run_slot`) → `assert 'stub' == 'skipped'` |
| AC12 | 7, 9, 11 | `tests/corroboration/test_service.py::test_given_anchor_is_the_anchor`, `tests/alerts/test_breaking.py::test_breaking_anchor_is_the_post_sql_only`, `::test_two_posts_extracted_in_one_tick_each_break`, `tests/alerts/test_store.py::test_breaking_context_rows_are_not_included` | `uv run pytest -q tests/corroboration/test_service.py -k given_anchor` (param accepted, ignored) → `assert result.anchor is not None and result.anchor.post.x_id == 1` (got 3); `uv run pytest -q tests/alerts/test_store.py` (stub `included_origins`) → `assert set() == {1, 4, 5}`; `uv run pytest -q tests/alerts/test_breaking.py` (stub `run_breaking`) → `assert -1 == 1` (`test_breaking_anchor_is_the_post_sql_only`), `assert -1 == 2` (`test_two_posts_extracted_in_one_tick_each_break`) |
| AC13 | 9, 11 | `tests/tweets/test_reposts.py::test_reposted_x_id_per_source`, `tests/alerts/test_breaking.py::test_included_post_or_its_repost_never_breaks` | `uv run pytest -q tests/tweets/test_reposts.py` (stub) → `assert None == 9999000000000000008`; `tests/alerts/test_store.py` (stub) → `assert set() == {4, 5}`; `uv run pytest -q tests/alerts/test_breaking.py` (stub `run_breaking`) → `assert -1 == 1` (the post and its repost in one tick) |
| AC14 | 12 | `tests/alerts/test_loop.py::test_breaking_sent_within_15_s_of_extraction` | |
| AC15 | 3, 10, 11, 12 | `tests/alerts/test_slots.py::test_repeated_run_sends_once`, `::test_lost_record_after_send_is_recovered_without_resend`, `tests/alerts/test_breaking.py::test_breaking_repeated_tick_sends_once` | `uv run pytest -q tests/alerts/test_slots.py` (stub `run_slot`) → `assert 'stub' == 'sent'` (`test_repeated_run_sends_once`, `test_lost_record_after_send_is_recovered_without_resend`); `uv run pytest -q tests/alerts/test_breaking.py` (stub `run_breaking`) → `assert -1 == 1` (`test_breaking_repeated_tick_sends_once`) |
| AC16 | 8, 10 | `tests/alerts/test_schedule.py::test_missed_slots_due_in_order_until_deadline`, `tests/alerts/test_slots.py::test_restart_sends_missed_slot_up_to_send_time`, `::test_no_send_at_or_after_deadline` | `uv run pytest -q tests/alerts/test_schedule.py` (stub schedule) → `assert [] == [120]` (`test_missed_slots_due_in_order_until_deadline`); slots part: _pending step 10_; `uv run pytest -q tests/alerts/test_slots.py` (stub `run_slot`) → `assert 'stub' == 'cutoff'` (`test_no_send_at_or_after_deadline`), `assert 'stub' == 'sent'` (`test_restart_sends_missed_slot_up_to_send_time`) |
| AC17 | 10, 12 | `tests/alerts/test_slots.py::test_failed_delivery_recorded_and_not_retried`, `::test_failed_corroboration_falls_back_to_sql_with_note`, `tests/alerts/test_loop.py::test_tick_error_does_not_stop_the_loop` | `uv run pytest -q tests/alerts/test_slots.py` (stub `run_slot`) → `assert 'stub' == 'failed'` (`test_failed_delivery_recorded_and_not_retried`); loop part: _pending step 12_ |
| AC18 | 3, 8, 9 | `tests/db/test_migrations.py::test_alert_log_migration_adds_only_new_tables`, `tests/alerts/test_schedule.py::test_deadline_keys`, `tests/alerts/test_store.py::test_included_is_scoped_to_the_deadline_key` | `uv run pytest -q tests/db/test_migrations.py -k alert_log` (stub migration 0009) → `assert ALERT_TABLES <= set(inspector.get_table_names())`; `uv run pytest -q tests/alerts/test_schedule.py` (stub schedule) → `assert '' == '2026/27:gw6'`; `uv run pytest -q tests/alerts/test_store.py` (stub) → `assert set() == {1, 2}` |
| AC19 | 15 | `tests/alerts/test_latency.py::test_percentiles_and_legs`, `tests/alerts/test_cli.py::test_latency_default_gameweek_and_rehearsal` | |
| AC20 | 8, 13 | `tests/alerts/test_schedule.py::test_polling_window`, `tests/tweets/test_schedule.py::test_custom_window_and_extra_deadline` | `uv run pytest -q tests/alerts/test_schedule.py` (stub schedule) → `assert datetime.timedelta(0) == datetime.timedelta(seconds=5400)`; the tweets part: _pending step 13_ |
| AC21 | 16 | `tests/alerts/test_cli.py::test_preview_prints_and_writes_nothing` | |
| AC22 | 8, 13, 14 | `tests/alerts/test_schedule.py::test_rehearsal_is_an_extra_alert_deadline`, `tests/worker/test_cli.py::test_rehearsal_not_written_to_gameweek_and_polls_fast` | `uv run pytest -q tests/alerts/test_schedule.py` (stub schedule) → `assert [] == [True]`; the worker part: _pending step 14_ |
| AC23 | 10 | `tests/alerts/test_slots.py::test_rehearsal_alerts_do_not_count_for_the_real_deadline` | `uv run pytest -q tests/alerts/test_slots.py` (stub `run_slot`) → `assert 'stub' == 'sent'` |
| AC24 | 8, 14 | `tests/alerts/test_schedule.py::test_rehearsal_past_ignored_and_overlap_rejected`, `tests/worker/test_cli.py::test_worker_rejects_overlapping_rehearsal_naming_the_variable` | `uv run pytest -q tests/alerts/test_schedule.py` (stub schedule) → `Failed: DID NOT RAISE ConfigError`; the worker part: _pending step 14_ |
| AC25 | 18 | `tests/test_env_example.py::test_every_alert_variable_is_an_empty_placeholder`, `tests/test_readme.py::test_deployment_describes_rehearsal_variable` | |
| AC26 | 14, 15 | `tests/alerts/test_status.py::test_alert_status_next_slot_last_alert_and_failures`, `tests/worker/test_cli.py::test_status_shows_alerts_line`, `tests/alerts/test_cli.py::test_status_shows_next_slot_last_alert_and_failures` | |
| AC27 | 5, 6, 8, 10, 11, 17 | the step tests above and `tests/alerts/test_end_to_end.py` | n/a — the AC is delivered by the tests themselves |
| AC28 | 12 | `tests/alerts/test_loop.py::test_logs_carry_no_bodies_addresses_or_texts` | |

## Steps

### Group 1 — Ownership column, alert log schema, configuration

- [x] 1. **Migration `0008`: `player.selected_by_percent`** — files:
      `backend/migrations/versions/0008_player_ownership.py`, `backend/app/fpl/models/reference.py`,
      `backend/tests/db/test_migrations.py`.
      Write first `test_ownership_migration_keeps_rows_and_downgrades`: upgrade to `0007`, apply
      the recorded bootstrap, snapshot `table_contents` (excluding the new column), upgrade to
      `0008` → the column exists, is `NULL` for every row, other tables and columns unchanged;
      downgrade `-1` → the column is gone, contents unchanged. Then the migration
      (`op.add_column("player", sa.Column("selected_by_percent", sa.Numeric(5, 1), nullable=True))`,
      the downgrade drops it) and the model field
      `selected_by_percent: Decimal | None = Field(default=None, sa_column=Column(Numeric(5, 1),
      nullable=True))`. The older migration tests select every model column of `player` at
      revisions without it: add `"selected_by_percent"` to their `exclude` sets (it also hides the
      same-named column of `deadline_snapshot_player`, which these tests never change).
      The older migration tests also seed with `apply_bootstrap` at revisions `0001`–`0007`, and
      after step 2 its `Player` upsert writes `selected_by_percent`, a column those revisions do
      not have. Add a test helper `_apply_bootstrap_at_revision(session)` in
      `tests/db/test_migrations.py` that runs `apply_bootstrap` with `app.fpl.reference.upsert`
      patched (`unittest.mock.patch`) to drop every row key that is not a column of the live table
      (`inspect(session.connection()).get_columns(...)`), and use it for every `apply_bootstrap`
      call made before `0008` (the new test included). Product code is not changed for this.
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_migrations.py`
- [x] 2. **Reference sync writes ownership** — files: `backend/app/fpl/reference.py`,
      `backend/tests/fpl/test_reference_sync.py`.
      Write first `test_selected_by_percent_written_and_refreshed`: apply the recorded bootstrap →
      every `player.selected_by_percent` equals the payload's `Decimal`; apply a copy with one
      element's value changed → that row holds the new value. Then add
      `"selected_by_percent": el.selected_by_percent` to the `Player` upsert in `apply_bootstrap`.
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_reference_sync.py
      tests/fpl/test_deadline_snapshot.py tests/db/test_migrations.py` (the migration tests prove
      the step 1 helper keeps the older revisions seedable).
- [x] 3. **Migration `0009`: alert log tables** — files:
      `backend/migrations/versions/0009_alert_log.py`, `backend/app/alerts/__init__.py`
      (registers the models, like `app/worker/__init__.py`), `backend/app/alerts/models.py`,
      `backend/migrations/env.py` (import `app.alerts.models`), `backend/tests/db/test_migrations.py`
      (import `app.alerts.models`; add `alert`, `alert_post` to the `other_tables` exclusions of the
      older tests), `backend/tests/alerts/__init__.py`.
      Write first `test_alert_log_migration_adds_only_new_tables` (pattern of
      `test_delivery_migration_adds_only_new_table`: seed a job run, a tweet and a delivery log row
      at `0008`, upgrade → both tables exist with a unique constraint on `alert.key`, other tables
      unchanged; downgrade → gone, other tables unchanged).
      Tables (`utc_column()` for every time):
      - `alert`: `id` PK; `key` str, unique `uq_alert_key`; `deadline_key` str; `deadline_at`;
        `kind` str (`digest` | `news` | `breaking`); `slot_minutes` int nullable (null for
        breaking); `trigger_x_id` BigInteger nullable FK `tweet.x_id` (breaking only); `as_of`;
        `status` str (`sent` | `failed` | `skipped`); `delivery_log_id` int nullable FK
        `delivery_log.id`; `recorded_at`; index `ix_alert_deadline_key_recorded_at`
        (`deadline_key`, `recorded_at`).
      - `alert_post`: PK (`alert_id` FK `alert.id`, `player_season`, `player_fpl_id`,
        `tweet_x_id`); FK (`player_season`, `player_fpl_id`) → `player`; `tweet_x_id` BigInteger FK
        `tweet.x_id`; `freshness` str (`new` | `context`); index `ix_alert_post_tweet_x_id`.
      Automatic verification: `cd backend && uv run pytest -q tests/db/test_migrations.py`
- [x] 4. **Alert configuration** — files: `backend/app/alerts/config.py`,
      `backend/tests/alerts/test_config.py`.
      Write first the tests: defaults (`(120, 30)`, `3`, `Decimal("15")`, no rehearsal);
      `test_invalid_values_name_the_variable` parametrised over `ALERT_SLOTS_MINUTES` = `abc`,
      `0,30`, `30,120`, `120,120`, `120,-5`, `""`→default; `ALERT_TRENDING_MIN_ACCOUNTS` = `0`,
      `x`; `ALERT_WIDELY_OWNED_PERCENT` = `-1`, `101`, `x`; `ALERTS_ENABLED` = `maybe`;
      `ALERT_REHEARSAL_DEADLINE` = `tomorrow` — each raises `ConfigError` whose message names the
      variable; `test_alerts_disabled_reasons` (each of delivery / tweet ingest / extraction
      missing → disabled with that reason; `ALERTS_ENABLED=false` → disabled with
      `ALERTS_ENABLED=false`; all on → enabled).
      Shape:
      ```python
      class AlertSettings(BaseSettings):  # env_file=".env", extra="ignore", env_ignore_empty=True
          alerts_enabled: str = "true"
          alert_slots_minutes: str = "120,30"
          alert_trending_min_accounts: str = "3"
          alert_widely_owned_percent: str = "15"
          alert_rehearsal_deadline: str = ""

      @dataclass(frozen=True)
      class AlertConfig:
          slots: tuple[int, ...]            # minutes before the deadline, strictly decreasing
          trending_min_accounts: int
          widely_owned_percent: Decimal
          rehearsal_deadline: datetime | None   # UTC, parsed with parse_local

      def parse_alert_config(settings: AlertSettings) -> AlertConfig        # ConfigError naming the variable
      def alerts_disabled_reason(settings: AlertSettings, *, delivery: bool,
                                 tweet_ingest: bool, extraction: bool) -> str | None
      ```
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_config.py`

### Group 2 — The alert engine

- [x] 5. **Template and rendering** — files: `backend/app/content/alert_email.toml`,
      `backend/app/alerts/schemas.py`, `backend/app/alerts/render.py`,
      `backend/tests/alerts/helpers.py` (builders of `ListedPlayer` / `PlayerReport` /
      `Corroboration` values for render tests), `backend/tests/alerts/test_render.py`,
      `backend/tests/content/test_alert_email.py`.
      Write first: `test_template_has_every_key_and_renders` (every key `render.py` reads exists;
      placeholders are filled — no `{` left); `test_player_section_fields` (name and club;
      league-owned with every manager's name and team name; widely owned with the percentage;
      trending with the account count; event type and certainty through the template's Polish
      names; the grade; supporting and contradicting counts; the reversal note only when flagged;
      the search-failed note only when set; a link line for the anchor and each supporting and
      contradicting citation; `new` marker only on posts in `new_x_ids`; players in the given
      order); `test_times_are_warsaw` (a UTC time renders as its `Europe/Warsaw` `format_local`);
      `test_alerts_code_has_no_polish_literals` (AST scan of `app/alerts/**/*.py`: no string
      constant contains any of `ąćęłńóśźżĄĆĘŁŃÓŚŹŻ`). The tests check fields, not whole texts.
      Schemas:
      ```python
      @dataclass(frozen=True)
      class ManagerRef: manager_name: str; team_name: str
      @dataclass(frozen=True)
      class ListedPlayer:
          player: PlayerRef                        # with team_name
          selected_by_percent: Decimal | None
          managers: tuple[ManagerRef, ...]         # non-empty = league-owned
          widely_owned: bool
          trending_accounts: int | None            # set = trending
          claim_x_ids: tuple[int, ...]             # his claim posts in the window
      @dataclass(frozen=True)
      class PlayerReport:
          listed: ListedPlayer
          corroboration: Corroboration
          new_x_ids: frozenset[int]
          search_failed: bool
      @dataclass(frozen=True)
      class AlertDeadline:
          key: str; deadline_at: datetime; rehearsal: bool; season: str | None; gameweek: int | None
      ```
      `render.render_alert(kind, deadline, as_of, reports, listed_without_news) -> Message`;
      the template carries the titles per kind, the section labels, the names of the four event
      types, the three certainties and the three grades, the reversal note, the search-failed
      note, the new marker and the digest's "no news" line (with `{count}`). Search failure =
      `search_failed` or `corroboration.retrieval.failure is not None`.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_render.py
      tests/content/test_alert_email.py`
- [x] 6. **Players in scope** — files: `backend/app/alerts/players.py`,
      `backend/tests/alerts/test_players.py` (synthetic managers, leagues and picks; the
      `seed_reference` / `add_claim` helpers from `tests/corroboration/helpers.py`).
      Write first: `test_league_owned_uses_latest_synced_picks_with_managers` (picks for GW4 and
      GW5 → GW5 counts; positions 1–15 incl. the bench; only managers of the given league IDs in
      the current season; managers listed with name and team name);
      `test_widely_owned_at_threshold` (14.9 no, 15.0 yes, `NULL` no);
      `test_trending_counts_independent_accounts_with_reposts` (3 posts from 2 accounts, one a
      repost of the first account's original → 2 accounts, not trending at 3; a third account →
      trending, count 3; posts outside the window ignored);
      `test_only_players_with_a_claim_are_reported_once_with_all_categories`;
      `test_order_by_ownership_nulls_last`.
      ```python
      def listed_players(session: Session, season: str, league_ids: list[int],
                         start: datetime, as_of: datetime, config: AlertConfig
                         ) -> tuple[list[ListedPlayer], int]   # (with a claim, ordered; count without)
      ```
      One `current_extractions(session, created_from=start, created_until=as_of)` call gives every
      claim in the window; events are grouped by player of `season`; trending counts distinct
      `account_of(post)` (from `app.corroboration.rules`). League-owned: latest
      `gameweek_fpl_id` in `manager_pick` among `league_membership` rows of `league_ids` and
      `season`. The count of listed players without a claim feeds the digest's "no news" line.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_players.py`
- [x] 7. **Corroboration: given anchor and shared runtime builder** — files:
      `backend/app/corroboration/service.py`, `backend/app/corroboration/runtime.py` (new; the
      moved `_runtime_from_settings` as `build_runtime(settings, clock)` plus
      `sql_only_runtime(reason: str, tracer=NULL_CORROBORATION_TRACER) -> CorroborationRuntime`),
      `backend/app/corroboration/cli.py`, `backend/tests/corroboration/test_service.py`.
      Write first `test_given_anchor_is_the_anchor` (three claims; `anchor_x_id` = the oldest →
      it is the anchor, the others labelled against it; an `anchor_x_id` with no claim → the
      newest, as today). Then the keyword `anchor_x_id: int | None = None` in `corroborate`.
      Automatic verification: `cd backend && uv run pytest -q tests/corroboration/`
- [x] 8. **Slot planning** — files: `backend/app/alerts/schedule.py`,
      `backend/tests/alerts/test_schedule.py`.
      Write first: `test_deadline_keys` (`"2026/27:gw6"`, `"rehearsal:2026-10-04T16:00Z"`);
      `test_rehearsal_is_an_extra_alert_deadline` (real deadlines plus the rehearsal, sorted; the
      next alert deadline after `now` may be the rehearsal);
      `test_missed_slots_due_in_order_until_deadline` (at T-125 nothing due; at T-120 the digest;
      at T-20 with nothing done: digest then news; with both done: none; at or after the deadline:
      none); `test_breaking_window` (from the last slot's alert being recorded until the deadline);
      `test_next_wake` (5 s inside the breaking window, otherwise the next slot capped at 60 s);
      `test_polling_window` (`max(90, first slot + 10)` minutes: 130 for the defaults, 90 for
      `(60, 30)`); `test_rehearsal_past_ignored_and_overlap_rejected` (a past rehearsal → no
      deadline and no error; a rehearsal whose `[R − (first slot + 10 min), R]` overlaps
      `[D − (first slot + 10 min), D]` of any real deadline of the season (a just-passed one
      included, as AC24 says "a real deadline's alert window") → `ConfigError` naming
      `ALERT_REHEARSAL_DEADLINE`; adjacent but not overlapping → fine).
      ```python
      def alert_deadlines(season: str | None, gameweeks: list[GameweekState],
                          rehearsal: datetime | None) -> list[AlertDeadline]
      def next_alert_deadline(deadlines: list[AlertDeadline], now: datetime) -> AlertDeadline | None
      def due_slots(deadline: AlertDeadline, slots: tuple[int, ...], done: set[int],
                    now: datetime) -> list[int]
      def breaking_open(deadline: AlertDeadline, last_slot_done: bool, now: datetime) -> bool
      def next_wake(...) -> float
      def polling_window(config: AlertConfig | None) -> timedelta
      def check_rehearsal(config: AlertConfig, real_deadlines: list[datetime], now: datetime) -> None
      ```
      Uses `next_deadline_after` from `app/fpl/deadlines.py` (no own deadline helper).
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_schedule.py`
- [x] 9. **Alert log store and repost origin** — files: `backend/app/tweets/reposts.py`,
      `backend/tests/tweets/test_reposts.py`, `backend/app/alerts/store.py`,
      `backend/tests/alerts/test_store.py`.
      Write first: `test_reposted_x_id_per_source` (a repost from each recorded payload page in
      `tests/tweets/payloads/` gives its original's ID, an original gives `None`, an empty or
      malformed raw gives `None`; if a page holds no repost, a synthetic raw of that source's
      shape); `test_included_is_scoped_to_the_deadline_key` (posts of `sent` and `failed`
      alerts count, of another key do not, `skipped` alerts have none);
      `test_included_origins_cover_reposts`; `test_breaking_context_rows_are_not_included` (a
      breaking alert's `new` row counts, its `context` rows do not; every row of a digest or news
      alert counts); `test_record_is_unique_per_key` (a second record
      with the same key does not add a row); `test_done_slots_and_last_alert`.
      ```python
      def record_alert(engine, *, key, deadline: AlertDeadline, kind, slot_minutes, trigger_x_id,
                       as_of, status, delivery_log_id, posts: list[IncludedPost],
                       recorded_at) -> int | None   # INSERT … ON CONFLICT (key) DO NOTHING
      def alert_exists(session, key: str) -> bool
      def done_slots(session, deadline_key: str) -> dict[int, AlertRow]
      def included_origins(session, deadline_key: str, before: datetime | None = None) -> set[int]
      def status_rows(session, deadline_key: str) -> (last AlertRow | None, failed count)
      ```
      `included_origins` returns, for every included post (design choice 4: all rows of
      digest/news alerts, `new` rows of breaking alerts, statuses `sent`/`failed`), its `x_id` and
      `reposted_x_id(raw)`; `before` limits it to alerts with `as_of < before` (for `preview`).
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/test_reposts.py
      tests/alerts/test_store.py`
- [x] 10. **Digest and news slots** — files: `backend/app/alerts/service.py`,
      `backend/tests/alerts/test_slots.py` (fake channel, fixed clock, a `CorroborationRuntime`
      with `FakeEmbedder` and the scripted judge from the corroboration tests).
      Write first: `test_digest_covers_previous_deadline_to_slot_with_full_corroboration`
      (window start = previous real deadline, `as_of` = the clock; the judge is called; one
      e-mail with key `alert:<key>:digest:120`; `alert` row `sent` with the delivery log ID;
      `alert_post` rows for every claim of each reported player plus judged citations, all
      `new`); `test_digest_without_claims_says_no_news` (still sent);
      `test_news_reports_only_players_with_unincluded_posts_and_marks_new`;
      `test_news_with_nothing_new_is_skipped` (no channel call, `alert` row `skipped`);
      `test_repeated_run_sends_once`; `test_lost_record_after_send_is_recovered_without_resend`
      (delete the `alert` row after a send, run again → no second channel call, the row comes back
      `sent` with the same delivery log ID); `test_restart_sends_missed_slot_up_to_send_time`;
      `test_no_send_at_or_after_deadline` (a clock that passes the deadline during
      corroboration → no channel call, no row); `test_failed_delivery_recorded_and_not_retried`
      (channel raises `ChannelRejectedError` → row `failed`; the same slot again and the next
      slot do not resend it or report its posts as new);
      `test_failed_corroboration_falls_back_to_sql_with_note` (a runtime whose corroboration
      raises for one player → that player still in the e-mail, SQL-only, search-failed note; the
      other players full); `test_rehearsal_alerts_do_not_count_for_the_real_deadline` (a rehearsal
      digest and news, then the real digest reports the same posts as new and the real news
      treats them as not included).
      ```python
      @dataclass(frozen=True)
      class AlertsRuntime:
          config: AlertConfig
          league_ids: list[int]
          delivery: DeliveryService
          corroboration: CorroborationRuntime

      def build_slot_alert(engine, runtime, deadline: AlertDeadline, kind: Literal["digest","news"],
                           slot_minutes: int, as_of: datetime,
                           included: set[int]) -> SlotDraft     # reads only; preview uses it
      def run_slot(engine, runtime, deadline, slot_index: int, clock: Clock) -> str   # outcome
      ```
      `run_slot`: `as_of = clock.now()`; season = the current season (latest `season.label`);
      start = `window_start(session, as_of)`; listed players; for news, keep players with a claim
      not already included; corroborate each (full; on an exception the SQL-only runtime and
      `search_failed=True`), `new_since` = the previous recorded alert's `as_of` for news;
      render; if `clock.now() >= deadline_at` → log and return without sending or recording;
      send with the alert key; record (`sent` also for `already_sent`). News with nothing new →
      record `skipped`. Logs: alert key, kind, player and post counts, outcome only.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_slots.py`
- [x] 11. **Breaking alerts** — files: `backend/app/alerts/breaking.py`,
      `backend/tests/alerts/test_breaking.py`.
      Write first: `test_breaking_anchor_is_the_post_sql_only` (an older post extracted after the
      news → its e-mail anchors on it, other claims in the window as support/contradiction, no
      embedder or judge call; key `alert:<key>:breaking:<x_id>`; `alert_post` rows: the trigger
      post `new`, every other post shown `context`);
      `test_two_posts_extracted_in_one_tick_each_break` (P1 and P2 about the same listed player,
      both extracted before one tick → two e-mails, P2 shown as support in P1's and having its
      own); `test_included_post_or_its_repost_never_breaks`
      (a post in the news, a later repost of it, a repost of a post in the news → none sends;
      a post and its repost in the same tick → one e-mail); `test_unlisted_player_does_not_break`;
      `test_extracted_before_last_slot_is_not_breaking`; `test_breaking_repeated_tick_sends_once`;
      `test_post_naming_two_listed_players_is_one_email_with_two_sections`;
      `test_no_breaking_at_or_after_deadline`.
      ```python
      def run_breaking(engine, runtime: AlertsRuntime, deadline: AlertDeadline,
                       since_extracted: datetime, clock: Clock) -> int   # e-mails sent
      ```
      Candidates per design choice 6; corroboration per player with
      `sql_only_runtime(...)` and `anchor_x_id=post`; `as_of = clock.now()`.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_breaking.py`
- [ ] 12. **Alerts loop** — files: `backend/app/alerts/loop.py`, `backend/tests/alerts/test_loop.py`.
      Write first: `test_breaking_sent_within_15_s_of_extraction` (the loop's `run()` with a
      `tests/worker/sim.FakeClock`-style clock whose `sleep` advances time; at a set virtual time
      inside the breaking window a hook stores a post and its extraction with `finished_at` = that
      time; then stops → `delivery_log.accepted_at − extraction.finished_at ≤ 15 s`);
      `test_tick_runs_due_slots_then_breaking`; `test_tick_error_does_not_stop_the_loop` (a tick
      raising → logged with the class name only, the next tick runs);
      `test_logs_carry_no_bodies_addresses_or_texts` (caplog over a digest, a news and a breaking
      alert: no post text, author handle, e-mail address, manager or team name, title or body);
      `test_stop_event_ends_loop_promptly`.
      ```python
      class AlertLoop:
          def __init__(self, engine, runtime: AlertsRuntime, clock: Clock,
                       stop_event: threading.Event) -> None
          def tick(self) -> float          # seconds to sleep
          def run(self) -> None
      def start_alerts(engine, runtime, stop_event, clock: Clock | None = None) -> threading.Thread
      ```
      `tick`: load the gameweeks of the current season (`app.worker.store.load_state`), build
      alert deadlines with the rehearsal, take the next one; run every due slot in order; if the
      breaking window is open, `run_breaking` with `since_extracted` = the last slot alert's
      `as_of`; flush the corroboration tracer; return `next_wake`. Thread name `alerts`.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_loop.py`

### Group 3 — Worker, CLI and documentation

- [ ] 13. **Tweet polling window from the alert config** — files: `backend/app/tweets/schedule.py`,
      `backend/app/tweets/loop.py`, `backend/tests/tweets/test_schedule.py`,
      `backend/tests/tweets/test_loop.py`.
      Write first `test_custom_window_and_extra_deadline` (with `window=130 min` the mode is
      `window` at T-130 and `sparse` at T-131; a sparse poll does not skip the 130-min window
      start; an extra deadline not in the gameweek list opens its own window) and
      `test_poller_uses_window_and_extra_deadlines` in `test_loop.py`. Then `window: timedelta =
      WINDOW` on `mode`, `interval`, `next_poll_at`; `TweetPoller`/`start_poller` take
      `window: timedelta = WINDOW` and `extra_deadlines: tuple[datetime, ...] = ()` and merge them
      into `upcoming_deadlines(...)`. Defaults keep today's behaviour (AC20: 90 min when alerts
      are off).
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/test_schedule.py
      tests/tweets/test_loop.py`
- [ ] 14. **Worker integration** — files: `backend/app/alerts/status.py`,
      `backend/tests/alerts/test_status.py`, `backend/app/worker/cli.py`,
      `backend/tests/worker/test_cli.py`.
      Write first: `tests/alerts/test_status.py::test_alert_status_next_slot_last_alert_and_failures`
      (`alert_status(engine, config, now) -> AlertStatus`: the current alert deadline, the next
      slot or the breaking window's end, the last alert's kind, time and outcome or none, and the
      failed count of the current deadline key; built on `schedule` (step 8) and `store.status_rows`
      (step 9)); `test_status_shows_alerts_disabled_with_reason` (`Alerts: disabled (delivery
      disabled)` etc.); `test_status_shows_alerts_line` (`Alerts: next slot: digest
      <UTC time>  last alert: news <UTC time> sent  failed: 1`, and `breaking until <time>` inside
      the breaking window); `test_run_starts_alerts_only_when_enabled` (thread `alerts` alive with
      an injected runtime, absent without); `test_worker_rejects_invalid_alert_slots_naming_the_variable`;
      `test_worker_rejects_overlapping_rehearsal_naming_the_variable` (run exits 1, stderr names
      `ALERT_REHEARSAL_DEADLINE`); `test_rehearsal_not_written_to_gameweek_and_polls_fast` (with a
      rehearsal set, the poller receives it as an extra deadline and the 130-min window; the
      `gameweek` table is unchanged). Add `ALERT.*` to the env-clearing regex of the test module.
      `WorkerDeps` gains `alerts: AlertsSetup | None` (config, a `make_channel`, a
      `make_corroboration` factory, optional clock) and `alerts_disabled_reason: str | None`;
      `_deps_from_settings` parses `AlertSettings` (errors → `fail`), computes the reason from the
      three features, and builds the setup with `build_runtime` (step 7). `run`: with alerts on,
      `check_rehearsal` against the gameweek deadlines (error → exit 1 naming the variable),
      start the poller with `polling_window(config)` and the rehearsal as an extra deadline, start
      the alerts thread after the indexer, join it on shutdown, close the channel. `status`: the
      tweet mode uses the same window; a final `Alerts:` line from
      `app.alerts.status.alert_status(...)` (created in this step; step 15 reuses it).
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_status.py
      tests/worker/`
- [ ] 15. **Alerts CLI: `status` and `latency`** — files: `backend/app/alerts/latency.py`, `backend/app/alerts/cli.py`, `backend/app/alerts/__main__.py`,
      `backend/tests/alerts/test_latency.py`, `backend/tests/alerts/test_cli.py`.
      Write first: `test_percentiles_and_legs` (nearest-rank p50 / p95 / max over fixed rows;
      legs post → first fetch, fetch → extraction done, extraction → accepted; empty → count 0
      and `-`); `test_latency_default_gameweek_and_rehearsal` (the latest alert deadline by
      default; `--gameweek 6` → that key of the current season; `--rehearsal` → the latest
      rehearsal key; only posts of `sent` alerts, each at its first alert; output has the count
      and the three statistics per leg and in total); `test_status_shows_next_slot_last_alert_and_failures`;
      `test_cli_rejects_invalid_alert_variable` (exit 1, stderr names the variable).
      Latency per post: `tweet.created_at`, `tweet.first_fetched_at`, the earliest `finished_at`
      of its `extracted` extractions, the `accepted_at` of the first sent alert including it.
      `status` prints, from `alert_status` (step 14), the current alert deadline (key and Warsaw
      time), the next slot (or `breaking until …`), the last alert (kind, Warsaw time, outcome)
      and the failed count.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_latency.py
      tests/alerts/test_cli.py`
- [ ] 16. **Alerts CLI: `preview`** — files: `backend/app/alerts/cli.py`,
      `backend/tests/alerts/test_cli.py`.
      Write first `test_preview_prints_and_writes_nothing` (`--at` a Warsaw time before a
      deadline, default `--kind digest` and `--kind news`: prints the title and text of what
      `build_slot_alert` builds for the next alert deadline after `--at`, with "included" =
      alerts of that key with `as_of < at`; news with nothing new prints that the slot would be
      skipped; every table's row count unchanged and no channel call; a bad `--at` → exit 1
      with the expected format). Preview uses `build_runtime` (full corroboration with the key,
      SQL-only with a skipped reason without it) and needs no delivery configuration.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_cli.py`
- [ ] 17. **End-to-end test** — files: `backend/tests/alerts/test_end_to_end.py`.
      One simulated real deadline driven through `AlertLoop.tick()` with a fake clock, the fake
      channel and the fake corroboration runtime: seed league picks, ownership and claims; T-120
      digest; a new post → T-30 news marking it new; two new posts after T-30 → two breaking
      e-mails; one repeated post (a repost of an included post) → none; at the deadline nothing
      more; then a rehearsal deadline on the same data with its own digest, news and breaking,
      and a check that the real deadline's included set is unchanged. Assertions on the channel's
      keys, the `alert` rows and the `alert_post` freshness.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_end_to_end.py`
- [ ] 18. **Documentation and roadmap** — files: `backend/.env.example`,
      `backend/tests/test_env_example.py`, `backend/tests/test_readme.py`, `docs/DEPLOYMENT.md`,
      `README.md`, `docs/ROADMAP.md`, `docs/DECISIONS.md`.
      Write first: `test_every_alert_setting_field_has_its_variable_covered` and
      `test_every_alert_variable_is_an_empty_placeholder` (the five `ALERT*` variables);
      `test_deployment_describes_rehearsal_variable` (`docs/DEPLOYMENT.md` names
      `ALERT_REHEARSAL_DEADLINE` as optional, for testing, to be removed after use).
      Then: `.env.example` block for the five variables with comments (defaults, the three
      preconditions, the rehearsal as a Warsaw time for testing, removed after use);
      `DEPLOYMENT.md` step 12 "Alerts (optional)" — when they run, the variables and their
      validation, migrations `0008`/`0009`, `python -m app.alerts status | latency | preview`, the
      worker's `Alerts:` line, the rehearsal (any environment, real e-mails, not in the gameweek
      table, past = ignored, overlap = start failure, remove after use); `README.md` a short
      "Alerts" paragraph with the three CLI commands in `## Development`; `ROADMAP.md` — the
      preconditions line of the item replaced by the owner's order (built and tested locally
      first; the deployment follows; BACKLOG #11 checked after this spec and before the
      deployment) and a new Stage 2 item after the deployment: "LLM-written per-player alert
      summary with a faithfulness evaluation (spec: TBD)"; the item itself is ticked by
      `/pipeline:implement` when the spec is done; `DECISIONS.md` one row (2026-10-02, spec 009
      plan) for: the alert log as the state with send-then-record, included = posts of `sent`
      and `failed` alerts, the repost origin read from the raw payload, `corroborate(…,
      anchor_x_id)` and `build_runtime` shared from `app/corroboration/runtime.py`.
      Automatic verification: `cd backend && uv run pytest -q tests/test_env_example.py
      tests/test_readme.py tests/test_docs.py tests/test_deployment.py`, then the full
      `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`

## Risks and traps

- **News slot duration delays breaking.** With N players the T-30 slot runs N full
  corroborations one after another (each up to 10 judge calls); breaking e-mails for posts
  extracted meanwhile go out after it. AC14 holds in steady state; the latency report shows the
  real figure. Do not split threads to "fix" it (design choice 1).
- **Deadline cut-off after a long build.** Check `clock.now() < deadline_at` right before
  `DeliveryService.send`, not only when the slot starts. The in-call delivery retries can still
  end a few seconds after the deadline; acceptable (at most ~6 s of back-off).
- **Send-then-record.** A crash between the two is healed by the delivery key (`already_sent`);
  the rebuilt alert's `alert_post` rows may differ slightly from the sent text — acceptable,
  stated here so the reviewer does not "fix" it with a pre-send claim row.
- **Migration tests.** `table_contents` selects every model column; the new `player` column and
  the two new tables must be excluded in every older-revision test, or they fail with
  "column does not exist"; and every `apply_bootstrap` seed before `0008` goes through the
  step 1 helper, because after step 2 the `Player` upsert writes the new column.
  `test_models_match_migration` catches a model/migration mismatch
  (index names, `Numeric(5, 1)`, BigInteger FKs to `tweet.x_id`).
- **Season and keys.** The current season is the latest `season.label`; a rehearsal key has no
  season. Keys contain `/` and `:` — fine for `delivery_log.idempotency_key` and Resend's
  `Idempotency-Key` header (max 256 chars).
- **Time zones.** Everything is UTC inside; `--at` and `ALERT_REHEARSAL_DEADLINE` are parsed with
  `parse_local` (naive = Warsaw); rendering uses `format_local`.
- **Synthetic data only.** Managers, teams and leagues in tests are made up; no real e-mail
  addresses (use `owner@example.test`).
- **Logs.** No titles, bodies, post texts, handles, manager names or addresses — only the alert
  key, kind, counts and outcome (AC28, CONVENTIONS).
- **Module boundaries.** `app/tweets` must not import `app/alerts`; `app/alerts` may import
  `app.extraction.store.current_extractions` (the one public current-extraction query) but
  must not write its own `DISTINCT ON (tweet_x_id)` SQL (`test_current_extraction_sql_lives_once`).

## End-to-end verification

### Automatic (performed by /pipeline:implement)

1. `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` —
   all green.
2. Against the development database (`docker compose up -d db`, `DATABASE_URL` from
   `backend/.env.example`): `cd backend && uv run alembic upgrade head`, then
   `uv run alembic downgrade -2` and `uv run alembic upgrade head` — all succeed; `\d player`
   shows `selected_by_percent`, `\dt alert*` shows `alert` and `alert_post`.
3. `cd backend && uv run python -m app.alerts --help` lists `preview`, `latency`, `status`;
   `uv run python -m app.alerts status` on the development database prints the current alert
   deadline and `last alert: never`; with `ALERT_SLOTS_MINUTES=30,120`
   `uv run python -m app.alerts status` exits 1 naming `ALERT_SLOTS_MINUTES`.
4. `cd backend && uv run python -m app.worker status` with no `DELIVERY_PROVIDER` ends with
   `Alerts: disabled (delivery disabled)`.
5. `cd backend && uv run python -m app.alerts preview --at <a Warsaw time before the next
   deadline>` prints a digest (or the "no news" digest) and writes nothing.
Record the outcomes under "Definition of Done".

### Manual (performed by the owner)

1. **Rehearsal run.** With delivery, tweet ingest and extraction configured locally, set
   `ALERT_REHEARSAL_DEADLINE` to a Warsaw time about 2.5 h ahead and run the worker: the
   digest arrives at T-120, news at T-30 (if anything new), breaking e-mails after T-30; check
   the Polish text, the categories and managers, the grades, the links and the Warsaw times;
   nothing arrives at or after the rehearsal moment. Then remove the variable.
2. **Latency after the rehearsal.** `python -m app.alerts latency --rehearsal` prints the count
   and p50 / p95 / max per leg.
3. **First real deadline (GW6).** The same three kinds arrive for the real deadline, including
   posts the rehearsal already reported.

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
      fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, `docs/DEPLOYMENT.md`, `README.md`,
      `backend/.env.example` updated
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation: date, stage, question, decision)_

## Review log

### 2026-10-02 — /pipeline:plan-review

Findings (severity counted before the fixes):

| # | Severity | Finding | Change |
|---|----------|---------|--------|
| R1 | `major` | Step 2 makes `apply_bootstrap` write `player.selected_by_percent`, but `tests/db/test_migrations.py` seeds with `apply_bootstrap` at revisions `0001`–`0007` (job run, tweet, extraction, retrieval, repost, delivery tests and the new step 1 test), where the column does not exist → "column does not exist"; step 2's verification did not run the migration tests, so the break would surface only in the full run. | Step 1 adds the test helper `_apply_bootstrap_at_revision` (patches `app.fpl.reference.upsert` to drop keys the live table lacks) and routes every pre-`0008` seed through it; step 2's verification runs `tests/db/test_migrations.py`; risk noted. |
| R2 | `major` | Forward dependency: step 14 (worker `Alerts:` status line) calls `app.alerts.status.alert_status`, which step 15 created. | `app/alerts/status.py` and `tests/alerts/test_status.py` move to step 14; step 15's CLI reuses it; AC26 row updated. |
| R3 | `major` | Breaking: an alert recorded every post it showed as included, so of two posts about one listed player extracted before the same tick, the second (shown as support in the first's e-mail) never got its own breaking e-mail — against AC12 and the owner decision "one e-mail per new post about a listed player". Step 11's "the rest `context` unless not yet included" was also ambiguous. | Design choice 4: a breaking alert records its trigger post `new` and the others `context`; only `new` rows of breaking alerts count as included (all rows of digest/news alerts still do). Tests `test_breaking_context_rows_are_not_included` (step 9) and `test_two_posts_extracted_in_one_tick_each_break` (step 11); AC12 row updated. |
| R4 | `minor` | Step 8 checked the rehearsal overlap only against future real deadlines; AC24 says "a real deadline's alert window". | The check runs against every real deadline of the season, a just-passed one included. |

Checked and found correct (later stages need not repeat it):

- Coverage: every AC1–AC28 has steps and a proving test; the matrix matches the steps (AC27 `n/a` with a reason; the fourth column present).
- Code facts the plan rests on: `corroborate(...)` returns early with no claim and skips search/judge when `embedder`/`judge` is `None` (so `sql_only_runtime` gives AC12's SQL-only path); `window_start` uses the gameweek table, so a rehearsal's window starts at the previous real deadline (AC22); `current_extractions` returns `finished_at` and takes `created_from`/`created_until`; `DeliveryService.send` returns `already_sent` for a `sent` key (send-then-record heals a lost record) and kind `alert` exists; `_runtime_from_settings` exists in `app/corroboration/cli.py`; `Element.selected_by_percent` is in the bootstrap schema; `account_of` counts a repost as its original author; the repost original ID paths match the stored raw payloads (twscrape `retweetedTweet`, twitterapi.io `retweeted_tweet`, X API `tweet.referenced_tweets`); league sync writes picks in one job transaction, so the max `gameweek_fpl_id` in `manager_pick` is a fully synced gameweek; `load_state` and `next_deadline_after` exist; corroboration writes nothing to the database (preview stays read-only).
- Compliance: CONVENTIONS (Polish text only in `app/content/`, UTC inside and `Europe/Warsaw` at rendering, no names/addresses/texts in logs, synthetic test data, tests before code) and DECISIONS 2026-09-26 (module `alerts`, no FPL flags), 2026-09-30 (one current-extraction query, deadline helpers, refactor in the feature), 2026-10-01 (delivery idempotency and row lock), 2026-10-02 (spec 009 row) — not broken.
- Minimality: reuses delivery, corroboration, extraction store, deadlines and test fakes; the single alerts thread (design choice 1) is simpler than two threads with a lock; no extra column for repost origin.
- Feasibility: no other forward dependency after R2; both migrations accepted in SPEC → Owner decisions; no new dependency; time zones and the deadline cut-off before `send` covered.
- E2E: the automatic part runs on the development database and the CLIs; the manual part keeps only real e-mails and the real deadline.
- Groups: three groups, no boundary leaves work half done; `implement.chunked` is false.
- Owner summary consistent (no new dependency; migrations `0008`/`0009` flagged as accepted). Language: English throughout.
- Accepted as is: news reports a player only for an unincluded *claim* post (a new judge-found post alone does not trigger news) — the AC6 rule that a reported player must have a claim makes this consistent.

Decision: the plan is ready for implementation — every finding was fixable in the plan and fixed, no blocker remains, and the only migrations are the two the owner accepted.

## Chunk notes

_(filled in by /pipeline:implement in chunk mode — one entry per chunk that ends at a group boundary)_

## Deviations

_(filled in by /pipeline:implement — every deviation from the plan with its rationale)_

## Final review

_(filled in by /pipeline:final-review)_
