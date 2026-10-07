# PLAN 010 — Off-list posts: list membership, quotes and conversation context

## Owner summary

- **Approach:** The worker's tweet poller fetches the List's members through twscrape when it
  starts and every 6 hours and stores each snapshot in a new table. A post's class is never
  stored. It is computed in SQL at query time from the latest snapshot and a new
  `tweet.quoted_x_id` column. A post is a **list post** when its author is a member, or when no
  snapshot exists, or when the source cannot list members. It is a **quoted post** when it is
  off-list and a list post quotes it. Any other post is a **context post**. Extraction,
  corroboration claims, retrieval for the judge, breaking alerts, trending and the latency
  reports read list and quoted posts only. A supporting or related quote counts as the
  quoted post's account. The twscrape adapter marks embedded posts and the newest post of each
  timeline entry. Paging and the paging bound use only those markers.
- **Main risks:** the twscrape entry parsing rests on synthetic fixtures shaped on the X
  GraphQL schema, so a schema change would stop catch-ups. A fallback keeps today's behaviour
  and logs a warning in that case. An empty or broken member list could silence every alert,
  so an empty list counts as a failed fetch. A member who changes their handle drops out of
  the list until the next snapshot. The migration tests for older revisions must exclude the
  new table.
- **New dependency:** no. twscrape 0.20.1 already provides `list_members_raw`.
- **Data migration:** yes. `0010` adds `tweet.embedded` (default false) and
  `tweet.quoted_x_id` with an index, backfills `quoted_x_id` from the stored twscrape `raw`
  (`quotedTweet.id`) and adds the `list_membership` table. SPEC → "Owner decisions" accepted
  it up front for the development and test databases. The owner migrates production at
  deployment.
- **Manual scenarios for the owner:** 1. On the local development database: migrate, fetch
  the membership with the new CLI and check how the 22 stored off-list posts are classified,
  including the `@afcstuff` Tzolis post as a quoted post. 2. Run the worker for one poll and
  check that the live List page is parsed into entries (no fallback warning).

## Approach

What the plan rests on:

- `specs/010-off-list-posts/SPEC.md`: read in full.
- `docs/CONVENTIONS.md`: read in full.
- `docs/DECISIONS.md`: searched for "repost", "catch-up", "2026-10-07", "independent" and
  "alert log". Found the 2026-09-30 corroboration row (independent accounts are original
  authors), the 2026-09-30 `reposted_author_handle` row, the 2026-10-02 alert-log row
  (included posts, a repost counts as its original, read from `raw`), the 2026-10-07 catch-up
  row (paging to the newest stored post or the window floor) and the 2026-09-29 retrieval row
  (every stored post is indexed).
- `docs/ROADMAP.md`: searched for "#11", "Stage 2" and "deploy". The Stage 2 alert item says
  BACKLOG #11 is settled after spec 009 and before the deployment. The roadmap has no item
  for this spec.
- `docs/BACKLOG.md`: searched for "#11", "19" and "list". Found row 11 (P1, to be removed)
  and row 19 (stays). `backend/tests/test_docs.py` checks BACKLOG rows by number, priority and
  trigger.
- `docs/DEPLOYMENT.md`: searched for "migrat", "0006", "0009" and "Tweet ingest". Found step 7
  (tweet ingest), step 10 (the migration `0006` note) and step 12 (the `0008`/`0009` notes),
  which give the pattern for the `0010` note.
- `README.md`: searched for "app.tweets". The Development section lists the `measure` and
  `summary` commands, and the new `members` command goes there too.
- Code read in full: `app/tweets/{models,store,ingest,reposts,loop,cli,measure,config,schedule}.py`,
  `app/tweets/sources/{base,__init__,paging,twscrape_source,twitterapi_io,x_api}.py`,
  `app/extraction/{store,loop,models}.py`, `app/corroboration/{schemas,rules,sources,service}.py`,
  `app/alerts/{store,breaking,latency,service,players,cli}.py`, `app/retrieval/search.py`,
  `app/worker/{jobs,loop,models,store,cli}.py`, `migrations/versions/0006_repost_author.py`.
- Tests and fixtures read: `tests/conftest.py`, `tests/db/test_migrations.py` (the
  `0006` backfill test is the pattern), `tests/tweets/fakes.py`,
  `tests/tweets/sources/test_twscrape_source.py` (`FakeApi`/`FakePool`),
  `tests/tweets/payloads/{__init__.py,README.md}`, `tests/alerts/helpers.py`,
  `tests/corroboration/helpers.py`, `tests/retrieval/helpers.py`, `tests/test_docs.py`,
  `tests/test_readme.py`, `tests/test_module_boundaries.py`.
- twscrape 0.20.1 in the venv: `API.list_members_raw` uses the `ListMembers` queue.
  `parse_tweets` collects every tweet object in the response through `to_old_rep`. That is why
  quoted posts appear as separate posts. `parse_users` collects every user object in the same
  way.

### Design

**Schema (migration `0010`, step 1).**
- `tweet.embedded BOOLEAN NOT NULL DEFAULT false` marks a post seen only embedded in another
  entry (a quoted post or a reply parent) and never as a timeline entry.
- `tweet.quoted_x_id BIGINT NULL`, with index `ix_tweet_quoted_x_id` and no foreign key,
  because the quoted post may not be stored. It holds the ID of the post this post quotes.
- New table `list_membership`: `id` (PK), `list_id BIGINT NOT NULL`,
  `source VARCHAR NOT NULL`, `fetched_at timestamptz NOT NULL` (indexed) and
  `handles VARCHAR[] NULL`. `handles` holds lowercased handles, and NULL means the source
  cannot list members.
- Backfill: `quoted_x_id = (raw #>> '{quotedTweet,id}')::bigint` where that value matches
  `^[0-9]+$`. That is the twscrape shape, and only twscrape reports quotes (SPEC → Scope).
  Existing posts keep `embedded = false`, because nothing in `raw` tells whether they came
  embedded. The flag only bounds paging, and the 22 embedded posts stored so far are older than
  the newest timeline post, so this is harmless.
