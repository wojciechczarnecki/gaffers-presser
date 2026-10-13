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
      spec and before the deployment (settled by spec 010)
      (spec: [009](../specs/009-pre-deadline-alerts/SPEC.md); slot timing:
      [011](../specs/011-alert-slot-calendar/SPEC.md)) — **first functional MVP**
- [x] Off-list posts: the worker keeps the List's membership, posts quoted by a list member
      stay alert sources and count with the quote as one account, conversation context from
      outside the List is stored but never extracted or alerted, and a catch-up is no longer
      ended by an old quoted post; closes BACKLOG #11
      (spec: [010](../specs/010-off-list-posts/SPEC.md))
- [ ] Retrieval evaluation report on the GW6 window: the owner's review of set v1 extended
      with queries from the GW6 deadline window, the comparison of modes (full-text, vector,
      hybrid) and embedding models, the default-model ADR, a decision on reranking
      (BACKLOG #16), a results section in the README (tooling and set v1 come with spec 006);
      before the next item, because the corroboration judge reads retrieval's output
      (spec: TBD, planned as 016) — **portfolio MVP**
- [ ] Extraction and corroboration evaluation v2 on the GW6 window: real pre-deadline leaks
      in the extraction set, the rehearsal e-mail cases added before the prompt fixes, the
      model comparison re-run and the default model revisited (ADR 0006); `contradicts` cases
      in the corroboration judge set and the judge evaluation re-run on the retrieval chosen
      above (BACKLOG #12, #23) (spec: TBD, planned as 017)
- [ ] README rewritten for the portfolio once the two evaluations above have results: what
      it does and why, an architecture diagram, a results table linking the reports, the
      engineering process (specs, ADRs, evaluation, tracing), a sample presser on made-up
      data, the commands moved to a development guide (spec: TBD)
- [ ] LLM-written per-player alert summary with a faithfulness evaluation, deferred until
      the items above are done: it only summarises the facts already in the alert and neither
      chooses what or when to send nor changes grades (spec: TBD)

## Stage 3 — League presser

- [x] Post-gameweek presser per league in Polish FPL slang: a fact sheet computed by the
      database, the writer, the presser log, the worker trigger and the e-mail; best manager of
      the gameweek by net points, ties allowed; evaluation tooling with set v1
      (spec: [012](../specs/012-league-presser/SPEC.md))
- [x] GW ranks and a natural voice: winners, flops and season records judged by FPL's GW
      rank, the "🌍 Overall" section with threshold moves, the writer at temperature 0.8 with
      sparing slang and rewritten style examples, ranks rounded the way FPL players say them,
      evaluation set v2 with an inflection-error count
      (spec: [013](../specs/013-presser-ranks-and-voice/SPEC.md))
- [x] Match stories: "⚽ Z boiska" and "🃏 Chipy" sections, transfer wins and judged hits,
      key moments, rivalry swaps and podium changes, FPL team names, a 2500-character limit,
      evaluation set v3 (spec: [014](../specs/014-presser-match-stories/SPEC.md))
- [x] Full FPL match stats: points breakdown, decisive stats, a bonus missed by one BPS,
      hat-tricks and keeper walls, xG flags, evaluation set v4
      (spec: [015](../specs/015-presser-full-match-stats/SPEC.md))
- [x] Presser fixes from the owner's GW1–5 reading: a captain's breakdown in captain points,
      season counts and streaks only from their fields, "M" for millions, the judge flags
      threshold rounding, "różnica" in the glossary, evaluation set v5 (fast path, no spec)
- [x] Presser fixes from the owner's reading of the writer 7 pressers: one decisive stat in the
      owner's order of interest, a club's kept clean sheet and `against_xgc`, key moments sorted
      by their cause, captain and chip counts in the sheet, thresholds only from `entered` and
      `left`, the winner's and the flop's streak and record, declined names, a fixed title,
      evaluation set v6 (fast path, no spec)
- [x] Presser model comparison: eleven configurations judged in the Claude Code session,
      style rated blind with the owner's check on 3 pairs, report, ADR 0007 and the new
      `PRESSER_MODEL` default `openai/gpt-6-sol`; the session judge and its procedure
      (`docs/EVALUATION.md`) (BACKLOG #32; [report](reports/presser-model-comparison.md))
- [x] Writer prompt version 13 for `openai/gpt-6-sol`: FPL team names in the table and
      "team + owner" for the winner and the flop (`team_name` on each, fact sheet version 7,
      evaluation set v7), style examples to match, guards on ended streaks, ranks just past 1M,
      empty lists, counterfactuals, distinct captains and self-corrections; checked by reading
      6 dev pressers before and after (BACKLOG #35, fast path)
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
