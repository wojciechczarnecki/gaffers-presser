---
status: done
stage_history:
  - "spec-draft — 2026-09-28"
  - "spec-ready — 2026-09-28"
  - "plan-draft — 2026-09-28"
  - "plan-approved — 2026-09-28"
  - "implemented — 2026-09-28"
  - "done — 2026-09-28"
metrics:
  started_at: 2026-09-28T12:43
  escalations: 1
  plan_steps: 18
  plan_review_blockers: 0
  plan_review_majors: 2
  plan_changes: 13
  implement_steps: 17
  implement_iterations: 5
  converge_gaps: 0
  deviations_minor: 5
  deviations_major: 0
  implement_chunks: 5
  final_review_blockers: 1
  final_review_worth_fixing: 14
  final_review_nits: 5
  findings_accepted: 15
  findings_rejected: 5
  finished_at: 2026-09-28T15:08
  cost_plan_cents: 225
  cost_plan_review_cents: 93
  cost_implement_cents: 1365
  cost_final_review_cents: 1110
---

# SPEC 003 — Tweet sources, ingest and latency measurement

## Goal

Posts from the watched X accounts land in the database continuously; in the final window
before an FPL deadline our system fetches a leak within 60 s (p95) of it being posted. The
source behind them is swappable by configuration, and the choice of the default source rests
on a measured detection latency per candidate, recorded in a report and an ADR.

## Context

- This spec merges two Stage 1 roadmap items: the latency spike and the swappable tweet
  source with ingest. The spike is not a separate throwaway step: extraction sits behind the
  `TweetSource` interface and does not depend on which source wins, so the measurement
  becomes an acceptance criterion of the ingest itself.
- The existing worker (`backend/app/worker/` — `loop.py`, `schedule.py`, `jobs.py`,
  `store.py`, `models.py`; spec 002) runs FPL jobs one after another under PostgreSQL
  advisory locks (`backend/app/db/locks.py`) and reads the gameweek calendar from the
  collector tables (`backend/app/fpl/models/`, spec 001). Deadlines for the polling window
  come from the same calendar.
- Configuration lives in `backend/app/core/settings.py` (pydantic-settings, `.env`);
  errors in `backend/app/core/errors.py`.
- Detection latency = the moment our system first fetched a post − the post's `created_at`
  on X. The 60 s target applies to this leg alone (owner's decision 2026-09-28); LLM
  extraction (next Stage 1 spec) and e-mail delivery (Stage 2) come on top of it.
- Polling model (explained to and chosen by the owner): with polling interval I, a post waits
  0…I for the next poll (on average I/2). A later poll never loses posts — it returns every
  post newer than the last seen one — it only delays them. Hence dense polling in the final
  window, where latency matters, and sparse polling outside it, where only completeness
  matters (a long gap would require deep pagination of the list timeline, which is capped).
- Candidate sources (state checked 2026-09-28):
  - **twscrape** — free, logged in with X accounts' cookies, maintained (0.20.x, August
    2026); breaks when X rotates internal identifiers, risk of a ban of the account;
  - **twitterapi.io** — third-party scraping API, about $0.00015 per call and $0.15 per
    1,000 posts, free trial credits without a card;
  - **official X API v2** — pay-per-use since February 2026, about $0.005 per post read, no
    free credits for new developer accounts; cost depends on post volume, not on polling
    frequency.
- Expected detection latency with a 20 s interval: about 10–15 s on average, p95 about
  25–35 s (to be confirmed by the measurement).

## Read context

- `docs/ROADMAP.md` — read in full. Stage 1, items 1 (latency spike) and 2 (swappable source
  and ingest) are delivered together by this spec; item 3 (extraction) follows. Stage 0's
  last item (production deploy, owner, ~2026-10-04) is operational and does not block this
  work.
- `docs/PROJECT.md` — read in full. FR-1.1 (ingest through a swappable source), FR-1.4
  (keep every post); non-functional: 60 s p95 latency in the final window, 20 PLN/month
  budget, swappability, privacy (credentials only in environment variables), UTC time.
- `docs/DECISIONS.md` — read in full. ADR 0003 (swappable `TweetSource`, free scraper first,
  latency measured before building on it); one long-running worker with a deadline-driven
  scheduler, no cron; modules per business area (`app/tweets`); advisory-lock serialisation
  of FPL jobs; exact dependency pins; tests against recorded payloads.
- `docs/BACKLOG.md` — read in full. #2 (switch to the official X API when the free scraper
  breaks, the 60 s target is missed, or before monetization) — this spec builds the official
  adapter so that switch becomes configuration only.
