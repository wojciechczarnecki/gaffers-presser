# CLAUDE.md

Instructions for agents working in this repository.

## Project

- **The Gaffer's Presser** — a post-gameweek press conference for your FPL mini-league: banter, team news and league history, in Polish FPL slang
- Owner: Wojciech Czarnecki (wczarnecky@gmail.com)
- Stack: Python (uv, ruff, pytest), CI on GitHub Actions; TODO: frameworks, database and hosting

## Document map — what to read when

| Document | When |
|---|---|
| `docs/PROJECT.md` | vision, functional requirements, architecture — at the start of work on a feature |
| `docs/ROADMAP.md` | stages and streams, statuses — at the start of every task; **update when done** |
| `docs/BACKLOG.md` | deferred improvements and debt: priority P1–P3 + a trigger to return |
| `docs/DECISIONS.md` | binding design decisions — before designing something differently |
| `docs/CONVENTIONS.md` | code style, tests, git — when writing code |
| `specs/` | SPEC + PLAN per feature — see the workflow below |

## Agentic workflow

The workflow comes from the `pipeline` plugin; the mechanics (statuses, the `RESULT`
contract, escalation triggers, metrics format) are described in its README — the single
source of truth. This project's configuration for the plugin lives in `.claude/workflow.json`.

```
/pipeline:idea (dialog)   → SPEC.md (spec-ready)   ← GATE 1: the owner approves the SPEC
/pipeline:ship NNN        → plan → plan review → implementation → review report
                                                   ← GATE 2: the owner decides on findings
                          → fixes, PR, green CI, done
merge PR                                           ← GATE 3: the owner
```

**Fast path** for small things (bugfix, docs, configuration — no new tables, endpoints
or dependencies): mini plan in the conversation → implementation → full verification →
`/code-review` on the diff → owner decisions → PR. When in doubt → full pipeline.

### Approvals

- `plan-approved` = approval of every edit within the plan's scope.
- Outside the plan and on the fast path: present the intent and get approval before editing.
- ALWAYS ask before: a migration on a database other than the test/development one;
  deleting files or data outside the plan's scope; adding a new dependency.

### Git — agents on working branches, `main` belongs to the owner

The agent creates the lane's branch, commits, pushes and opens the PR (`gh pr create`).
It updates the branch with `git merge origin/main` (not rebase). Out of the agent's reach:
commit, merge and push to `main` (only `git pull --ff-only`), merging PRs, force-push,
`reset --hard`, `clean -f`, `--no-verify`. The plugin's command guard and the `pre-push`
hook enforce it (enabled once per clone: `git config core.hooksPath scripts/git-hooks`).

## Iron rules

- Every feature MUST have tests — without them it is not done.
- TODO: the rule for user-facing texts (one place for strings).
- When a task is done, tick it in `docs/ROADMAP.md` — the roadmap must not lie.
- Record architectural decisions in `docs/DECISIONS.md` in the same PR.
- Secrets and real users' data never in code, logs, commits or tests.
- Production is out of reach of agent sessions — the owner performs operations on it.

## Commands

```bash
# full verification of the stack (the same command as `verify.command` in .claude/workflow.json)
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q

# TODO: running the application for development
# TODO: tests, lint, build per layer
```

## Structure

```
TODO: the project's directories and what lives in them
```
