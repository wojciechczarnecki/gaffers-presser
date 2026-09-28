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
| 9 | P1 | Run the pre-merge tweet-source latency measurement (M1, ≥ 20 controlled posts), write the latency report and ADR 0005 choosing the default source (spec 003, AC18/AC19) | before the tweet ingest is relied on in production |
