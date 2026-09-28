# Backlog

Deferred improvements and technical debt. Every item has a priority and a **trigger** — the
condition that brings it back to work. An item without a trigger never comes back, so we
require one.

- **P1** — return at the next opportunity in this area
- **P2** — return when the trigger occurs
- **P3** — deliberately deferred; return only on the trigger

| # | Priority | Item | Trigger |
|---|---|---|---|
| 1 | P2 | Compare PydanticAI with LangGraph on one real flow (see ADR 0001) | building the Q&A agent (stage 4) |
| 2 | P2 | Switch the tweet source to the official X API (pay-per-use) | the free scraper breaks repeatedly, the 60 s target is missed, or before any monetization |
| 3 | P2 | A second phone number and an unofficial WhatsApp library (Baileys) for delivery | the e-mail + copy-paste MVP is in use and the owner has a second number |
| 4 | P2 | Members' consent flow before storing what they write in chats | any feature that reads group chats |
| 5 | P3 | Images (standings cards, Wrapped cards) and audio (TTS "press conference") | the text presser is in regular use |
| 6 | P3 | Rename the product (Polish or neutral brand, no FPL/PL trademarks) | before any public launch or monetization |
| 7 | P3 | Alert event types beyond the four in scope (position change, set pieces, goalkeeper change) | subscribers ask for them |
| 8 | P2 | Keep podcast transcription (Whisper, PyTorch) out of the production image — a separate package or dependency group | adding podcast transcripts (stage 4) |
| 9 | P1 | Run the pre-merge tweet-source latency measurement (M1, ≥ 20 controlled posts), write the latency report and ADR 0005 choosing the default source (spec 003, AC18/AC19), then tick the Stage 1 "Detection-latency measurement" item in the ROADMAP | before the tweet ingest is relied on in production |
| 10 | P3 | `tests/worker/test_cli.py::test_polls_continue_while_a_deadline_snapshot_blocks` (and, since spec 004, `::test_polls_continue_while_extraction_blocks`, seen locally in the final review of 004) intermittently emits a `PytestUnraisableExceptionWarning` — the SIGTERM handler's `Shutdown` lands inside a SQLAlchemy GC callback (seen before and after the final review of spec 003; the test passes) | the warning turns into a failure, or pytest starts running with `-W error` |
| 11 | P1 | Extend the extraction evaluation set with the GW6 deadline window (real pre-deadline line-up leaks, `confirmed_starter` / `benched` from real posts), re-run the comparison and revisit the default model (ADR 0006) | the GW6 deadline (2026-10-10) has passed with the tweet ingest running |
| 12 | P2 | Automatic fallback to a second LLM provider when the default one fails | extractions fail for a whole deadline window because of the provider |
| 13 | P1 | Follow-up spec to 004: review the evaluation set v1 (`reviewed: true`), fill `prices.toml`, run the comparison on the test split for at least one cheap model per provider, write `docs/reports/extraction-eval-v1.md` and ADR 0006, set the default model in `app/extraction/config.py` (spec 004 AC26/AC27, descoped by the owner) | before extraction is enabled in production (an LLM provider is chosen) |