- The model `Tweet` gets
  `embedded: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false")))`
  and `quoted_x_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))`
  (with the index in `__table_args__`). The new model is `ListMembership` in
  `app/tweets/models.py` with `handles: list[str] | None = Field(sa_column=Column(ARRAY(String), nullable=True))`.

**Fetched posts and the store (steps 2–4).** `FetchedPost` gets three fields with defaults, so
the other adapters do not change:
- `embedded: bool = False`
- `entry_head: bool = True`. This marks the post that places its entry in the timeline: the
  post of a single-post entry, or the newest item (highest ID) of a conversation module.
- `quoted_x_id: int | None = None`

`store_posts` becomes an upsert. On conflict it sets
`embedded = tweet.embedded AND excluded.embedded` and
`quoted_x_id = COALESCE(tweet.quoted_x_id, excluded.quoted_x_id)`, but only when one of them
changes (a `WHERE` on the `DO UPDATE`). It counts only the inserted rows
(`RETURNING (xmax = 0)`). Duplicates in one call are merged the same way before the insert,
because Postgres refuses to update one row twice in one statement. As a result, a post seen
first embedded and later as an entry ends up with the stored data of a timeline post,
whichever order the fetches come in (AC7). `last_seen_id` becomes `max(x_id) WHERE NOT embedded`
(AC14). `first_fetched_at` never changes.

`collect_new` (`app/tweets/sources/paging.py`) keeps every post of the fetched pages. The
`since_id` check and the "whole page before the floor" rule look only at the posts with
`entry_head`. A page without any head never ends paging by these two rules (AC13).
Today `collect_new` dedupes with `collected.setdefault(...)`, which keeps the first copy: a
post embedded on a newer page and shown as an entry on an older page of the same catch-up
would reach the store as embedded. A shared helper `merge_fetched(a, b) -> FetchedPost` in
`app/tweets/sources/base.py` (`embedded = a.embedded and b.embedded`,
`entry_head = a.entry_head or b.entry_head`, `quoted_x_id = a.quoted_x_id or b.quoted_x_id`,
every other field from the non-embedded copy) merges duplicates both in `collect_new` and in
`store_posts` before the insert, so the timeline copy wins in every order (AC7, AC14).

**twscrape adapter (step 4).** `TwscrapeSource.pages` reads the entries of each raw page,
`data.list.tweets_timeline.timeline.instructions[*]` → `entries` (and a single `entry`):
- A `TimelineTimelineItem` contributes the ID at `content.itemContent.tweet_results.result.rest_id`
  (or `….result.tweet.rest_id` for `TweetWithVisibilityResults`).
- A `TimelineTimelineModule` (`list-conversation-…`) contributes the ID of each
  `content.items[*].item.itemContent.tweet_results.result`.
- Entries whose `entryId` starts with `cursor-` or `promoted-` contribute nothing.

Every post `parse_tweets` returns gets three values:
- `embedded`: the post is in no entry.
- `entry_head`: the post is the highest ID of its entry.
- `quoted_x_id`: `tweet.quotedTweet.id` when twscrape gives one.

Fallback: a page with posts but no recognised entry keeps the behaviour before this spec
(every post is a head, none embedded) and logs `twscrape: no timeline entries recognised on a
page` as a warning. That way a schema change cannot turn every poll into a 50-page catch-up.
`_to_post` takes the two sets as arguments.

**Membership (steps 5–8).**
- `TweetSource` gets `members(self, list_id: int) -> list[str]`. `TwitterApiIoSource` and
  `XApiSource` raise `MembershipNotSupportedError(CollectorError)`, defined in
  `app/tweets/sources/base.py`.
- `TwscrapeSource.members` pages `self._api.list_members_raw(list_id)` on the same
  `asyncio.Runner`. It reads each page's `TimelineUser` entries
  (`content.itemContent.user_results.result`, with the handle from `core.screen_name` or else
  `legacy.screen_name`) instead of `parse_users`, which would also collect any other user object
  in the response. `NoAccountError` maps to `SourceRateLimitedError` or `SourceUnavailableError`
  as in `pages`, through one helper that takes the queue name (`ListMembers` here). The helper
  is extracted from `pages`. A malformed page raises `SourcePayloadError`, and so does an empty
  result, because an empty snapshot would turn every post into a context post.
- New module `app/tweets/membership.py`:
  - `MembershipSnapshot(fetched_at, source, list_id, handles: frozenset[str] | None)`
  - `fetch_membership(source, list_id, now) -> MembershipSnapshot`. It lowercases the handles,
    and `MembershipNotSupportedError` gives `handles=None`. Every other error propagates.
  - `save_snapshot(engine, snapshot)`
  - `latest_snapshot(session) -> MembershipSnapshot | None`
  - `refresh_membership(engine, source, list_id, now) -> MembershipSnapshot` (fetch + save)
  - `MEMBERSHIP_INTERVAL = timedelta(hours=6)` and `MEMBERSHIP_RETRY = timedelta(minutes=30)`
- `TweetPoller.run`, after the source is built and before the schedule check, calls
  `refresh_membership` when `self._membership_due_at` is `None` (start) or has passed. The next
  due time is `now + MEMBERSHIP_INTERVAL` after a success, `now + MEMBERSHIP_RETRY` after a
  failure, and never again in this run after "not supported" (one NULL row per start, so
  switching away from twscrape takes effect at once). A failure is caught there and logs
  `list membership fetch failed: <ErrorClass>` as a warning. It never touches the poll
  (AC1–AC3).