- `docs/CONVENTIONS.md` — read in full. Recorded payloads for external APIs, no network in
  `pytest`, database tests on a container, exact pins, logs without credentials.
- `docs/DEPLOYMENT.md` — searched for "variable", "env", "secret", "volume", "service": new
  environment variables for the source must be added to the runbook.
- `docs/adr/0003-swappable-tweet-source.md` — read in full (see DECISIONS above).

## Scope

- A `TweetSource` interface and three adapters: twscrape, twitterapi.io and the official
  X API v2. The active adapter is chosen by configuration.
- Watched accounts are configured as one public X List (its ID in an environment variable);
  every poll reads the list timeline — one request per poll for all accounts.
- Storage of every fetched post (new table(s), Alembic migration): the post's X ID, author,
  text, `created_at`, the moment we first fetched it, the source adapter that fetched it and
  the raw payload.
- Ingest in the existing worker process as a separate polling loop, independent of the FPL
  job queue: every 20 s from 90 min before the next deadline until the deadline, every
  30 min otherwise.
- A record of every poll's outcome, so a silent or failing source is detectable (input for
  Stage 5 failure alerting).
- A latency measurement command that polls all configured sources side by side on the same
  posts, records per-post detection latency to a file, and produces a summary per source
  (count, p50, p95, max, errors).
- A measurement report and an ADR choosing the default source, based on a run with
  controlled posts performed by the owner before the merge.
- Documentation: new environment variables in `backend/.env.example` and
  `docs/DEPLOYMENT.md`; README section on running the ingest and the measurement.

## Out of scope

- LLM extraction, player linking, Langfuse — next Stage 1 spec.
- E-mail delivery and the full post → inbox measurement — Stage 2 (the e-mail leg is
  measured there).
- Retrieval, embeddings, corroboration — Stage 2.
- Automatic fallback between sources when one fails — BACKLOG (P2, trigger: the default
  source fails in a real deadline window).
- Failure alerting on a silent source — Stage 5 (this spec only records the data it needs).
- Measuring the official X API — optional, only if the owner buys credits; the adapter is
  built and tested regardless.
- Several account pools, proxies and account rotation for twscrape — BACKLOG (P3, trigger:
  rate limits or a ban of the single dedicated account).

## Requirements and acceptance criteria

Sources and configuration

- [ ] AC1: Each of the three adapters (twscrape, twitterapi.io, official X API) turns its
  recorded list-timeline payload into the same normalised post form (X ID, author handle,
  text, `created_at` in UTC, raw payload); tested on recorded payloads without network.
- [ ] AC2: Each adapter fetches only posts newer than the last seen X ID for the list, and
  follows pagination until it reaches that ID or the source's page limit; tested on a
  recorded multi-page payload.
- [ ] AC3: The active source is selected by an environment variable; switching it requires
  no code change. An unknown source name or missing credentials for the selected source →
  a clear configuration error naming the missing variable, without its value.
- [ ] AC4: When no tweet source is configured, the worker starts and runs the FPL jobs
  exactly as before, logging once that tweet ingest is disabled (the production deploy of
  spec 002 keeps working without new variables).
- [ ] AC5: Credentials (cookies, API keys, tokens) never appear in logs, error messages,
  the poll log or the stored raw payloads; a test asserts it for each adapter's error paths.

Storage

- [ ] AC6: Every fetched post is stored once, keyed by its X ID; fetching the same post again
  (another poll, another source, a restart) does not create a duplicate and does not change
  the moment it was first fetched.
- [ ] AC7: Stored posts keep: X ID, author handle, text, `created_at`, first-fetched moment,
  the adapter that first fetched it, and whether it is a repost or a reply; the raw payload
  is kept as JSON.
- [ ] AC8: The migration only adds new tables; `alembic upgrade head` and `downgrade` pass on
  the test container, and the existing collector and `job_run` tables are unchanged.

Polling in the worker

- [ ] AC9: The polling interval is 20 s from 90 min before the next deadline until that
  deadline, and 30 min at any other time, including when there is no future deadline;
  tested on the scheduling logic with a fake clock at the window boundaries (T-90 min − 1 s,
  T-90 min, deadline − 1 s, deadline).
- [ ] AC10: Tweet polling runs independently of the FPL jobs: while an FPL job runs (a
  simulated long deadline snapshot at T-5), polls continue at 20 s; tested with a fake
  source, a fake clock and a blocking fake job.
- [ ] AC11: A failed poll (network error, rate-limit response, source breakage) is recorded,
  does not stop the worker or the FPL jobs, and the next poll happens at the normal interval;
  on a rate-limit response the next poll waits at least as long as the source asks, when it
  says.
- [ ] AC12: Every poll leaves a record: source, start, end, outcome, number of new posts,
  error class on failure; the latest successful poll per source can be read with one query.
