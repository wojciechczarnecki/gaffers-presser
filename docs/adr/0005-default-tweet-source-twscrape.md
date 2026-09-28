# 0005 — twscrape as the default tweet source

Status: accepted (2026-09-28)

## Context

ADR 0003 made the tweet source swappable and required the latency of each candidate to be
measured before stage 1 builds on it. Spec 003 holds the ingest to p95 ≤ 60 s detection
latency (post on X → first fetch). On 2026-09-28 the owner measured twscrape and
twitterapi.io side by side at the 20 s window interval with 20 controlled posts
([report](../reports/tweet-source-latency-2026-09.md)); the official X API was not measured,
as no credits were bought.

## Options

- **twscrape** (free, a dedicated logged-in X account): 20 / 20 controlled posts detected,
  p95 25.9 s, max 26.8 s, 0 failed polls out of 125. Missed two posts of one real account
  that twitterapi.io returned — cause not yet known.
- **twitterapi.io** (paid per request): the free credits ran out early in the run
  (95 of 130 polls failed), so no controlled post was detected; three real posts at
  17.1–22.8 s. Continuous 20 s polling needs paid credits, which count against the
  20 PLN monthly budget.
- **Official X API**: not measured; about 15–30 PLN a month at our volume (ADR 0003).

## Decision

`TWEET_SOURCE=twscrape` is the default for the worker. The measured p95 detection latency
is **25.9 s** (20 controlled posts), within the 60 s target, so BACKLOG #2 is not triggered.

## Consequences

- Stage 1 and later stages build on twscrape at 0 PLN; the budget stays free for the LLM.
- The two posts twscrape missed are investigated before the ingest is relied on for alerts
  (BACKLOG #14); if twscrape systematically drops a class of posts, the choice is revisited.
- twitterapi.io stays a configured fallback; it has not been measured, so switching to it
  requires a measurement on paid credits first.
- The scraper's breakage and a banned account remain the main risk — stage 5 failure
  alerting, and BACKLOG #2 once the target is missed or before any monetization.