- The CLI `python -m app.tweets members` (in `app/tweets/cli.py`) reads `TWEET_SOURCE` and
  `X_LIST_ID`, builds the source, calls `refresh_membership` and prints
  `List members: <n>  snapshot: <UTC time>`. For a source that cannot list members it prints
  `List members: not supported by <source> (every post counts as a list post)`. A failure
  prints `error: list membership fetch failed: <ErrorClass>` and exits 1. `MeasureDeps` gets
  `make_engine: Callable[[], Engine] | None = None`. `None` builds the engine from
  `load_settings().database_url`, and tests inject the `db` engine.

**Classification (step 6).** New module `app/tweets/classes.py` holds SQL fragments over a
table alias, so every query applies the same rule:
- `_LATEST = "(SELECT handles FROM list_membership ORDER BY fetched_at DESC, id DESC LIMIT 1)"`
- `list_post_sql(alias) -> str`, which gives
  `({_LATEST} IS NULL OR lower({alias}.author_handle) = ANY({_LATEST}))`. With no snapshot or a
  NULL snapshot, every post is a list post (AC3). A repost's `author_handle` is the list
  account that reposted it (AC5).
- `source_post_sql(alias) -> str`, which gives `(list_post_sql(alias) OR EXISTS (SELECT 1 FROM
  tweet quoting WHERE quoting.quoted_x_id = {alias}.x_id AND list_post_sql('quoting')))`
- `PostClass = Literal["list", "quoted", "context"]`
- `post_classes(session, x_ids) -> dict[int, PostClass]`
- `quoted_authors(session, x_ids) -> dict[int, str]` maps the x_id of a quote to the stored
  quoted post's `author_handle`. A quoted post that is not stored gives no entry.

The aliases are code constants, never input. Reading the class at query time means a change
of the snapshot reclassifies stored posts without rewriting any data (AC5).

**Consumers (steps 9–13).**
- Extraction: `next_pending` and `extraction_status().waiting` add `source_post_sql`. A
  context post is never pending. It becomes pending as soon as its author joins or a list post
  quotes it, because it has no extraction (AC7). `posts_for_reextract` and indexing do not
  change, so every post can still be re-extracted on demand and is still indexed.
- `current_extractions(…, sources_only: bool = False)`, and `SearchFilters.sources_only:
  bool = False` in `_filter_sql` of `app/retrieval/search.py`, both add
  `source_post_sql('t')`. The callers that feed alerts and corroboration pass `True`:
  `corroboration/sources.py` (`sql_claims`, `retrieval_candidates`), `alerts/players.py`
  `_claims` and `alerts/breaking.py` `_candidates` (AC8). Evaluation tooling keeps the default.
  An explicit flag keeps the evaluation and review tooling unchanged.
- `PostRef` gets `quoted_author_handle: str | None = None` as its last field.
  `corroboration/sources.py` and `alerts/players.py` fill it from `quoted_authors`.
- `app/corroboration/rules.py` gets `counted_account(post: PostRef, label: Label) -> str`. It
  returns the lowercased quoted author when `label != "contradicts"` and the post is a quote
  with a stored quoted post, and `account_of(post)` otherwise. `count_accounts` keys accounts
  by `counted_account(item.post, item.label)` and the anchor by
  `counted_account(anchor, "supports")`. `grade` weighs `counted_account(...)`, and the trace
  span's `account` uses it too (AC10). `account_of` itself stays (repost rule).
- Trending in `alerts/players.py` counts `counted_account(post, "supports")`. A claim has no
  label there, so a quote joins the quoted author like a repost does. This is the only reading
  that keeps "the quote and the quoted post count as one account" (SPEC → Goal) when both
  name the player. Counting them separately was rejected because it would let one leak plus a
  quote of it reach the trending threshold faster.
- "Included" bookkeeping (`included_origins`, `post_origin_sets`) needs no change. A quoted
  post is an ordinary source row in `alert_post`. Step 12 adds the proving test (AC11).
- Latency (AC12): `post_latencies` in `app/alerts/latency.py` adds `list_post_sql('t')`. In
  `app/tweets/cli.py` `_measure_one`, after building the source, the command tries
  `source.members(list_id)` once. With members, it records only posts whose lowercased author
  is a member. When membership is not supported or the fetch fails, it records only posts with
  `not post.embedded`. Its own `since_id` is the maximum over posts with `not post.embedded`
  only, as `last_seen_id` (AC14). `summary` reads what `measure` wrote, so it counts list posts only.

Rejected variants (one sentence each):
- **Storing the class on each post:** rejected by the SPEC decision (the class follows the
  current snapshot).
- **A SQL view instead of the Python fragments:** the fragments need no extra migration
  object and are tested once in `tests/tweets/test_classes.py`.
- **Taking the quoted author's handle into `current_extractions`/`SearchResult` through a
  join:** one `quoted_authors` lookup in the two callers touches fewer signatures.