- [ ] AC13: `python -m app.worker status` shows the tweet ingest state: source, last
  successful poll, next poll, current mode (window / sparse).
- [ ] AC14: Shutdown (SIGTERM / Ctrl-C) stops the polling loop together with the worker
  within the existing shutdown bound of spec 002.

Latency measurement

- [ ] AC15: A measurement command polls every configured source side by side at a given
  interval for a given duration and writes one record per (source, post): X ID, author,
  `created_at`, first-fetched moment, detection latency in seconds.
- [ ] AC16: The command prints a summary per source: posts seen, p50, p95, max latency,
  failed polls; tested on a synthetic record set with known percentiles.
- [ ] AC17: A source whose credentials are missing is skipped with a message; the command
  still measures the others.
- [x] AC18 (manual, owner): before the merge, the owner runs the measurement locally for
  twscrape and twitterapi.io (official X API only if credits were bought) at the window
  interval (20 s) with ≥20 controlled posts published from the dedicated account on the
  watched list at known times; no real deadline window is required. The summaries are
  committed as a report under `docs/` in this PR.
- [x] AC19: An ADR records the default source chosen from the report and the measured p95
  detection latency; DECISIONS gets the row. If no candidate reaches p95 ≤ 60 s detection
  latency, the ADR says so and BACKLOG #2 is triggered.

Documentation

- [ ] AC20: `backend/.env.example`, `docs/DEPLOYMENT.md` and the README list the new
  variables (source selection, list ID, per-source credentials) with placeholders only, and
  describe how to run the ingest and the measurement.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| The latency spike and the ingest are one spec; the measurement is an acceptance criterion | a separate spike spec with throwaway code | extraction sits behind the interface and does not depend on the winner; throwaway code without tests breaks the iron rule and would be rewritten anyway |
| Three adapters: twscrape, twitterapi.io, official X API v2 | free scrapers only; scraper + official only | a fair comparison for the ADR; the official adapter makes BACKLOG #2 a configuration switch |
| Watched accounts as one public X List (ID in an environment variable) | a list of handles in configuration | one request per poll for all accounts, so rate limits and per-call costs do not grow with the number of accounts; the list is edited in X without a deploy; search `from:a OR from:b` has an indexing delay |
| Polling: every 20 s in the 90 min before a deadline, every 30 min otherwise | one or two polls just before the deadline (a digest); 10 min outside the window; polling only inside the window | only continuous polling in the window meets 60 s; outside the window only completeness matters and 30 min keeps each batch within one page, keeps pre-window context (press conferences) for RAG and source scoring |
| Tweet polling as a separate loop inside the existing worker process | the shared FPL job queue; a separate process / Railway service | FPL jobs (the T-5 snapshot) never delay a leak; still one process, one image, one Railway service |
| Detection latency (post → first fetch) is measured here; the e-mail leg in Stage 2 | the full post → inbox now | only this leg depends on the source; e-mail needs Stage 2's adapter |
| The 60 s p95 target covers detection only (post → first fetch); extraction and e-mail come on top | 60 s post → inbox (the previous PROJECT.md wording) | the owner's decision; the source and polling are what the target should hold accountable |
| Pre-merge measurement on controlled posts only; a real deadline window is observed later | waiting for a real deadline window before the PR | controlled posts give exact ground truth at any time; the PR does not wait for the fixture calendar |
| Measurement budget 0 PLN by default: twscrape (free) and twitterapi.io (trial credits); the official API measured only if the owner buys credits | a 10–20 PLN cap for all three | the owner's decision; the official API has no free credits for new accounts |

## Owner decisions

- New dependencies accepted up front: `twscrape` and a client library for the official X API
  v2 (tweepy or X's official Python SDK — the plan picks one and states why).
- Data migration accepted up front: new tables only (posts, poll log); applied locally and
  on the test container by the agent, on production by the Railway pre-deploy after the
  owner's merge.
- Measurement spend: 0 PLN by default; the owner may buy official X API credits to add it to
  the measurement.
- The owner creates one dedicated X account (never a private one): it is the scraper's
  login, owns the watched X List, and publishes the controlled test posts. The owner also
  registers a twitterapi.io key before the measurement.
- Implementation may start before the Stage 0 production deploy; everything runs locally.

## Open questions (non-blocking)

- Is the 90 min window long enough in practice, and does the latency hold under a real
  window's load? Observed in the first real deadline window after the merge and appended to
  the report; the constants can change later.
- Does running from a Railway datacenter IP change twscrape's behaviour (blocks, stricter
  limits) compared with the owner's machine? Observed after the first production deploy.
