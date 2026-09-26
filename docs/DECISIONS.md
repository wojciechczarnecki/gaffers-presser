# Design decisions

Binding architectural and process decisions. Append rows at the end (append-only) — then
parallel lanes merge trivially. Record a decision in the same PR in which it takes effect.

| Date | Decision | Rejected alternatives | Rationale |
|---|---|---|---|
| 2026-09-25 | The agentic workflow comes from the `pipeline` plugin; the pipeline's mechanics live in its README | a copy of the mechanics in the repository | a single source of truth, updated with `/plugin update` |