- **A paging bound over the list posts:** AC14 asks for the newest timeline post, and the
  `embedded` flag gives it without depending on membership.

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 5, 7 | `tests/tweets/sources/test_twscrape_source.py::test_members_from_recorded_payload`, `tests/tweets/test_loop.py::test_membership_fetched_at_start_and_every_6_hours` |
| AC2 | 7 | `tests/tweets/test_loop.py::test_failed_membership_fetch_keeps_snapshot_and_polling` |
| AC3 | 5, 6, 7 | `tests/tweets/test_classes.py::test_no_snapshot_every_post_is_a_list_post`, `::test_unsupported_snapshot_every_post_is_a_list_post`, `tests/tweets/test_loop.py::test_unsupported_source_records_a_null_snapshot_once` |
| AC4 | 8 | `tests/tweets/test_cli.py::test_members_command_stores_and_prints_snapshot`, `::test_members_command_unsupported_source`, `::test_members_command_failure_exits_1` |
| AC5 | 6 | `tests/tweets/test_classes.py::test_class_follows_the_current_snapshot_both_ways`, `::test_repost_counts_by_its_reposting_member` |
| AC6 | 1, 4, 6 | `tests/tweets/test_classes.py::test_recorded_pages_classified`, `tests/db/test_migrations.py::test_quote_migration_backfills_and_downgrades` |
| AC7 | 2, 3, 9 | `tests/extraction/test_store.py::test_context_post_is_not_pending`, `::test_context_post_becomes_pending_when_its_author_joins`, `::test_context_post_becomes_pending_when_a_list_post_quotes_it`, `tests/extraction/test_loop.py::test_loop_skips_context_posts`, `tests/tweets/test_store.py::test_embedded_then_entry_ends_as_timeline_post`, `::test_entry_then_embedded_stays_timeline_post`, `tests/tweets/sources/test_paging.py::test_duplicate_across_pages_keeps_timeline_copy`, `tests/retrieval/test_indexing.py::test_context_post_is_indexed` |
| AC8 | 10, 12 | `tests/corroboration/test_sources.py::test_context_post_never_a_claim_or_candidate`, `tests/retrieval/test_search.py::test_sources_only_filter_drops_context_posts`, `tests/alerts/test_breaking.py::test_context_reply_parent_never_alerts`, `tests/alerts/test_slots.py::test_context_post_absent_from_digest_and_counts` |
| AC9 | 10, 12 | `tests/alerts/test_breaking.py::test_quoted_off_list_leak_breaks_and_is_cited` |
| AC10 | 11 | `tests/corroboration/test_rules.py::test_supporting_quote_counts_as_quoted_author`, `::test_contradicting_quote_counts_as_its_own_author`, `::test_member_quoting_member_counts_once`, `tests/corroboration/test_service.py::test_quote_and_quoted_leak_give_one_supporting_account` |
| AC11 | 12 | `tests/alerts/test_slots.py::test_quoted_post_included_once_across_slots` |
| AC12 | 13 | `tests/alerts/test_latency.py::test_post_latencies_leave_out_a_quoted_post`, `tests/tweets/test_cli.py::test_measure_records_list_posts_only`, `::test_measure_without_membership_skips_embedded_posts` |
| AC13 | 3, 4 | `tests/tweets/sources/test_paging.py::test_embedded_old_post_does_not_end_paging`, `::test_old_non_head_module_item_does_not_end_paging`, `::test_floor_rule_reads_entry_heads_only`, `::test_duplicate_across_pages_keeps_timeline_copy`, `tests/tweets/sources/test_twscrape_source.py::test_recorded_quote_of_2019_post_pages_to_last_seen` |
| AC14 | 2 | `tests/tweets/test_store.py::test_last_seen_id_ignores_embedded_posts` |
| AC15 | 14 | `tests/test_docs.py::test_backlog_11_closed_and_off_list_entries_added`, `::test_list_replies_report_exists_and_is_linked` |
| AC16 | 14 | `tests/test_docs.py::test_decisions_and_deployment_cover_list_membership` |

## Steps

Every step's command runs from `/home/czarny/Projects/gaffers-presser/backend`, and every
step ends green on `uv run ruff check . && uv run ruff format --check .` as well.

- [x] 1. **Migration `0010` and models (data migration).** Add `tweet.embedded`,
      `tweet.quoted_x_id` (with `ix_tweet_quoted_x_id`) and the `list_membership` table as in
      Design → Schema. Write the backfill and a downgrade that drops all three. Update the
      models in `app/tweets/models.py`. In `tests/db/test_migrations.py`, add
      `test_quote_migration_backfills_and_downgrades` (pattern: the `0006` test). It inserts at
      `0009` a twscrape quote (`raw.quotedTweet.id = 77`), a post without `quotedTweet` and a
      post with a non-numeric `quotedTweet.id`. It upgrades to `0010` and checks
      `quoted_x_id` = 77 / NULL / NULL, `embedded` false for all of them and the other tables
      unchanged. It then downgrades (columns and table gone, rows kept) and upgrades again.
      Add `list_membership` to the tables excluded by the tests that run at older revisions
      (like `ALERT_TABLES` today). Files: `migrations/versions/0010_off_list_posts.py`,
      `app/tweets/models.py`, `tests/db/test_migrations.py`.
      Automatic verification: `uv run pytest -q tests/db/test_migrations.py` `iterations: 2`
- [x] 2. **Fetched-post fields, upsert and paging bound.** Add `embedded`, `entry_head` and
      `quoted_x_id` to `FetchedPost`, and the same keyword arguments to `tests/tweets/fakes.py::post`.
      Add `merge_fetched` to `base.py` (Design → Fetched posts) and use it for the in-call
      duplicates. Turn `store_posts` into the upsert from Design and change `last_seen_id`.
      Write the tests first in `tests/tweets/test_store.py`:
      - `test_last_seen_id_ignores_embedded_posts`
      - `test_embedded_then_entry_ends_as_timeline_post`, where a later fetch fills
        `quoted_x_id` too
      - `test_entry_then_embedded_stays_timeline_post`
      - `test_upsert_counts_only_inserted_rows`
      - `test_duplicate_ids_in_one_call_are_merged`
      - `test_first_fetched_at_never_changes`
      Files: `app/tweets/sources/base.py`, `app/tweets/store.py`, `tests/tweets/fakes.py`,
      `tests/tweets/test_store.py`.
      Automatic verification: `uv run pytest -q tests/tweets/test_store.py tests/tweets/test_ingest.py` `iterations: 0`
- [x] 3. **Paging on entry heads.** Change `collect_new` as in Design. Write the tests first
      in `tests/tweets/sources/test_paging.py`:
      - `test_embedded_old_post_does_not_end_paging`
      - `test_old_non_head_module_item_does_not_end_paging`
      - `test_floor_rule_reads_entry_heads_only`, where a page whose heads are new but whose
        embedded posts are older than the floor keeps paging
      - `test_page_without_heads_continues`
      - `test_duplicate_across_pages_keeps_timeline_copy`: a post embedded (with no
        `quoted_x_id`) on page 1 and an entry head with `quoted_x_id` on page 2 comes out of
        `collect_new` once, `embedded = False`, with the `quoted_x_id`; the same in the
        opposite page order

      Replace the `setdefault` dedupe with `merge_fetched` (added to `base.py` in step 2 and
      used by `store_posts` there). Every existing paging test stays green unchanged. Files:
      `app/tweets/sources/paging.py`, `tests/tweets/sources/test_paging.py`.
      Automatic verification: `uv run pytest -q tests/tweets/sources/test_paging.py tests/tweets/test_ingest.py` `iterations: 0`
