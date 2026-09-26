# Design decisions

Binding architectural and process decisions. Append rows at the end (append-only) — then
parallel lanes merge trivially. Record a decision in the same PR in which it takes effect.

| Date | Decision | Rejected alternatives | Rationale |
|---|---|---|---|
| 2026-09-25 | The agentic workflow comes from the `pipeline` plugin; the pipeline's mechanics live in its README | a copy of the mechanics in the repository | a single source of truth, updated with `/plugin update` |
| 2026-09-26 | Decisions with real alternatives get an ADR in `docs/adr/`; this table stays the log and links to them | everything in this table; ADRs only | the table stays scannable, the reasoning has room |
| 2026-09-26 | Stack: Python 3.12, FastAPI, SQLModel, Alembic, PostgreSQL 16, uv; React 19 + TypeScript + Vite + Tailwind only when a web UI is needed (Wrapped); hosting on Railway | AWS + Terraform | the owner's known stack; AWS is already in the portfolio; Railway account exists |
| 2026-09-26 | LLM orchestration with LangGraph, typed outputs via Pydantic models — [ADR 0001](adr/0001-llm-orchestration-langgraph.md) | PydanticAI; plain SDK | branching workflow, checkpoints, market recognition |
| 2026-09-26 | PostgreSQL + pgvector, own hybrid retrieval (full-text + vectors, RRF); facts via SQL only — [ADR 0002](adr/0002-storage-and-retrieval-postgres-pgvector.md) | dedicated vector DB; framework retrievers | one database, retrieval we can evaluate and explain |
| 2026-09-26 | Swappable `TweetSource`, free scraper first — [ADR 0003](adr/0003-swappable-tweet-source.md) | official X API from day one | 20 PLN budget; switch by configuration |
| 2026-09-26 | E-mail as the MVP delivery channel — [ADR 0004](adr/0004-email-as-mvp-channel.md) | WhatsApp via Baileys now; Discord; Telegram | official and free; users are not on Discord/Telegram |
| 2026-09-26 | Team-news alerts come from X leaks, never from the official FPL injury flags | FPL `news` / `chance_of_playing` diffs | the flags are late and imprecise |
| 2026-09-26 | FPL data only through the unofficial public API, cached, with back-off | scraping the FPL site | the API is stable and complete enough |
| 2026-09-26 | LLM calls traced in Langfuse from the first stage that calls an LLM | observability as a late stage | eval and debugging need traces from the start |
| 2026-09-26 | LLM provider behind the LangChain chat-model interface; the cheapest model that passes the eval set wins | a fixed provider | budget; provider switch is configuration |
