---
status: plan-approved
stage_history:
  - "spec-draft — 2026-09-30"
  - "spec-ready — 2026-09-30"
  - "plan-draft — 2026-09-30"
  - "plan-approved — 2026-09-30"
metrics:
  started_at: 2026-09-30T14:45
  escalations: 0
  plan_steps: 19
  plan_review_blockers: 0
  plan_review_majors: 1
  plan_changes: 6
---

# SPEC 007 — Leak corroboration

## Goal

When an alert is about to mention a player, it must say what the **latest** news about that
player is and how well it is backed. Corroboration answers that question for one player at one
moment. It takes the player's newest claim since the last deadline as the **anchor**. Every
other post about the player in the window is labelled as supporting, contradicting or related
to the anchor. The result counts the independent accounts on each side, flags a reversal, and
grades the anchor's strength. Posts that extraction linked to the player are labelled by
a fixed rule. Posts that only retrieval finds are labelled by an LLM judge, which has its own
evaluation set.

The feature works when:
- the `corroborate` CLI shows this result for any player at any past moment;
- the judge's evaluation runner reports its metrics on set v1;
- the shared code it needs (the chat model, retries, the current extraction, deadlines) has
  one implementation.

## Context

- **Stage 1 and spec 006 are done.**
  - Every stored post passes through extraction (`app/extraction/`, specs 004/005), which
    writes `extraction_event` rows (player FPL ID, event type, certainty). The latest
    successful extraction of a post is its current one.
  - Hybrid retrieval over posts is `app.retrieval.search` (spec 006), with time and repost
    filters.
- **Extraction misses mentions.** In the development database (210 posts, 25 events), 7 posts
  mention Isak but only 3 carry an Isak event. Among the misses is a clear withdrawal-with-
  injury post by `@BenDinnery`. Gakpo has 6 mentions and 1 event, Palmer 4 and 1. This gap is
  what the judge exists for. It re-reads only the few retrieved posts that have no event for
  the player, and it asks a narrow question about one known player.
- **The consumer is the alert spec (Stage 2, next items).** The owner described the schedule
  it will build:
  - a full digest at T-2h covering the whole window since the last deadline;
  - news only at T-30 min and T-10 min, including new posts about players already covered;
  - "breaking" alerts after T-10 up to the deadline.

  Corroboration therefore takes a player, an as-of moment and a "new since" moment, and it
  is not tied to one triggering post.
- **Reposts.** Extraction attributes a repost to the list account that reposted it
  (DECISIONS 2026-09-28). Only `is_repost` is stored. The original author is available in each
  source's payload:
  - twscrape: `retweetedTweet`;
  - twitterapi.io: `retweeted_tweet`;
  - the X API: `referenced_tweets` with `retweeted`.