- [x] 4. **twscrape entries, embedded posts and quotes.** Add the synthetic fixtures under
      the rules in `tests/tweets/payloads/README.md`, enforced by `tests/tweets/test_payloads.py`,
      and add them to the README and to `test_payload_files_exist`:
      - `twscrape-page-conversation.json.gz` is built from `twscrape-page-1`'s tweet result as
        a template. It holds a single entry by member `synthetic_leaker_1` quoting a post by
        off-list `synthetic_offlist_1` dated 2019, embedded through `quoted_status_result`.
        It also holds a `list-conversation-…` `TimelineTimelineModule` with three items: an
        older post by member `synthetic_leaker_2`, a reply to it by off-list
        `synthetic_offlist_2`, and the member's newer reply back. A single entry by member
        `synthetic_leaker_3` replies to a post by off-list `synthetic_offlist_3`, which is
        shown as an embedded reply parent. A single entry is a repost by member
        `synthetic_leaker_1` of a post by off-list `synthetic_offlist_4`, embedded through
        `retweeted_status_result`. A cursor entry closes the page.
      - `twscrape-page-conversation-2.json.gz` is an older page that holds the post used as
        `since_id`.

      Build them with a one-off script in the scratchpad (not committed), and describe them in
      the README. Implement Design → twscrape adapter. Write the tests first in
      `tests/tweets/sources/test_twscrape_source.py`:
      - `test_conversation_page_marks_entries_heads_and_embedded`, which checks the
        `embedded`, `entry_head` and `quoted_x_id` values per post; the repost is an entry
        head and, if `parse_tweets` returns the reposted original as its own post, that
        original is embedded
      - `test_recorded_quote_of_2019_post_pages_to_last_seen`, which runs `collect_new` over
        both pages with `since_id` on page 2 and expects 2 pages fetched
      - `test_page_without_recognised_entries_falls_back_and_warns`

      `test_page_normalised` stays green. Files: `app/tweets/sources/twscrape_source.py`,
      `tests/tweets/payloads/*`, `tests/tweets/test_payloads.py`,
      `tests/tweets/sources/test_twscrape_source.py`.
      Automatic verification: `uv run pytest -q tests/tweets/sources/test_twscrape_source.py tests/tweets/test_payloads.py` `iterations: 0`
- [x] 5. **Membership through the source, and the snapshot store.**
      - Add `members` to the `TweetSource` protocol and `MembershipNotSupportedError` to
        `base.py`.
      - Implement `TwscrapeSource.members` with the extracted `NoAccountError` helper, and
        `members` raising "not supported" in `twitterapi_io` and `x_api`.
      - Add `members` to the fake and scripted sources in `tests/tweets/fakes.py`,
        `tests/tweets/test_loop.py`, `tests/tweets/test_cli.py` and the worker tests that
        build sources. Each returns a configurable list and defaults to "not supported".
      - Add the fixture `twscrape-list-members.json.gz`: synthetic, shaped as a `ListMembers`
        response (`data.list.members_timeline.timeline.instructions[*].entries[*]` with
        `user-<id>` `TimelineUser` entries and a cursor), with 3 members `Synthetic_Leaker_1..3`
        (mixed case).
      - Create `app/tweets/membership.py` as in Design.

      Tests, written first:
      - `tests/tweets/sources/test_twscrape_source.py`:
        - `test_members_from_recorded_payload`
        - `test_members_rate_limited_maps_to_source_error`, on the `ListMembers` queue
        - `test_members_malformed_or_empty_raises_payload_error`
      - `tests/tweets/sources/test_twitterapi_io.py::test_members_not_supported` and
        `tests/tweets/sources/test_x_api.py::test_members_not_supported`
      - `tests/tweets/test_membership.py`:
        - `test_fetch_lowercases_handles`
        - `test_unsupported_gives_null_handles`
        - `test_save_and_latest_snapshot`
        - `test_errors_propagate_from_fetch`

      Files: `app/tweets/sources/{base,twscrape_source,twitterapi_io,x_api}.py`,
      `app/tweets/membership.py`, the test files above, `tests/tweets/payloads/*`.
      Automatic verification: `uv run pytest -q tests/tweets/sources tests/tweets/test_membership.py tests/tweets/test_payloads.py` `iterations: 1`
- [x] 6. **Classification.** Create `app/tweets/classes.py` as in Design. Write the tests
      first in `tests/tweets/test_classes.py`:
      - `test_no_snapshot_every_post_is_a_list_post`
      - `test_unsupported_snapshot_every_post_is_a_list_post`
      - `test_class_follows_the_current_snapshot_both_ways`: adding the author makes an
        earlier context post a list post, and removing them makes it context again with no
        row rewritten
      - `test_repost_counts_by_its_reposting_member`
      - `test_quote_by_context_post_does_not_make_a_quoted_post`
      - `test_handles_compared_case_insensitively`
      - `test_recorded_pages_classified`: stores both conversation pages through
        `TwscrapeSource` + `store_posts` with a snapshot of `synthetic_leaker_1..3`. It checks
        that the quote is `list`, the 2019 post `quoted`, the module's off-list reply
        `context`, the embedded reply parent `context`, the member's repost `list` (and a
        stored reposted original by `synthetic_offlist_4` `context`), and that the 2019 post is classified
        the same when its quote relation comes from a backfilled row (set `quoted_x_id`
        directly).
      - `test_quoted_authors`

      Files: `app/tweets/classes.py`, `tests/tweets/test_classes.py`.
      Automatic verification: `uv run pytest -q tests/tweets/test_classes.py` `iterations: 1`
