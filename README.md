# The Gaffer's Presser

[![CI](https://github.com/wojciechczarnecki/gaffers-presser/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/wojciechczarnecki/gaffers-presser/actions/workflows/ci.yml)

Team-news alerts and a post-gameweek press conference for Polish Fantasy Premier League
mini-leagues — built as a retrieval-augmented LLM system with evaluation and tracing.

> Status: early development. The roadmap below says what exists and what is planned.

## What it does

- **Team-news alerts.** Line-up leaks and injury news appear on X minutes before the FPL
  deadline, scattered across many accounts, while the official FPL flags lag behind. The
  system ingests posts from watched accounts, extracts who is out, doubtful, benched or
  starting, corroborates each leak against earlier posts, and e-mails an alert with cited
  sources — targeting 60 s from post to inbox.
- **Source credibility.** After each gameweek every leak is settled against who actually
  played, so each account gets a measured accuracy score.
- **League presser.** After each gameweek, a banter-filled summary of the mini-league in
  Polish FPL slang: the manager of the gameweek, the disasters and the season race.

## How it works

- **FPL collector** — players, fixtures, leagues, picks and post-gameweek ground truth from
  the public FPL API into PostgreSQL.
- **LangGraph flow** — extract (typed output) → link to FPL players → retrieve → corroborate
  → decide → notify.
- **Hybrid retrieval** — PostgreSQL full-text search and pgvector, merged with reciprocal
  rank fusion. Facts are queried with SQL, narrative is retrieved.
- **Evaluation and tracing** — every LLM step has an evaluation set; every call is traced in
  Langfuse.

Stack: Python 3.12, FastAPI, SQLModel, Alembic, PostgreSQL 16 + pgvector, LangGraph,
Langfuse, uv; hosted on Railway.

## Documentation

| Document | Contents |
|---|---|
| [docs/PROJECT.md](docs/PROJECT.md) | problem, requirements, architecture, risks |
| [docs/ROADMAP.md](docs/ROADMAP.md) | stages and their status |
| [docs/DECISIONS.md](docs/DECISIONS.md) and [docs/adr/](docs/adr/) | design decisions and their reasoning |
| [docs/CONVENTIONS.md](docs/CONVENTIONS.md) | code, tests, git |

## Development

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```

Running the application locally arrives with the FPL collector (stage 0).

The project is built with a spec-driven agentic workflow
([agentic-pipeline](https://github.com/wojciechczarnecki/agentic-pipeline)): every feature
goes from an approved spec through a reviewed plan and implementation to a pull request.

## License

Copyright © 2026 Wojciech Czarnecki. All rights reserved. The source is public for review as
a portfolio project; no licence to use, copy or distribute it is granted.

*Not affiliated with the Premier League or Fantasy Premier League.*
