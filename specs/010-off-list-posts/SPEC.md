---
status: implemented
stage_history:
  - "spec-draft — 2026-10-07"
  - "spec-ready — 2026-10-07"
  - "plan-draft — 2026-10-07"
  - "plan-approved — 2026-10-07"
  - "implemented — 2026-10-07"
metrics:
  started_at: 2026-10-07T12:11
  plan_steps: 14
  plan_changes: 4
  escalations: 0
  escalations_permission: 0
  escalations_tooling: 0
  plan_review_blockers: 0
  plan_review_majors: 1
  implement_steps: 14
  implement_iterations: 8
  deviations_minor: 3
  deviations_major: 0
  final_review_blockers: 0
  final_review_worth_fixing: 6
  final_review_nits: 5
---

# SPEC 010 — Off-list posts: list membership, quotes and conversation context

## Goal

The twscrape adapter stores every tweet in a List timeline response as an ordinary list
post: posts quoted by list members, the parents of their replies and other accounts' posts
inside a conversation. Those posts count as independent accounts in corroboration and drive
alerts like posts written by the curated list. This spec makes the system know which
posts come from list members. A post quoted by a list member keeps feeding alerts, because
new leakers reach us that way, but it is counted together with the quote. Conversation
context from outside the list is stored but not extracted and never reaches alerts. The same
work fixes a catch-up that ends early and closes BACKLOG #11.

Success: a quote of an off-list leak still produces an alert; the quote and the quoted post
count as one account; an off-list reply parent never appears in an alert or a count; a
catch-up after downtime is not ended by an old quoted post.

## Context