- [x] 7. **Membership refresh in the poller.** Add the refresh to `TweetPoller.run` as in
      Design. Write the tests first in `tests/tweets/test_loop.py`, with a fake clock:
      - `test_membership_fetched_at_start_and_every_6_hours`, which uses a `TwscrapeSource`
        over a `FakeApi` serving the recorded `list_members` payload, then checks the
        snapshot rows: one at start, none at +5 h 59 min, a second at +6 h
      - `test_failed_membership_fetch_keeps_snapshot_and_polling`, where a source whose
        `members` raises `SourceRateLimitedError` still polls on schedule, the previous
        snapshot stays the latest, the warning names the error class and a retry follows
        after 30 min
      - `test_unsupported_source_records_a_null_snapshot_once`

      Files: `app/tweets/loop.py`, `tests/tweets/test_loop.py`.
      Automatic verification: `uv run pytest -q tests/tweets/test_loop.py tests/worker/test_cli.py` `iterations: 1`
- [x] 8. **CLI `python -m app.tweets members`.** Add the command and `MeasureDeps.make_engine`.
      Add a line for `members` in README → Development next to `measure`/`summary`. Write the
      tests first in `tests/tweets/test_cli.py`:
      - `test_members_command_stores_and_prints_snapshot`
      - `test_members_command_unsupported_source`
      - `test_members_command_failure_exits_1`
      - `test_members_command_needs_tweet_source_and_list_id`

      Files: `app/tweets/cli.py`, `tests/tweets/test_cli.py`, `README.md`.
      Automatic verification: `uv run pytest -q tests/tweets/test_cli.py tests/test_readme.py` `iterations: 1`
- [x] 9. **Extraction reads source posts only.** Add `source_post_sql` to `next_pending` and
      to `extraction_status().waiting`. Write the tests first:
      - `tests/extraction/test_store.py`:
        - `test_context_post_is_not_pending` (neither `next_pending` nor `waiting`)
        - `test_context_post_becomes_pending_when_its_author_joins`
        - `test_context_post_becomes_pending_when_a_list_post_quotes_it`
        - `test_reextract_still_selects_a_context_post`
      - `tests/extraction/test_loop.py::test_loop_skips_context_posts`
      - `tests/retrieval/test_indexing.py::test_context_post_is_indexed`

      Extend `tests/retrieval/helpers.py::add_tweet` and `tests/corroboration/helpers.py::add_claim`
      with `quoted_x_id` and `embedded` keyword arguments. Add a helper
      `set_members(engine, handles)` in `tests/tweets/membership_helpers.py` (wraps
      `save_snapshot`). Files: `app/extraction/store.py`, the tests and helpers above.
      Automatic verification: `uv run pytest -q tests/extraction tests/retrieval/test_indexing.py` `iterations: 1`
- [x] 10. **Corroboration sources: no context posts, quoted authors carried.** Add
      `sources_only` to `current_extractions` and `SearchFilters` (and `_filters_for_trace`).
      Pass `True` in `corroboration/sources.py`, add `PostRef.quoted_author_handle` and fill it
      from `quoted_authors` in `sql_claims` and `retrieval_candidates`. Write the tests first:
      - `tests/corroboration/test_sources.py::test_context_post_never_a_claim_or_candidate`.
        An off-list reply parent naming Saka has an `out` extraction recorded through
        `add_extraction`, as the re-extraction CLI would record it. It is neither in
        `sql_claims` nor in `retrieval_candidates`.
      - `tests/corroboration/test_sources.py::test_quote_carries_quoted_author`
      - `tests/retrieval/test_search.py::test_sources_only_filter_drops_context_posts`
      - `tests/extraction/test_current_extractions.py::test_sources_only_drops_context_posts`

      Files: `app/extraction/store.py`, `app/retrieval/search.py`,
      `app/corroboration/{schemas,sources}.py`, the tests above.
      Automatic verification: `uv run pytest -q tests/corroboration tests/retrieval/test_search.py tests/extraction/test_current_extractions.py` `iterations: 1`
- [x] 11. **Quote counting rule.** Add `counted_account` and use it in `count_accounts`,
      `grade` and the trace span (`service.py`). Write the tests first:
      - `tests/corroboration/test_rules.py`:
        - `test_supporting_quote_counts_as_quoted_author`
        - `test_related_quote_counts_as_quoted_author`
        - `test_contradicting_quote_counts_as_its_own_author`
        - `test_member_quoting_member_counts_once`
        - `test_quote_of_the_anchor_author_never_supports`
        - `test_quote_without_stored_quoted_post_counts_as_its_author`
      - `tests/corroboration/test_service.py`:
        - `test_quote_and_quoted_leak_give_one_supporting_account`: the anchor is a third
          member's post, a member quotes an off-list leak, and both claims support. That
          gives one supporting account.
        - `test_contradicting_quote_gives_one_contradicting_account`, which is the quoting
          member

      Files: `app/corroboration/{rules,service}.py`, the tests above.
      Automatic verification: `uv run pytest -q tests/corroboration` `iterations: 0`
- [ ] 12. **Alerts: source posts only, quoted posts as sources.** Pass `sources_only=True` in
      `alerts/players.py::_claims` and `alerts/breaking.py::_candidates`. Fill
      `quoted_author_handle` in `_claims` and count trending by
      `counted_account(post, "supports")`. Write the tests first:
      - `tests/alerts/test_breaking.py`:
        - `test_context_reply_parent_never_alerts`, with an `out` extraction on an off-list
          reply parent and a snapshot without its author
        - `test_quoted_off_list_leak_breaks_and_is_cited`: a member's quote "confirmed 👇"
          has an extraction without events and quotes an off-list `out` leak about a listed
          player. One breaking alert is sent, with trigger = the quoted post and the quoted
          post's URL in the message.
      - `tests/alerts/test_slots.py`:
        - `test_context_post_absent_from_digest_and_counts`
        - `test_quoted_post_included_once_across_slots`: the quoted post is in the digest's
          `alert_post` rows as `new`, and the next news slot does not offer it as new and is
          skipped
        - `test_trending_counts_quote_and_quoted_as_one_account`

      Files: `app/alerts/{players,breaking}.py`, the tests above.
      Automatic verification: `uv run pytest -q tests/alerts`
