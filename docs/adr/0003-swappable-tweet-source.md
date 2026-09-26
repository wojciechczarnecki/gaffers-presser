# 0003 — Swappable tweet source, free scraper first

Status: accepted (2026-09-26)

## Context

Team-news leaks come from X. Since February 2026 the official X API is pay-per-use
(about $0.005 per post read) with no free tier, which takes most of the 20 PLN monthly
budget. Free scrapers (twikit, twscrape — logged in with an X account) and cheap third-party
scraping APIs exist, but they are fragile and against X's terms of service. The owner needs a
leak in the inbox within 60 s in the final window before a deadline.

## Options

- **Free scraper** logged in with a dedicated X account: 0 PLN; can break or get the account
  banned.
- **Cheap third-party scraping API**: a few PLN a month; more stable, still a grey area.
- **Official X API**: about 15–30 PLN a month at our volume; compliant.

## Decision

A `TweetSource` interface with adapters. Start with a free scraper on a dedicated X account;
switch adapters by configuration. The latency of each candidate is measured before stage 1
builds on it.

## Consequences

- Ingest, storage and extraction never depend on a specific source.
- Scraper breakage is expected and must be detected (stage 5 failure alerting).
- Any paid product moves to the official API first (BACKLOG #2).
