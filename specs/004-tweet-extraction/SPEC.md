---
status: spec-ready
stage_history:
  - "spec-draft — 2026-09-28"
  - "spec-ready — 2026-09-28"
---

# SPEC 004 — LangGraph extraction flow, player linking and the default LLM

## Goal

Every stored post from the watched X List becomes a typed, stored extraction: which players
it is about (linked to FPL player IDs), what happened to each (out / doubt / benched /
confirmed starter) and how sure the author is. The flow runs automatically in the worker,
every LLM call is traced in Langfuse, and the default LLM is chosen by measuring cheap models
from four providers on an evaluation set with recorded results. We know it works when the
chosen model passes the agreed thresholds on the test split and new posts get their
extraction in the worker without delaying the tweet polls.

## Context

- Last item of Stage 1. Spec 003 delivered the ingest: posts are stored in `tweet`
  (`backend/app/tweets/models.py` — X ID, author handle, text, `created_at`,
  `first_fetched_at`, source, `is_repost`, `is_reply`, raw JSON) by a polling loop running in
  its own thread inside the worker (`backend/app/tweets/loop.py`, started from
  `backend/app/worker/cli.py`). Nothing reads the posts yet.
- FPL players live in `player` (`backend/app/fpl/models/reference.py`): keyed by
  (season, FPL ID), with `web_name`, `first_name`, `second_name`, `team_fpl_id`, `position`;
  teams in `team` (`name`, `short_name`). The current season in the local database is
  `2026/27` (667 players).
- The codebase has no LLM code yet: no LangGraph, LangChain, Langfuse or
  `backend/app/content/` directory. The module layout reserves `app/extraction` (DECISIONS,
  2026-09-26).
- What the posts look like (local database, 2026-09-25 … 2026-09-28, 107 posts from 22
  accounts, an international break): most posts are noise (fantasy tool promotion, club
  politics, anniversaries); about 20% are reposts; real signal mixes Premier League news
  with national-team line-ups ("Haaland starts for Norway"), injuries on international duty
  ("Ødegaard limped out… required treatment") and women's-team line-ups whose surnames can
  collide with Premier League players. The next deadline (GW6) is 2026-10-10 10:00 UTC, so
  no pre-deadline line-up leaks exist yet.
- Volume: about 35 posts a day, about 1,000 a month; at this volume a cheap model costs
  pennies to a few PLN a month depending on the provider, so the model choice matters for
  the 20 PLN/month budget.