- [ ] 13. **Latency reports count list posts only.** Add `list_post_sql('t')` to
      `post_latencies`. Filter `_measure_one` as in Design. Write the tests first:
      - `tests/alerts/test_latency.py::test_post_latencies_leave_out_a_quoted_post`: a sent
        alert holds a list post and an off-list quoted post created a day earlier, both
        `new`. The report holds the list post only.
      - `tests/tweets/test_cli.py`:
        - `test_measure_records_list_posts_only`: an off-list conversation reply created after
          the start is left out
        - `test_measure_without_membership_skips_embedded_posts`
        - `test_measure_since_id_ignores_embedded_posts`
        - `test_summary_reads_only_what_measure_wrote`

      Files: `app/alerts/latency.py`, `app/tweets/cli.py`, the tests above.
      Automatic verification: `uv run pytest -q tests/alerts/test_latency.py tests/alerts/test_cli.py tests/tweets/test_cli.py`
- [ ] 14. **Documents.**
      - Write `docs/reports/twscrape-list-replies-2026-10.md`: the two posts (x_id
        2104662407054266709 and 2104663688330486065), their reply target `@FPL_TomHadley`
        (off-list, in a conversation another account started), the conversation-module
        observation (`@Teamnewsandtix`), the conclusion that leaks are top-level posts or the
        author's own threads, which the List keeps, and the trigger to revisit (a GW6 leak
        found only as a member's reply in someone else's conversation).
      - Link the new report from `docs/reports/tweet-source-latency-2026-09.md`.
      - BACKLOG: remove row 11. Add a P3 row for membership in `twitterapi_io`/`x_api` (trigger:
        switching the configured source, BACKLOG #2). Add a P2 row for giving extraction and
        the judge the quoted post's text (trigger: an alert misses or misreads a quote whose
        own text names no player).
      - DECISIONS: one row dated 2026-10-07 covering list membership as the basis of "list
        post" (latest snapshot, query time, fallback to all), the three classes, the quote
        counting rule (also in trending) and paging on entry heads (spec 010).
      - DEPLOYMENT: under step 7, note migration `0010` (columns, backfill, new table, clean
        downgrade), the membership fetch at the worker's start and every 6 h, and
        `python -m app.tweets members`.
      - ROADMAP: add a ticked Stage 2 item for spec 010 and note in the alert item that
        BACKLOG #11 is settled by spec 010.

      Tests first in `tests/test_docs.py`:
      - `test_backlog_11_closed_and_off_list_entries_added` (priority and trigger present)
      - `test_list_replies_report_exists_and_is_linked`
      - `test_decisions_and_deployment_cover_list_membership` (`0010` and `app.tweets
        members` in DEPLOYMENT, a DECISIONS row naming `list_membership` and the quote rule)

      Files: the documents above, `tests/test_docs.py`.
      Automatic verification: `uv run pytest -q tests/test_docs.py tests/test_readme.py`

## Risks and traps

- **twscrape page shape:** the entry parsing is tested only against synthetic fixtures
  shaped on the X GraphQL schema (`TimelineTimelineItem`/`TimelineTimelineModule`,
  `tweet_results.result[.tweet].rest_id`). Step 4's fallback (no recognised entry, so
  behaviour as today plus a warning) keeps a schema surprise from causing 50-page polls. The
  owner's manual check confirms the live shape.
- **An empty or wrong snapshot silences alerts:** an empty member list raises
  `SourcePayloadError` and the previous snapshot stays. Do not "simplify" that away.
- **Handle changes:** classification compares handles, as the SPEC requires. A member who
  renames drops out until the next snapshot (at most 6 h) and then counts again for every
  post. Their posts under the old handle stay off-list. This is accepted, because the stored
  `raw` keeps user IDs if it ever matters.
- **Upsert pitfalls:** `ON CONFLICT DO UPDATE` refuses two rows with one key in one statement,
  so merge them first. The new-post count must come from `xmax = 0`, not from the returned row
  count. The `WHERE` on the update keeps unchanged rows from being rewritten.
- **The SQL fragments** are formatted into `text()` queries. Only the code-constant aliases go
  in, never input. The inner alias `quoting` must differ from every outer alias.
- **Migration tests:** the tests that run at older revisions compare all tables of the
  metadata and must exclude `list_membership`. `test_models_match_migration` needs the model's
  index and types to match `0010` exactly.
- **Fake sources:** every fake or scripted source used with the poller or the CLI needs
  `members`. A missing one shows up as a warning, not a failure, so search the tests for
  `def pages(` to find them all.
- **Existing tests keep passing without a snapshot:** no snapshot means every post is a list
  post. Any test that starts failing after steps 9–13 points to a wrong fragment, not to a test
  that needs a snapshot.
- **Reposted originals:** `parse_tweets` may return a reposted original as its own post. It
  is in no entry, so it is stored as embedded and, by an off-list author, is a context post.
  The repost itself stays a list post, is extracted and counts as the original author, so
  the news still reaches alerts once.
- **Time zones:** `fetched_at` is timezone-aware UTC (`utc_column`), and the CLI prints UTC like
  `app.worker status`.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