- The finding (2026-10-07, while investigating BACKLOG #11): `TwscrapeSource.pages`
  (`backend/app/tweets/sources/twscrape_source.py`) maps every tweet returned by
  `twscrape.parse_tweets`, which includes embedded tweets (quoted posts and reply parents),
  not only the timeline's own entries. In the local database, 22 posts by 20 off-list
  accounts were stored this way (e.g. `@goal` from 2019, `@PolymarketSport`), all 22 were
  extracted and one (`@afcstuff` on Tzolis, quoted by a list member) reached an alert.
- A raw List timeline page (fetched 2026-10-07) has two kinds of entries: single
  `tweet-<id>` entries and `list-conversation-…` modules. A module holds several items: a
  member's thread, or a member's post, an off-list account's reply to it
  (`@Teamnewsandtix`) and the member's reply back. The response structure alone therefore
  cannot tell members from non-members. twscrape provides `list_members` / `list_members_raw`.
- BACKLOG #11: the two `@FPL_Harry` posts twscrape missed during the latency measurement
  (x_id 2104662407054266709 and 2104663688330486065, `docs/reports/tweet-source-latency-2026-09.md`)
  were fetched with `tweet_details`: both are replies to `@FPL_TomHadley`, an account not on
  the list, in a conversation another account started. They are advice on defender picks
  with no news. All 8 replies stored locally are to list members or self-threads, and the
  module above shows that a member's reply to an off-list account *is* listed inside a
  conversation the member started. So the List timeline leaves out a member's replies in
  other people's conversations with non-members. Leaks are posted as top-level posts or the
  author's own threads, which the List keeps.
- Paging (`backend/app/tweets/sources/paging.py`, `collect_new`, spec 002 catch-up from
  #17) ends at the first page that holds any post with an ID at or below the newest stored
  ID. An embedded quoted post or an earlier post of a conversation is older than the
  newest stored post, so it can end a catch-up on its first page.
- Extraction (`backend/app/extraction/flow.py`, `_render_post`) and the corroboration judge
  (`backend/app/corroboration/judge.py`) read only the post's own text, not the quoted one.
  A quote like "confirmed 👇" therefore yields no event, and the news reaches alerts only
  through the stored quoted post. That is why quoted posts must stay sources.
- Corroboration counts independent accounts by `account_of` → `PostRef.original_author`
  (`backend/app/corroboration/rules.py`, `schemas.py`): a repost counts as its original
  author. Alert candidates and breaking alerts come from `current_extractions`
  (`backend/app/extraction/store.py`) and hybrid search (`backend/app/retrieval/search.py`).
  The detection and post → inbox latency reports read `tweet.created_at` and
  `first_fetched_at` (`backend/app/tweets/measure.py`, `backend/app/alerts/latency.py`).
- The name "origin" is already used for repost grouping (`backend/app/tweets/reposts.py`,
  `origin_ids`; `post_origin_sets` in `backend/app/alerts/store.py`).

## Read context

- `docs/ROADMAP.md` — read in full: no item for this work; it precedes the production
  deployment (Stage 0) together with BACKLOG #11, which the Stage 2 alert item says is
  settled after spec 009 and before the deployment. It feeds the GW6 data for BACKLOG #12.
- `docs/PROJECT.md` — searched for "quote", "reply", "repost", "list", "independent",
  "credib", "corrobor": FR-1.1 (a configured list of accounts), FR-1.4 (keep every post and
  extraction — for scoring sources), FR-2.2 (independent accounts), FR-4.1 (per-account
  accuracy, Stage 4).
- `docs/DECISIONS.md` — read in full: 2026-09-28 tweet ingest of one X List; 2026-09-28
  relevance rule (reposts attributed to the list account in extraction); 2026-09-30
  corroboration (independent accounts are original authors, a repost counts as its original
  author); 2026-09-30 `reposted_author_handle`; 2026-09-30 anchor account never counts;
  2026-10-02 alert log (a repost counts as its original, `app/tweets/reposts.py`);
  2026-10-07 tweet ingest catch-up. This spec extends the independence rule from reposts to
  quotes and adds list membership. It supersedes nothing.
- `docs/BACKLOG.md` — read in full: #11 is closed by this spec; #19 (shared cited source,
  aggregators) is related but stays separate; #2 (official X API) is the trigger for
  membership in the other adapters; #27 (resumable catch-up) is untouched.
- `docs/adr/` — searched for "list", "source": ADR 0003 (swappable source behind
  `TweetSource`), ADR 0005 (twscrape is the configured source).
- `docs/CONVENTIONS.md` — searched for "test", "migrat": tests against recorded payloads,
  PostgreSQL in a container, tests with the implementation.
- `docs/DEPLOYMENT.md` — searched for "migrat", "twscrape", "X_LIST": each migration gets a
  runbook note (as `0006`); the twscrape variables are unchanged.

## Scope

- List membership: the worker fetches the watched List's members through the configured
  source and keeps the latest successful snapshot.
- Classification of every stored post, existing and new, by the current membership
  snapshot and by its relation to list posts: **list post** (author is a current member),
  **quoted post** (off-list author, quoted by at least one list post), **context post**
  (any other off-list post: reply parents, other accounts' posts inside a conversation).
- Extraction of list posts and quoted posts only; context posts are stored and indexed.
- Corroboration and alerts: list posts and quoted posts are sources; context posts are
  not. A quote counts as the account of the quoted post unless it contradicts.
- The twscrape adapter reports which posts are the timeline's own entries and which are
  embedded context, and which post each quote quotes. Paging decisions use only the
  timeline's own posts.
- Latency reports count list posts only.
- A report closing BACKLOG #11, a DECISIONS entry and BACKLOG updates.

## Out of scope

- List membership in the `twitterapi_io` and `x_api` adapters: they report that membership
  is not supported, and all their posts count as list posts, as today → BACKLOG (P3,
  trigger: switching the configured source, BACKLOG #2).
- Giving extraction and the judge the quoted post's text alongside the quote → BACKLOG
  (P2, trigger: an alert misses or misreads a quote whose own text has no player name).
- Fetching the replies the List timeline leaves out (BACKLOG #11 finding) — accepted, with
  the trigger in the report: a GW6 leak found only as a member's reply in someone else's
  conversation.
- A report of off-list accounts that are candidates for the list (Stage 4 credibility).
- Any label in alert e-mails marking an off-list post (the owner: not needed).
- Shared cited sources and aggregators (BACKLOG #19); a resumable catch-up (BACKLOG #27).
- Deleting stored posts or their earlier extractions (the 22 existing off-list posts keep
  theirs).
- Extracting context posts: the re-extraction CLI covers it if Stage 4 credibility needs
  their history (the owner).

## Requirements and acceptance criteria

**List membership**

- [ ] AC1: The worker fetches the watched List's members when it starts and then every
  6 hours (a fixed interval, no new environment variable), and stores the
  snapshot (the members' handles, case-insensitive, and the time it was fetched) in the
  database. A test with a recorded `list_members` payload shows the stored snapshot.
- [ ] AC2: A failed or rate-limited membership fetch keeps the previous snapshot, logs a
  warning with the error class, and never stops tweet polling, extraction or alerts. A
  test with a failing fake source shows the old snapshot still in use and the poller
  still running.
- [ ] AC3: With no snapshot yet, or with a source that does not support membership
  (`twitterapi_io`, `x_api`), every stored post counts as a list post, the behaviour before
  this spec (the fallback errs toward keeping alerts). A test covers both
  cases.
- [ ] AC4: A CLI command fetches the membership on demand and prints the number of
  members and the time of the snapshot; a test runs it against a fake source.

**Classification**

- [ ] AC5: A stored post is a list post when its author (for a repost, the list account
  that reposted it) is in the latest membership snapshot; the classification follows the
  current snapshot at the moment of the query, so adding an account makes its earlier
  posts list posts and removing one makes them off-list, without rewriting stored data. A
  test changes the snapshot and shows both directions.
- [ ] AC6: An off-list post is a quoted post when at least one stored list post quotes it;
  every other off-list post is a context post. The quote relation for posts stored before
  this spec is backfilled from the stored `raw` payload, so the 22 existing off-list posts
  are classified like new ones (`quotedTweet` in twscrape's payload). A test with recorded
  pages covers a quote, a reply parent, an off-list reply inside a member's conversation
  module, and an old quoted post.
- [ ] AC7: Every post, of every class, is still stored with its `raw` payload and indexed
  for retrieval (FR-1.4). The extraction loop extracts list posts and quoted posts and
  skips context posts, and the extraction status does not count a context post as pending.
  A context post that later becomes a list post or a quoted post (its author joins the
  list, or a list post quotes it) is extracted by the loop like any never-extracted post.
  The re-extraction CLI can still extract any post on demand. The same post fetched first
  as embedded context and later as a timeline entry ends up with the relation of a
  timeline post. Tests: a context post is not extracted and not pending; after the
  snapshot adds its author it is extracted; both fetch orders.

**Corroboration and alerts**

- [ ] AC8: Context posts are never anchors, supporting, contradicting or related posts in
  corroboration (neither from extracted claims nor from retrieval), never trigger a
  breaking alert and never appear in an alert e-mail. A test with an off-list reply
  parent that names a player (with an `out` extraction recorded through the re-extraction
  CLI) shows no alert and no count.
- [ ] AC9: A quoted post is a source like a list post: it can be the anchor, it counts as
  its own author's account and it can trigger a breaking alert. A test with a list post
  quoting an off-list `out` leak (and the quote's own text naming no player) produces an
  alert citing the quoted post.
- [ ] AC10: A quote whose label against the anchor is `supports` or `related` counts as
  the account of the quoted post's author, like a repost counts as its original author;
  a quote labelled `contradicts` counts as its own author. This holds whether or not the
  quoted author is on the list. Tests: a member quoting an off-list leak with a supporting
  claim gives one supporting account, not two; a member quoting it with a contradicting
  claim gives one contradicting account (the quoting member); a member quoting another
  member with a supporting claim gives one account.
- [ ] AC11: "New since the previous alert" and the included-post bookkeeping treat a
  quoted post like any other source post; a quoted post already included in an alert is
  not offered as new again. A test covers a quoted post across two alert slots.
- [ ] AC12: The alert detection and post → inbox latency reports, and `app.tweets
  summary`, count list posts only (an embedded post's `created_at` says nothing about
  detection). A test with a quoted post from a day earlier shows it left
  out of the latency figures.

**Paging**

- [ ] AC13: Only the timeline's own posts end paging: an embedded quoted post, a reply
  parent or an earlier post shown inside a conversation module with an ID at or below the
  newest stored ID does not end a catch-up, and does not count toward the "whole page
  older than the window start" rule. A test with a recorded first page holding a new post
  that quotes a 2019 post shows paging continuing to the page that holds the newest
  stored post.
- [ ] AC14: The newest stored post used as the paging bound is the newest timeline post,
  so an embedded post never moves it (today it is the maximum ID over all
  stored posts, which only an embedded post newer than every timeline post could move).

**Documents**

- [ ] AC15: `docs/reports/twscrape-list-replies-2026-10.md` records the BACKLOG #11
  investigation (the two posts, their reply targets, the conversation-module observation,
  the conclusion that leaks are not affected, the trigger to revisit); the latency report
  links to it; BACKLOG #11 is removed and the two out-of-scope items are added with
  priority and trigger.
- [ ] AC16: `docs/DECISIONS.md` gets a row for list membership as the basis of "list post",
  the three classes and the quote counting rule; `docs/DEPLOYMENT.md` notes the migration
  and the membership fetch on the first start.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| "List post" means an author in the List's current membership, fetched with `list_members` | the timeline response structure (entries vs embedded tweets) | conversation modules list off-list accounts' replies as items; only membership gives the truth (the owner) |
| Classification follows the current membership snapshot at query time | freezing it on each post when it is stored | adding a leaker to the list counts their earlier posts at once; removing an unreliable account stops it from affecting alerts without rewriting data (the owner) |
| Quoted off-list posts stay sources for alerts | dropping them; storing them as context only | new leakers reach the list through quotes, and extraction cannot see the quoted text from the quote (the owner) |
| A quote counts as the quoted post's account unless it contradicts | counting quote and quoted separately; merging only off-list quotes | joining a leak is not independent confirmation, just as a repost is not; a dunk must still count against the leak (the owner) |
| Off-list reply parents and conversation items are context: stored and indexed, not extracted, never in corroboration or alerts | treating them like quotes; not storing them; extracting them now for Stage 4 | a reply says nothing about agreement, so a fake leak answered with "bullshit" would reach alerts; the stored `raw` and the re-extraction CLI keep Stage 4 open without paying for extractions nobody reads (the owner) |
| Membership only in the twscrape adapter | all three adapters | the others are not configured and twitterapi.io has no credits, so they could not be tested live (the owner) |
| No label for off-list posts in alert e-mails | "(spoza listy, cytuje @X)" | the owner: not needed |
| The paging fix is part of this spec | a separate fast-path PR | the same cause, and the catch-up matters for the production deployment (the owner) |

## Owner decisions

- Data migration — accepted up front: schema changes for the membership snapshot and the
  quote relation, with a backfill of the existing posts from `raw`, on the development and
  test databases; the production database is migrated by the owner at deployment.
- New dependency — none (twscrape already provides `list_members`).

## Open questions (non-blocking)

- none
