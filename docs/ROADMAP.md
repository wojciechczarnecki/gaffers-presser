# Roadmap

Tick statuses only after green verification and a merged PR — the roadmap must not lie.

One stage at a time: a stage reaches its MVP and is merged before the next one starts.

**Definition of done for every stage that calls an LLM:** its calls are traced in Langfuse,
and each LLM step that produces data has an evaluation set with recorded results.

## Stage 0 — FPL data collector

- [x] Collector for players, teams, fixtures, gameweeks and the configured classic leagues
      (standings, managers, picks, transfers, chips); deadline snapshots; post-gameweek
      ground truth (starts, minutes, points) — on-demand CLI jobs and a backfill
      (spec: [001](../specs/001-fpl-collector/SPEC.md))
- [x] Worker running the jobs on a deadline-driven schedule, with a job run log, catch-up on
      start, a container image and Railway configuration
      (spec: [002](../specs/002-fpl-worker/SPEC.md))
- [ ] Production deployment on Railway: the owner follows the runbook and confirms the
      first deploy (spec: [002](../specs/002-fpl-worker/SPEC.md), AC21; after the owner's local
      test run of the alerts on the GW6 deadline, 2026-10-10, which needs BACKLOG #26 — the
      tweet ingest catch-up — merged first)

## Stage 1 — Tweet ingest and extraction

- [x] Swappable tweet source (twscrape, twitterapi.io, official X API) and ingest of the
      watched X List: storage, deadline-aware polling in the worker, measurement tooling
      (spec: [003](../specs/003-tweet-ingest/SPEC.md))
- [x] Detection-latency measurement (post → first fetch ≤ 60 s p95) and the default-source
      choice — the owner's run, report and ADR 0005 (spec 003 AC18/AC19, BACKLOG #9;
      [report](reports/tweet-source-latency-2026-09.md): twscrape, p95 25.9 s)
- [x] LangGraph extraction flow: player linking to FPL IDs, event type
      (out / doubt / benched / confirmed starter), certainty; Langfuse tracing; the
      extraction loop in the worker and the re-extraction CLI; evaluation tooling with the
      unreviewed set v1 (spec: [004](../specs/004-tweet-extraction/SPEC.md))
- [x] Extraction model comparison: review evaluation set v1, compare models through
      OpenRouter, report, ADR 0006, the default model and OpenRouter as the only provider
      (follow-up to spec 004, AC26/AC27; BACKLOG #14)
      ([report](reports/extraction-eval-v1.md);
      spec: [005](../specs/005-extraction-model-comparison/SPEC.md))

## Stage 2 — RAG v1 and alert e-mails (portfolio MVP)

- [x] Hybrid retrieval on PostgreSQL (full-text + pgvector, rank fusion)
      (spec: [006](../specs/006-hybrid-retrieval/SPEC.md))
- [x] Corroboration of leaks across independent accounts: retrieval plus SQL over the linked
      players; an LLM step, if the spec adds one, gets its own evaluation set
      (spec: [007](../specs/007-leak-corroboration/SPEC.md))
- [x] E-mail delivery adapter behind an interface (ADR 0004), shared with the Stage 3 presser
      (spec: [008](../specs/008-delivery-adapter/SPEC.md))
- [x] Pre-deadline alert e-mails with cited sources for league-owned and widely owned
      players, with post → inbox latency measured; whether the alert text is LLM-generated
      from the retrieved posts (with a faithfulness evaluation) is decided in its spec.
      The owner builds and tests it locally first (a rehearsal deadline with real e-mails);
      the production deployment (Stage 0) follows it, and BACKLOG #11 is settled after this
      spec and before the deployment
      (spec: [009](../specs/009-pre-deadline-alerts/SPEC.md)) — **first functional MVP**
- [ ] LLM-written per-player alert summary with a faithfulness evaluation, after the
      production deployment: it only summarises the facts already in the alert and neither
      chooses what or when to send nor changes grades (spec: TBD)
- [ ] Retrieval evaluation report after the GW6 window: the owner's review of set v1, the
      comparison of modes and embedding models, the default-model ADR, a results section in
      the README (tooling and set v1 come with spec 006) (spec: TBD) — **portfolio MVP**

## Stage 3 — League presser

- [ ] Post-gameweek presser per league in Polish FPL slang: summary, best manager of the
      gameweek (net points, ties allowed), banter; delivered by e-mail (spec: TBD)
- [ ] Slang glossary and style examples (from X and Polish FPL podcasts) (spec: TBD)

## Stage 4 — Source credibility and Q&A

- [ ] Settle leaks against the ground truth; per-account accuracy shown in alerts (spec: TBD)
- [ ] Q&A agent with cited sources, routing between SQL and retrieval (spec: TBD)
- [ ] Podcast transcripts as a knowledge source (spec: TBD)

## Stage 5 — Operational hardening

- [ ] Cost tracking against the monthly budget, with a warning before it is exceeded
      (spec: TBD)
- [ ] Evaluation regression in CI for prompt and model changes (spec: TBD)
- [ ] Failure alerting: a silent source, a failed job, a missed deadline window (spec: TBD)

## After the MVP

### Product

- [ ] Wrapped: season review for any team ID (web, React), free as a viral hook
- [ ] Multi-tenancy: any league, alert subscriptions
- [ ] Paid alerts (requires the official X API)

### Channels

- [ ] WhatsApp delivery (unofficial library on a second number)
- [ ] Messenger delivery
- [ ] Replying in the chat (requires members' consent)
