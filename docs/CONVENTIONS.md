# Development conventions

Binding conventions for code and process. The iron rules are summarised in CLAUDE.md;
this document holds the details.

## Language

- Commit messages, PR titles and branch names: English, regardless of `language`.
- Code: Python. Identifiers and comments: English. Product content (generated texts, e-mail
  templates, the slang glossary) is Polish
- Documentation (`docs/`, `specs/`) and PR descriptions: English (`language: "en"`
  in `.claude/workflow.json`).
- The language of the conversation with the agent is not a project setting — it follows the
  Claude Code session.
- No mixing of languages within one document.

## Code style

- Line length: 100 (`line-length = 100` in the ruff config in `pyproject.toml`)
- Formatting and lint: `ruff format` and `ruff check`, run through `uv` (the same commands as `format[]` in `.claude/workflow.json`)
- No docstrings; self-documenting code.
- Time: `datetime` values are timezone-aware UTC; conversion to `Europe/Warsaw` happens only
  when rendering content.
- Logs never carry manager names, league IDs, e-mail addresses or credentials.
- Comments only where the code cannot express a constraint.
- Layout: one directory per business module under `app/`; inside it, split a growing file into
  a package by subdomain, never one file per class (see DECISIONS, 2026-09-27).

## User-facing text

Product content is Polish and lives in files, never as string literals in code:

- `backend/app/content/` holds prompts, e-mail templates and the slang glossary, one file per
  artefact; code loads them by name.
- A change of tone or wording is a change of those files only.
- Tests check that a template renders with the expected fields; they do not compare whole
  generated or templated texts.

## Tests

**Every new feature MUST have tests.**

- Backend: pytest in `backend/tests/`, mirroring `app/`; shared fixtures in
  `backend/tests/conftest.py`.
- LLM steps are unit-tested against a fake model — no network and no cost in `pytest`.
  Evaluation sets run through a separate command, never inside `pytest` (a CI regression
  run comes in stage 5).
- External APIs (FPL, X, e-mail) are tested against recorded payloads. Payloads with
  managers or leagues are synthetic; public player data may be real.
- Tests that need PostgreSQL run against a container (locally and in CI), never a shared
  database.
- Agent sessions never reach live external services with the owner's credentials:
  `backend/.env` holds them, so a CLI check that could call X or another source runs with
  `env -u TWEET_SOURCE` or outside `backend/`.
- Write tests BEFORE or TOGETHER with the implementation.
- Minimum coverage: key paths + edge cases (authorisation errors, missing resource,
  validation, empty lists, duplicates, range boundaries).
- Specific assertions: where the content of a response matters, check the content.
- Full verification of the stack in one command:
  `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` (`verify.command`).

### Interface tests

None until a frontend exists (Wrapped).

## Commits and branches

- Commit types: `feat` / `fix` / `test` / `docs` / `refactor` / `chore`
- Imperative mood (`add`, `fix`, `harden`).
- One branch per task: `feat/NNN-<slug>`, `fix/...`, `chore/...`, `docs/...`
- PRs to `main` are **squash** merged; CI must be green before merge.
- Agent commits: after every green step, only the files of that step.
- Updating a branch: `git merge origin/main`, not rebase.

## Parallel work

Each lane gets its own branch and its own working directory (git worktree) in the directory
from `worktree.dir`. The agent takes the first spec number free across the specs directory,
branch names and worktrees.

## Workflow metrics

Every spec carries a flat `metrics:` block in its SPEC.md frontmatter, filled in by the
pipeline stages. Key format and the report: the `pipeline` plugin's README
(`workflow_metrics.py [specs-dir]`, `workflow_metrics.py --check <spec-dir>` — through
`PATH`, to which Claude Code appends the plugin's `bin/`).

## Dependencies

- Exact pins (`==`) in `backend/pyproject.toml`; `backend/uv.lock` committed. To upgrade:
  change the pin, run `uv lock`.
- Dev tools go to the `dev` optional-dependency group.
- Dependabot: monthly grouped minor/patch updates for uv and GitHub Actions; major versions
  are upgraded deliberately as their own task. Known vulnerabilities are caught by the
  weekly audit (`security.yml`) and by Dependabot security updates.
- A green Dependabot PR is merged after a look at the changelog; a red one gets a fix on a
  separate branch.