- Terms used below:
  - **event** — (player, event type) extracted from a post, with the author's certainty;
  - **event type** — `out` (injury, illness, suspension; will not play), `doubt` (the
    player's availability is uncertain, e.g. "50/50", "a late fitness test"), `benched`
    (fit but not starting), `confirmed_starter` (will start);
  - **certainty** — how sure the author is of the claim, independent of the event type:
    `confirmed` (official line-up, the manager's or the club's own words, "confirmed"),
    `likely` ("expected to", "I understand", "set to"), `rumour` ("hearing", "could",
    "might", unverified);
  - **relevance rule** — availability events (`out`, `doubt`) count whatever the competition
    (an injury on international duty matters); line-up events (`benched`,
    `confirmed_starter`) count only for the player's next Premier League match; national
    team, cup and European line-ups and women's football give no events.

## Read context

- `docs/ROADMAP.md` — read in full. Stage 1, item 3 (LangGraph extraction flow: player
  linking, event type, certainty; Langfuse tracing; extraction evaluation set) is delivered
  by this spec. Item 2 (latency measurement, the owner's run, BACKLOG #9) and Stage 0's
  production deploy are the owner's operations and do not block it. The Stage definition of
  done requires Langfuse tracing and an evaluation set with recorded results.
- `docs/PROJECT.md` — read in full. FR-1.2 (typed result: players linked to FPL IDs, event
  type, source account, the author's stated certainty), FR-1.3 (exactly four event types),
  FR-1.4 (keep every post and extraction); the architecture's flow (extract → link →
  retrieve → corroborate → decide — this spec builds the first two nodes); "the default LLM
  is picked in the stage 1 spec by comparing cheap models on the extraction evaluation set";
  non-functional: 20 PLN/month budget, swappable LLM provider, every LLM call traced,
  evaluation before a stage is done, privacy (public player data may be real).
- `docs/DECISIONS.md` — read in full. LangGraph with typed Pydantic outputs (ADR 0001); the
  LLM provider behind the LangChain chat-model interface, the cheapest model that passes the
  evaluation set wins; Langfuse from the first LLM stage; unit tests use a fake LLM and
  evaluation sets run separately from `pytest`; modules per business area (`app/extraction`);
  one worker process with independent loops (the tweet poller never waits on FPL jobs);
  exact dependency pins; the 60 s target covers detection only, extraction comes on top.
- `docs/BACKLOG.md` — read in full. #1 (PydanticAI comparison) stays with the Q&A agent;
  #7 (event types beyond the four) stays deferred; #9 reserves ADR 0005 for the tweet-source
  choice, so this spec's ADR is 0006.
- `docs/CONVENTIONS.md` — read in full. Prompts live in `backend/app/content/`, loaded by
  name; LLM steps unit-tested against a fake model; evaluation sets run through a separate
  command; database tests on a container; exact pins; logs never carry credentials.
- `docs/DEPLOYMENT.md` — searched for "variable", "TWEET_SOURCE", "secret": the tweet ingest
  section is the pattern for an optional feature switched on by variables; the new LLM and
  Langfuse variables go there.
- `docs/adr/` — `README.md` and `0001-llm-orchestration-langgraph.md` read in full; the
  other ADRs (0002–0004) not relevant (retrieval, tweet source, e-mail).
- `specs/003-tweet-ingest/SPEC.md` — read in full: the ingest this flow consumes and the
  patterns it repeats (optional feature off when unconfigured, credentials never logged,
  the owner's manual measurement run before the merge with a report and an ADR).

## Scope

- A LangGraph flow in `app/extraction` with two stages: **extract** (LLM, typed output:
  events with the player named as written, team context when stated, event type, certainty)
  and **link** (resolve each named player to an FPL player of the current season).
- Linking: a deterministic lookup over the current season's players (web name, first and
  last name, full name, aliases, accent-insensitive, narrowed by team when stated); the LLM
  only chooses among candidates when the lookup is ambiguous; unresolved mentions are kept
  unlinked.
- A reviewed alias file (nicknames and short forms such as "KDB", "Bruno", "TAA").
- The extraction prompt as a file in `backend/app/content/`, with a version recorded on
  every extraction.
- LLM provider and model selected by configuration: Google Gemini, OpenAI, Anthropic and
  OpenRouter (OpenRouter through its OpenAI-compatible API).
- Langfuse Cloud (EU region) tracing of every LLM call in the flow.
- Storage of every extraction and its events (new tables, Alembic migration); re-extraction
  keeps the history.
- An extraction loop in the worker process, independent of the tweet poller and of the FPL
  jobs; extraction state in `python -m app.worker status`.
- A CLI to re-extract posts (by X ID, by time range, all failed), optionally with another
  model.
- An evaluation set v1 in the repository: about 150 real posts collected so far plus about
  25 synthetic cases for rare categories, split into dev (~30%, for prompt work) and test
  (~70%, for the model comparison), pre-labelled by a model and reviewed case by case by the
  owner; a snapshot of the public player data it refers to.
- A pre-labelling command and an evaluation command (outside `pytest`) computing the
  metrics, cost and latency per model.
- The owner's comparison run on the test split (at least one cheap model per provider), a
  results report under `docs/`, ADR 0006 choosing the default model, and the DECISIONS rows.
- Documentation: new variables in `backend/.env.example`, `docs/DEPLOYMENT.md` and the
  README; a BACKLOG item to extend the evaluation set with the GW6 deadline window.

## Out of scope

- Retrieval, embeddings, corroboration across accounts, deciding whether to alert, e-mail —
  Stage 2.
- Settling events against the ground truth and per-account accuracy — Stage 4.
- Event types beyond the four, expected return dates, injury duration, fixture or
  competition fields — BACKLOG #7 (P3, trigger: subscribers ask for them).
- Extending the evaluation set with the GW6 deadline window (real pre-deadline leaks) —
  new BACKLOG item, P1, trigger: the GW6 deadline (2026-10-10) has passed with the ingest
  running.
- An evaluation regression run in CI — Stage 5.
- Self-hosted Langfuse — not planned (Langfuse Cloud free plan).
- Automatic fallback to a second LLM provider when the default fails — BACKLOG, P2,
  trigger: extractions fail for a whole deadline window because of the provider.
- Prompt caching or batching of several posts into one LLM call — not needed at ~1,000
  posts a month.

## Requirements and acceptance criteria

Configuration and tracing

- [ ] AC1: The LLM provider (`google`, `openai`, `anthropic`, `openrouter`) and the model
  name are selected by environment variables; switching either needs no code change. An
  unknown provider or a missing API key for the selected provider → a configuration error
  naming the missing variable, never its value.
- [ ] AC2: With no LLM configured, the worker starts and runs the FPL jobs and the tweet
  ingest exactly as before, logging once that extraction is disabled (the production deploy
  keeps working without new variables).
- [ ] AC3: With Langfuse keys set, each post's run of the flow is one Langfuse trace holding
  every LLM call in it (extraction and any disambiguation) with inputs, outputs, token usage,
  cost, latency, model and prompt version, and the post's X ID as metadata; tested without
  network by asserting what the flow hands to the tracing callback.
- [ ] AC4: With Langfuse keys missing, extraction still runs, untraced, and the worker logs
  one warning at start.
- [ ] AC5: LLM and Langfuse credentials never appear in logs, error messages or stored rows;
  a test asserts it on the error paths.

Extraction

- [ ] AC6: For every post the flow returns a typed result: zero or more events, each with
  the player mention as written, the linked FPL player (season, FPL ID) or none, the event
  type (one of the four) and the certainty (one of the three). A post with no relevant event
  gives an empty result, not an error.
- [ ] AC7: A post about several players yields one event per player with its own type and
  certainty (e.g. a leaked XI with substitutes → `confirmed_starter` for each starter and
  `benched` for each named substitute; "X out, Y starts" → two events); tested with a fake
  model.
- [ ] AC8: The relevance rule (Context) is stated in the prompt, and the evaluation set holds
  at least 3 cases each of: an injury on international duty (events expected), a
  national-team line-up, a women's-team line-up, and a cup or European line-up (no events
  expected); a test checks the set's composition.
- [ ] AC9: Reposts and replies are extracted like any other post; their events are
  attributed to the list account that posted them (`author_handle`), and the post's
  `is_repost` / `is_reply` stay readable next to the extraction.

Linking

- [ ] AC10: A mention that matches exactly one player of the current season by web name,
  first or last name, full name or alias — case- and accent-insensitive ("Odegaard" finds
  "Ødegaard"), narrowed by the team when the post states one — is linked without an LLM
  call; tested on a synthetic player table.
- [ ] AC11: A mention matching several players is resolved by the LLM choosing among those
  candidates only; an answer outside the candidate list, no answer, or no candidate at all
  leaves the mention unlinked with its text kept, and the event is still stored.
- [ ] AC12: Aliases live in a versioned data file in the repository; adding an alias needs
  no code change; an alias pointing to a player missing from the current season is ignored
  and logged once.
- [ ] AC13: Linking uses only the players of the current season (the latest season in the
  `player` table).

Storage

- [ ] AC14: Every extraction attempt of a post is stored: the post's X ID, status
  (`extracted` / `failed`), provider and model, prompt version, start and end, attempts,
  error class on failure, and token usage and cost when the provider reports them; the
  events belong to their extraction.
- [ ] AC15: Re-extracting a post adds a new extraction and keeps the earlier ones; the
  current extraction of a post (its latest `extracted` one) can be read with one query.
- [ ] AC16: The migration only adds new tables; `alembic upgrade head` and `downgrade` pass
  on the test container, and the existing tables are unchanged.

Worker

- [ ] AC17: The worker extracts every stored post that has no extraction yet, in its own
  loop independent of the tweet poller and the FPL jobs: with a blocking fake LLM the tweet
  polls continue at 20 s in the window; tested with fakes and a fake clock.
- [ ] AC18: With no backlog, extraction of a newly stored post starts within 5 s of the post
  being stored; posts are processed oldest first.
- [ ] AC19: A provider error, timeout, rate-limit response or output failing validation is
  retried up to 3 attempts in total with back-off; after the last attempt the extraction is
  stored as `failed` with the error class; other posts keep being processed and the worker
  keeps running.
- [ ] AC20: The extraction latency (post stored → extraction stored) is recorded per
  extraction; `python -m app.worker status` shows the model, posts waiting, failed
  extractions and the latest extraction.
- [ ] AC21: Shutdown (SIGTERM / Ctrl-C) stops the extraction loop together with the worker
  within the existing shutdown bound of spec 002.

CLI

- [ ] AC22: A CLI command re-extracts one post by X ID, the posts in a `created_at` range, or
  all posts whose current state is `failed`, optionally with another provider and model; it
  prints a summary: posts processed, events, failures, total cost.

Evaluation

- [ ] AC23: The evaluation set is a JSONL file in the repository; each case holds an ID (the
  X ID, or a synthetic ID), author handle, text, `created_at`, split (`dev` / `test`), a
  `synthetic` flag, a `reviewed` flag and the expected events (mention, FPL ID or none,
  event type, certainty). It has about 150 real and about 25 synthetic cases; every event
  type and certainty level appears at least 5 times in the test split, and it includes
  multi-player posts, reposts, ambiguous names, nicknames and accented names. A `pytest`
  test validates the schema, the composition rules and that every expected FPL ID exists in
  the committed player snapshot.
- [ ] AC24: A pre-labelling command runs a chosen model on posts from the local database and
  writes candidate cases with `reviewed: false`; the evaluation command refuses cases that
  are not reviewed.
- [ ] AC25: The evaluation command (never run by `pytest`) runs a given provider and model on
  a split and records: event precision, recall and F1 (an event matches on FPL player — or
  the mention when unlinked — and event type), linking accuracy on mentions with an expected
  FPL ID, the false-alarm rate (share of posts with no expected events that got any event),
  certainty accuracy with a confusion table, latency p50/p95, tokens and cost per post, and
  the projected monthly cost at the observed posting volume, in PLN at a rate set in
  configuration; results are written to a file and the run is traced in Langfuse under a run
  name. The metric computation is tested in `pytest` on synthetic predictions with known
  values.
- [ ] AC26 (manual, owner): before the merge, the owner reviews every pre-labelled case, then
  runs the evaluation on the test split for at least one cheap model from each of Google,
  OpenAI, Anthropic and OpenRouter; the prompt is tuned on the dev split only. The results
  are committed as a report under `docs/` in this PR.
- [ ] AC27: ADR 0006 records the default model: the cheapest one that passes all
  thresholds on the test split — event F1 ≥ 0.85, linking accuracy ≥ 0.95, false-alarm rate
  ≤ 5%, projected cost ≤ 5 PLN a month; DECISIONS gets the row and the configuration default
  names that model. If no model passes, the ADR says so, names the best one as the interim
  default, and a BACKLOG item is added.

Documentation

- [ ] AC28: `backend/.env.example`, `docs/DEPLOYMENT.md` and the README list the new
  variables (provider, model, per-provider API keys, Langfuse keys and host, the PLN rate)
  with placeholders only, and describe how to run the extraction, the CLI, the pre-labelling
  and the evaluation; BACKLOG gets the GW6 evaluation-set item (Out of scope).

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Evaluation set v1 now: real posts collected so far plus marked synthetic cases for rare categories; the GW6 window extends it later (BACKLOG P1) | waiting for the GW6 deadline window (2026-10-10) before the PR; merging the flow without an evaluation | Stage 2 (RAG, the portfolio priority) does not wait for the fixture calendar; the international break gives many hard negatives; the stage's definition of done needs the evaluation now |
| The evaluation set, with the full text of public posts, lives in the repository | X IDs only, texts hydrated from the local database; synthetic paraphrases only | a reproducible evaluation anyone can read and rerun; the posts are public news content, not private users' data (the privacy rule covers managers and leagues); the X terms-of-service risk is accepted by the owner |
| Four providers compared: Google Gemini, OpenAI, Anthropic, OpenRouter (through the OpenAI-compatible client) | one or two providers | a fair comparison for the ADR and the portfolio; OpenRouter adds open-weight models without an extra client package |
| Langfuse Cloud, EU region, free plan | self-hosting Langfuse on Railway; self-hosting only locally | self-hosted Langfuse v3 needs ClickHouse, Redis and object storage (several GB of RAM); ~1,000 posts a month fit the free plan; a local-only Langfuse would leave production untraced |
| Linking: the LLM names players as written; a deterministic lookup with aliases and team context links them; the LLM only chooses among candidates when ambiguous | the whole player list in every prompt; an agent calling a player-search tool | ~7k tokens per post for the full list; a tool loop adds calls and latency; the lookup is cheap, explainable and evaluated on its own |
| Extraction runs automatically in its own loop in the worker, plus a re-extraction CLI | a CLI only, wired to the worker in Stage 2 | posts are extracted as they arrive; extraction latency is measured in production from day one; a slow LLM never delays tweet polls or FPL jobs |
| Relevance rule: availability events from any competition, line-up events only for the next Premier League match; national-team, cup, European and women's line-ups give no events | every event only for Premier League matches; everything plus a competition field | an injury on international duty changes FPL availability; a national-team line-up does not; a competition field would widen FR-1.3 |
| Certainty on three levels: confirmed / likely / rumour | a 0–1 score; two levels | labels agree better with a small defined scale; a number would be false precision |
| Reposts are extracted like ordinary posts, attributed to the list account | skipping reposts; attributing to the original author | the owner's decision: simplest, nothing on the list is lost; `is_repost` stays on the post for Stage 2 |
| Dev/test split, the prompt tuned on dev only; labels pre-generated by a model and reviewed by the owner case by case | one set without a split; labelling from scratch | a test split untouched by prompt work gives an honest comparison; review keeps the labels the owner's while saving hours |
| Pass thresholds: event F1 ≥ 0.85, linking accuracy ≥ 0.95, false-alarm rate ≤ 5%, projected cost ≤ 5 PLN a month; certainty reported without a threshold | stricter thresholds; no thresholds | the DECISIONS rule "the cheapest model that passes the evaluation set wins" needs a definition of passing; 5 PLN leaves room in the 20 PLN budget for the tweet source and e-mail |

## Owner decisions

- New dependencies accepted up front, with exact pins: `langgraph`, `langchain-core`,
  `langchain-google-genai`, `langchain-openai` (also used for OpenRouter),
  `langchain-anthropic`, `langfuse`, and `rapidfuzz` if the plan needs fuzzy matching.
- Data migration accepted up front: new tables only (extractions, events); applied locally
  and on the test container by the agent, on production by the Railway pre-deploy after the
  owner's merge.
- The evaluation set with the full text of public posts is committed to the repository.
- Reposts are extracted like ordinary posts (the owner overrode the recommendation to skip
  them).
- The owner provides API keys for Google, OpenAI, Anthropic and OpenRouter and a Langfuse
  Cloud (EU) project, reviews every pre-labelled case, and runs the comparison before the
  merge; the comparison runs' spend stays within 10 PLN in total.

## Open questions (non-blocking)

- Stage 2 corroboration: should a repost count as an independent confirmation? Reposts carry
  `is_repost`, so Stage 2 decides.
- Does a real deadline window (GW6) change the metrics? Answered by the BACKLOG item that
  extends the evaluation set; the default model may be revisited then.
- Posts in languages other than English are extracted like any other post; the evaluation
  set is expected to be mostly English, so their quality is not measured separately.
