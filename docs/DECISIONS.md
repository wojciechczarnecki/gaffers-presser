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
| 2026-09-26 | Python lives in `backend/` as one package (`app/`) split into modules (`core`, `db`, `fpl`, `tweets`, `extraction`, `retrieval`, `alerts`, `delivery`, `presser`); separate processes are separate entry points of that package; a future web UI goes to `frontend/` | Python at the repository root; a uv workspace with a package per module | the modules share database models, configuration and one image; `frontend/` sits beside `backend/` cleanly; `app` survives a product rename |
| 2026-09-26 | One long-running worker with its own deadline-driven scheduler; no cron | Railway cron jobs | the final window needs polling every 20–30 s |
| 2026-09-26 | LLM and embeddings through provider APIs behind adapters; default LLM chosen in the stage 1 spec on the evaluation set | local models in production (bge-m3, Ollama) | local models need ~2 GB RAM on Railway; API embeddings cost pennies |
| 2026-09-26 | Environments: local Docker Compose (`pgvector/pgvector:pg16`) and Railway production; no staging; database tests on a PostgreSQL container | a shared development database; a staging environment | one owner, low risk; tests stay isolated |
| 2026-09-26 | Unit tests use a fake LLM; evaluation sets run separately from `pytest` | real LLM calls in tests | deterministic, free CI |
| 2026-09-26 | Hosting is budgeted separately from the 20 PLN/month external-API budget | one shared budget | the owner's decision |
| 2026-09-26 | No open-source licence: all rights reserved, source public for review | MIT; Apache-2.0; AGPL-3.0 | keeps the monetization option open; opening later stays possible, while a granted permissive licence cannot be withdrawn |
| 2026-09-26 | Database tests start their own PostgreSQL through testcontainers; Docker Compose serves only the development database | Compose for tests + a service container in CI | `verify.command` stays self-contained for agents and parallel worktrees; one definition of the test database |
| 2026-09-26 | FPL entities are keyed by (season, FPL ID) | FPL ID alone | FPL IDs reset every July |
| 2026-09-26 | FPL flags are kept as a change log (a baseline row plus a row on each change) with a full player snapshot per deadline; raw FPL payloads are archived only at deadline snapshots and results syncs | fixed snapshots only; archiving every response | an exact flag timeline to compare leaks against; raw data survives the season reset at a few MB per gameweek |