- **Duplicated code this feature would add a third or fourth copy of:**
  - an OpenRouter chat-model builder in `app/extraction/providers.py` and in
    `app/retrieval/evaluation/llm.py` (each with its own SDK retry workaround);
    `model_settings.toml` lives in `app/extraction/`;
  - retries with back-off in `app/extraction/service.run_with_retries` and in
    `app/retrieval/indexing.with_retries`;
  - the "current extraction per post" SQL (`DISTINCT ON … finished_at DESC, id DESC`), twice in
    `app/extraction/store.py` and once in `app/retrieval/evaluation/queries.py`;
  - deadline helpers in `app/tweets/schedule.py`, `app/tweets/store.py` and
    `app/worker/schedule.py`;
  - the query-embedding timeout, fixed at 30 s in `OpenRouterEmbedder` (BACKLOG #18).
- **The owner's rule for this and the following specs:** many interdependent modules are
  being built quickly, so a refactor or abstraction a feature needs is done in that feature's
  spec, not deferred.

## Read context

- `docs/ROADMAP.md` — read in full. Stage 2, item 2: "Corroboration of leaks across
  independent accounts: retrieval plus SQL over the linked players; an LLM step, if the spec
  adds one, gets its own evaluation set". The Stage DoD requires Langfuse tracing and an
  evaluation set with recorded results for every LLM step. Items 3–4 (e-mail adapter, alerts)
  consume this feature.
- `docs/PROJECT.md` — read in full.
  - FR-2.2: corroborate a new leak against earlier posts about the same player and state how
    many independent accounts agree.
  - FR-2.3: alerts, the consumer.
  - FR-4.1: per-account accuracy, which later feeds the strength grade.
  - NFRs: budget ≤ 20 PLN/month, tracing, evaluation, UTC, privacy.
  - Architecture: "extract → link → retrieve → corroborate → decide".
- `docs/DECISIONS.md` — read in full. Binding here:
  - facts via SQL, narrative via retrieval (ADR 0002);
  - OpenRouter as the only provider (ADR 0006);
  - `model_settings.toml` settings per model;
  - the relevance rule and the "next Premier League match" meaning of event types
    (2026-09-28, 2026-09-29);
  - reposts attributed to the list account in extraction;
  - Langfuse tracing;
  - evaluation sets as JSONL in the repository, split dev/test, pre-labelled by a model and
    reviewed by the owner;
  - fakes in unit tests;
  - business-module layout;
  - testcontainers.

  The row of 2026-09-29 ("chat-specific settings and each feature's evaluation stay in their
  module") is superseded by this spec for the chat-model settings.
- `docs/BACKLOG.md` — read in full.
  - #18 (short query-embedding timeout on the alert path) is triggered by this spec and
    closed here.
  - #11 (twscrape missing posts) and #12 (GW6 extraction set) stay separate; #12 is also when
    the judge set grows.
- `docs/CONVENTIONS.md` — searched for "module", "prompt", "content", "migration". Prompts
  go to `backend/app/content/`, and a module is one directory under `app/`.
- `docs/DEPLOYMENT.md` — searched for "LLM_MODEL", "OPENROUTER", "pre-deploy". The judge
  uses the existing `OPENROUTER_API_KEY` / `LLM_MODEL` / `LLM_FALLBACK_MODEL`. The migration
  reaches production through the pre-deploy command.
- `docs/adr/` — searched for "corroborat", "repost", "independent". ADR 0001 names
  "retrieve earlier posts → corroborate → decide" as the LangGraph flow this feature starts.

## Scope

- **Module `app/corroboration/`**. It has one operation,
  `corroborate(player, as_of, new_since)`, and a CLI
  `python -m app.corroboration <player> [--at] [--since] [--new-since]` that replays any moment.
- **Window:** from the latest gameweek deadline at or before `as_of` up to `as_of`. With no
  such deadline it covers the 7 days before `as_of`. `--since` overrides the start.
- **Claims:**
  - from SQL: the current extraction events linked to the player in the window;
  - from retrieval: hybrid search for the player in the window. The posts it returns that
    have no current event for the player go to the LLM judge.
- **Anchor:** the newest SQL claim in the window.
- **Labels relative to the anchor:** `supports` / `contradicts` / `related` / `unrelated`.
  - SQL claims get their label from a fixed compatibility table.
  - Judged posts get their label from the judge.
- **Independent accounts:** the original author, with a repost counted as its original
  author. Several posts by one account count once, by that account's newest labelled post.
  The anchor's own account is never its own confirmation.
- **Result:**
  - the anchor;
  - supporting and contradicting accounts, with citations (link, author, time, certainty,
    origin `sql`/`judge`), each marked `new` (newer than `new_since`) or `context`;
  - related posts;
  - a `reversal` flag;
  - a flag for a contradicting post newer than the anchor;
  - the strength grade (`high` / `medium` / `low`) with the reasons behind it.
- **Strength grade** computed here by an explicit threshold rule, with a place for account
  credibility (Stage 4).
- **LLM judge** as a traced LangGraph step, with its prompt in `backend/app/content/prompts/`.
- **Judge evaluation:**
  - set v1: JSONL cases (anchor, player, post, label), split dev/test;
  - pre-labels by a stronger model than the judge;
  - an interactive review command;
  - a runner with metrics and a result file;
  - a first recorded run.
- **Migration:** `tweet.reposted_author_handle` (nullable). All three source adapters fill it,
  and the migration backfills it from `raw`.
- **Tracing:** one Langfuse trace per corroboration, holding the retrieval and the judge calls.
- **Refactors, done in this spec:**
  - the chat model and retries move to `app/llm/`: one OpenRouter chat-model factory, the
    model-settings catalogue, a structured call with usage and cost, and one retry-with-
    back-off helper. Extraction, the retrieval evaluation and the judge use them.
  - one public "current extraction" query in `app/extraction/store.py` replaces the three
    copies of that SQL;
  - deadline helpers in `app/fpl` ("latest deadline at or before", "next deadline after");
    `app/tweets` and `app/worker` use them;
  - the query-embedding timeout is set per call. Corroboration uses a short one, while the
    `search` CLI and indexing keep 30 s.
  - `SearchResult` carries `is_repost` and `reposted_author_handle`.
- **Documentation:**
  - DECISIONS rows;
  - the ROADMAP link;
  - BACKLOG: #18 closed, and new entries from "Out of scope";
  - DEPLOYMENT, if the migration or configuration needs a note.

## Out of scope

- **The alert schedule** (T-2h digest, T-30 and T-10 news, "breaking" after T-10 until the
  deadline), choosing players, remembering when the last alert went out, the post → inbox
  latency definition, and the descriptive risk text written by an LLM (with its faithfulness
  evaluation). All of this is the alert spec (Stage 2, item 4). This spec only provides
  `as_of` and `new_since`.
- **Account credibility in the strength grade:** Stage 4 (FR-4.1). This spec leaves the hook.
- **Detecting a shared cited source** ("per club statement", "via @journalist") and
  aggregator accounts, so that posts repeating one source count once → BACKLOG, P2. Trigger:
  the judge evaluation or alert review shows confirmations inflated by aggregators.
- **Players with no SQL claim** whom only retrieval would find: they get no anchor and are
  not corroborated → BACKLOG, P3. Trigger: the judge evaluation shows extraction missing a
  player entirely in more than a few windows.
- **Pass thresholds for the judge and choosing its model by comparison:** after the owner's
  review of set v1 and the GW6 window (together with BACKLOG #12).
- **A corroboration table or a worker loop:** corroboration is computed on demand.
- **Changing extraction's repost attribution.** Extraction stays as it is; only corroboration
  resolves the original author.

## Requirements and acceptance criteria

**Refactors (no change in behaviour)**

- [ ] AC1: One OpenRouter chat-model factory, the model-settings catalogue, a structured
      call returning the parsed output with usage and cost, and one retry-with-back-off
      helper live in `app/llm/`. `app/extraction` and `app/retrieval` build no
      `ChatOpenRouter` of their own and hold no retry loop of their own.
- [ ] AC2: One public function in `app/extraction/store.py` returns the current extraction
      (events included) for posts. No other module repeats the `DISTINCT ON … finished_at DESC`
      SQL for the current extraction.
- [ ] AC3: `app/fpl` provides "latest deadline at or before t" and "next deadline after t",
      and the tweet poller and the worker schedule use them.
- [ ] AC4: The existing extraction, retrieval, tweet and worker tests pass with only
      import-path changes. The environment variable names and the CLI commands are
      unchanged, and `model_settings.toml` keeps its content.
- [ ] AC5: An embedding call takes a timeout per call. With a fake that takes longer than
      the corroboration timeout, a corroboration continues on full-text only, marks the
      vector leg failed, and records this in the result. The `search` CLI keeps 30 s.

**Repost authors**

- [ ] AC6: The migration adds `tweet.reposted_author_handle`, backfills it from `raw` for
      every stored repost of the three source shapes, and downgrades cleanly (the
      upgrade → downgrade → upgrade test pattern).
- [ ] AC7: Each of the three source adapters sets `reposted_author_handle` for a repost and
      leaves it empty for an original post (tests on recorded payloads).

**Corroboration**

- [ ] AC8: The window runs from the latest deadline at or before `as_of` to `as_of`. With no
      such deadline it covers the 7 days before `as_of`, and `--since` overrides the start.
      Posts after `as_of` are never used, so replaying a past moment gives the same result
      as it would have given then (assuming no re-extraction since).
- [ ] AC9: The anchor is the newest current extraction event for the player with
      `created_at` in the window. With no such event the result is "no claim", no judge call
      is made, and the CLI says so.
- [ ] AC10: SQL claims are labelled relative to the anchor's event type by this table:
      the same type → `supports`; `confirmed_starter` against `out`/`doubt`/`benched` (either
      way) → `contradicts`; any other pair among `out`/`doubt`/`benched` → `related`.
- [ ] AC11: Retrieval runs a hybrid search for the player in the window. Every returned
      post with no current event for the player (up to 10 per corroboration) goes to the
      judge, which labels it `supports`, `contradicts`, `related` or `unrelated` relative to
      the anchor, under the same "next Premier League match" rule as extraction. `unrelated`
      posts do not appear in the result.
- [ ] AC12: Accounts count by the original author: `reposted_author_handle` for a repost,
      `author_handle` otherwise. An account with several labelled posts counts once, by its
      newest one. The anchor's account never counts as a confirmation of the anchor.
- [ ] AC13: Every cited post carries its X link, author (and original author for a repost),
      `created_at`, certainty (SQL claims), origin (`sql`/`judge`) and label, and it is marked
      `new` when `created_at` is after `new_since` and `context` otherwise.
      `new_since` defaults to the window start.
- [ ] AC14: `reversal` is true when at least one independent account whose counted post is
      older than the anchor contradicts it. A separate flag is set when a judged post newer
      than the anchor contradicts it.
- [ ] AC15: The strength grade is computed as follows:
      - `high`: the anchor's certainty is `confirmed`, or it has ≥ 2 independent supporting
        accounts;
      - `medium`: the certainty is `likely`, or it has 1 supporting account;
      - `low`: otherwise;
      - a contradicting account counted as `new`, or a contradicting post newer than the
        anchor, lowers the grade one step.

      The result lists the reasons, and the thresholds are named parameters. A test covers
      each branch.
- [ ] AC16: The CLI prints the anchor, the grade with its reasons, the flags, then the
      supporting, contradicting and related posts grouped into `new` and `context`. It
      accepts `--at`, `--since` and `--new-since` in `Europe/Warsaw` (stored and compared in
      UTC) and a player given by FPL ID or by name (an ambiguous name lists the candidates
      and exits non-zero).
- [ ] AC17: With `OPENROUTER_API_KEY` unset, corroboration runs on SQL claims only and says
      that retrieval and the judge were skipped. A judge call that fails after retries
      leaves that post out, and the result says how many posts went unjudged; it never
      fails the whole corroboration.

**Tracing**

- [ ] AC18: With Langfuse configured, each corroboration is one trace. It holds the player,
      `as_of`, `new_since`, the anchor, the retrieval search, every judge call as a
      generation (model, tokens, cost) and the final grade. Without Langfuse everything
      runs untraced.

**Judge evaluation**

- [ ] AC19: Set v1 is committed JSONL. Each case holds the player, the anchor (event type,
      certainty, text), the post and the expected label, split dev/test. It holds about 60
      cases built from the development corpus: retrieved posts for players that have
      extraction events, including the known misses (e.g. Isak), with every label present.
- [ ] AC20: Cases are pre-labelled by a model stronger than the judge's default, named in
      the case and chosen in the plan from `model_settings.toml`. Labels are stored as
      unreviewed.
- [ ] AC21: An interactive review command walks the unreviewed cases, lets the owner
      accept, change or skip the label, and saves after every decision, as the extraction
      `review` command does.
- [ ] AC22: The runner reports accuracy, per-label precision and recall, and the
      false-support rate (an `unrelated`/`related`/`contradicts` case labelled `supports`)
      for a chosen split and model. It counts reviewed cases only unless
      `--include-unreviewed` is given, and it writes a JSON result file with the model,
      prompt version, split, date, cost and per-case outcomes. Metric computation has unit
      tests on hand-computed cases.
- [ ] AC23: One run of the default judge model on the test split with `--include-unreviewed`
      is committed as a result file. The owner's review and the pass thresholds are an
      owner-side step after the PR.

**Documentation and hygiene**

- [ ] AC24: The `docs/DECISIONS.md` rows and the `docs/BACKLOG.md` entries (#19, #20) added
      with this SPEC still match what was built (edited in place if the implementation
      changed them), `docs/PROJECT.md` FR-2.2 matches the behaviour, and BACKLOG #18 is
      removed once the per-call timeout is in.
- [ ] AC25: Tests use fake chat models and embedders, and database tests use
      testcontainers. `<verify.command>` stays green.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| The newest claim is the anchor, and earlier posts are context labelled against it | majority vote across accounts; a claim-state summary without an anchor | a leak five minutes before the deadline must override a week of "he plays"; a majority rule would outvote it (the owner) |
| Unit: player + `as_of` + `new_since`, computed on demand | per new post; a worker loop with a stored table | the alert schedule (T-2h digest, T-30/T-10 news, breaking) needs the same player at several moments; always fresh; no stale state |
| Labels `supports` / `contradicts` / `related` / `unrelated` relative to the anchor | the judge returning an event type like extraction | with an anchor, the stance is what alerts need; SQL claims map to the same labels, so both origins speak one language (the owner) |
| Compatibility table: `confirmed_starter` against the other three is a contradiction; `out`/`doubt`/`benched` are mutually `related` | `out` and `doubt` confirming each other | a `doubt` must not inflate the confidence of an `out`; precision lives in the event type |
| LLM judge only on retrieved posts with no current event for the player | SQL only with retrieval as uncounted context; improving extraction recall instead | extraction reads blind and misses players in long posts (Isak: 3 of 7); a narrow question about one known player over a handful of posts recovers them now, and it is the RAG step (retrieve → judge) of Stage 2 |
| Independence by original author (reposts resolved), newest post per account | plain distinct `author_handle`; shared cited-source detection by an LLM | deterministic and testable; shared-source detection is a harder extraction, deferred to the backlog |
| Original repost author as a column filled by the source adapters, backfilled from `raw` | parsing `RT @x:` from the text | explicit and independent of text formatting; each adapter already reads its payload |
| The strength grade is computed in corroboration by an explicit threshold rule with a credibility hook; alerts only render and filter by it | the grade in the alert spec; weights by certainty | the grade is an assessment of the evidence, which corroboration owns; Q&A (Stage 4) and alerts reuse one definition; credibility (FR-4.1) plugs into the same function; weights need data we do not have yet |
| Shared refactors in this spec: chat model and retries to `app/llm`, one current-extraction query, deadline helpers in `app/fpl`, a per-call embedding timeout | deferring them to a later cleanup | the owner's rule: dependent modules are being built fast, so a needed abstraction is done in the spec that needs it; the judge is the third chat consumer, and alerts will be the next user of deadlines |
| Judge set v1 pre-labelled by a stronger model, reviewed after the PR | pre-labels by the judge's own model; review blocking done | the judge's model grading itself is circular; real pre-deadline line-up leaks exist only after GW6 |

## Owner decisions

- Data migration `tweet.reposted_author_handle` with a backfill from `raw` — accepted.
  Production gets it through the Railway pre-deploy after the owner merges.
- New dependency: none.
- LLM judge with its own evaluation set v1. The owner's review and the pass thresholds come
  after the PR.
- Refactors in this spec: chat model + retries to `app/llm`, the current-extraction query,
  deadline helpers, the per-call embedding timeout — accepted. For following specs: do a
  needed refactor in the spec that needs it.
- Window: since the latest deadline.
- For the alert spec (recorded here so it is not lost): T-2h digest, T-30 and T-10 news
  only, "breaking" alerts after T-10 until the deadline, and a descriptive risk text.

## Open questions (non-blocking)

- For the alert spec: how to define and measure post → inbox latency under a scheduled
  digest plus breaking alerts (ROADMAP Stage 2, item 4).