- `cd /home/czarny/Projects/gaffers-presser/backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
  is fully green.
- `cd /home/czarny/Projects/gaffers-presser/backend && uv run python -m app.tweets --help` lists
  `members`. `uv run python -m app.tweets members` with `TWEET_SOURCE` unset exits 1 with an
  error naming `TWEET_SOURCE`.
- The migration chain on a fresh container is covered by `tests/db/test_migrations.py`:
  upgrade, downgrade and upgrade, the `0010` backfill, and the models matching the migration.

### Manual (performed by the owner)

- On the local development database (with the twscrape credentials in `backend/.env`), run
  `cd backend && uv run alembic upgrade head && uv run python -m app.tweets members`. Then run
  this query:
  `SELECT t.author_handle, t.x_id, CASE WHEN m.h IS NULL OR lower(t.author_handle) = ANY(m.h) THEN 'list' WHEN EXISTS (SELECT 1 FROM tweet q WHERE q.quoted_x_id = t.x_id AND (m.h IS NULL OR lower(q.author_handle) = ANY(m.h))) THEN 'quoted' ELSE 'context' END AS class FROM tweet t, (SELECT handles AS h FROM list_membership ORDER BY fetched_at DESC, id DESC LIMIT 1) m WHERE NOT (m.h IS NULL OR lower(t.author_handle) = ANY(m.h)) ORDER BY class, t.author_handle;`
  Pass when: the command prints `List members: <the List's member count>`, the query
  returns the 22 off-list posts (plus any fetched since), every row is `quoted` or `context`,
  and the `@afcstuff` Tzolis post is `quoted`.
- With the same `.env`, run `cd backend && uv run python -m app.worker run` until the tweet
  poller has polled once (a minute is enough), stop it with Ctrl-C, and run
  `SELECT count(*) FILTER (WHERE embedded) AS embedded, count(*) FILTER (WHERE NOT embedded) AS timeline FROM tweet WHERE first_fetched_at > now() - interval '10 minutes';`
  Pass when: the worker log has no `twscrape: no timeline entries recognised on a page`
  warning and no `list membership fetch failed` warning, and, when the poll stored new
  posts, `timeline` is at least 1 (the live page shape matches the parsing of step 4).

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, `docs/DEPLOYMENT.md`, `docs/BACKLOG.md`,
      the new report and `README.md` updated
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

## Review log

2026-10-07 — plan review (fresh eye, `/pipeline:ship`)

- `major` — Design and step 3: `collect_new` dedupes with `collected.setdefault(...)`, so a post embedded on a newer page and shown as an entry on an older page of the same catch-up reached `store_posts` only as its embedded copy (wrong `embedded`, possibly no `quoted_x_id`); the plan's "whichever order" claim for AC7/AC14 held only across separate polls. Added the shared `merge_fetched` helper in `base.py` (step 2), used by both `store_posts` and `collect_new` (step 3), and the proving test `test_duplicate_across_pages_keeps_timeline_copy`; matrix rows AC7 and AC13 updated.
- `minor` — Step 13: `_measure_one` keeps its own `since_id` as the maximum over every collected post, embedded ones included, which contradicts AC14's bound. Design now takes the maximum over non-embedded posts; added `test_measure_since_id_ignores_embedded_posts`.
- `minor` — Step 4/6: the conversation fixture had no repost, although a reposted original that `parse_tweets` returns as its own post becomes an embedded context post under the new parsing. Added a member repost of an off-list post to the fixture, assertions in `test_conversation_page_marks_entries_heads_and_embedded` and `test_recorded_pages_classified`, and a risk entry.
- `minor` — Manual verification: the risks say the owner's manual check confirms the live page shape, but no manual item checked it. Added a second manual item (one worker poll, no fallback warning, timeline rows stored) with a `Pass when:` line, and mentioned it in the Owner summary.

Checked and found correct:

- Coverage: AC1–AC16 each have steps and a named proving test; the matrix matches the steps; every step has `Automatic verification:` with exact test paths and writes its tests first.
- Compliance: DECISIONS rows 2026-09-28 (relevance rule, reposts attributed to the list account), 2026-09-29 (every post indexed — kept, indexing unchanged), 2026-09-30 (independent accounts, anchor account never counts — `counted_account(anchor, "supports")` and `test_quote_of_the_anchor_author_never_supports`), 2026-10-02 (alert log, included posts — no change needed) and 2026-10-07 (catch-up) are respected; CONVENTIONS: synthetic payloads under the payload README rules, PostgreSQL container tests, no user-facing Polish text added.
- Every query that feeds alerts or corroboration was checked against the code: `current_extractions` callers (`alerts/players.py`, `alerts/breaking.py`, `corroboration/sources.py`; evaluation tooling deliberately keeps the default), `SearchFilters` in `corroboration/sources.py`, `next_pending` and `extraction_status` (the extraction loop selects only through `next_pending`, so failed context posts are not retried), `post_latencies`.
- Feasibility: step order has no forward dependencies (fixtures in 4 and 5 before the classification test in 6 and the poller test in 7; `save_snapshot` in 5 before the helpers in 9); `MAX_SLEEP` is 60 s, so the 6-hour refresh in `TweetPoller.run` fires on time without changing the sleep computation; the uncorrelated `_LATEST` subquery is evaluated once per query; the upsert pitfalls (`xmax = 0`, in-statement duplicates) are covered.
- Data migration `0010` with backfill is accepted in SPEC → Owner decisions for the development and test databases; no new dependency (twscrape 0.20.1 already in the lock). The Owner summary flags both correctly.
- Language: the plan is in English, as `language: en` requires.
- Approval: the plan is ready — every finding was fixed in the plan itself, the migration is accepted by the owner and no SPEC gap was found.

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

- `minor` — Step 4 also generated the `twscrape-list-members.json.gz` fixture (planned for step 5), because the payload-count test and README are updated once for all three new fixtures.
- `minor` — The reply-parent post in `twscrape-page-conversation` is nested under the tweet result as `in_reply_to_status_result`, a placement made up for the fixture (X does not nest reply parents that way; they come as module items). It is documented in the payloads README.

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_
