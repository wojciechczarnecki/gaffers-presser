# Development conventions

Binding conventions for code and process. The iron rules are summarised in CLAUDE.md;
this document holds the details.

## Language

- Commit messages, PR titles and branch names: English, regardless of `language`.
- Code: Python. Identifiers and comments: TODO: English or Polish
- Documentation (`docs/`, `specs/`) and PR descriptions: English (`language: "en"`
  in `.claude/workflow.json`).
- The language of the conversation with the agent is not a project setting — it follows the
  Claude Code session.
- No mixing of languages within one document.

## Code style

- Line length: 100 (`line-length = 100` in the ruff config in `pyproject.toml`)
- Formatting and lint: `ruff format` and `ruff check`, run through `uv` (the same commands as `format[]` in `.claude/workflow.json`)
- No docstrings; self-documenting code.
- Comments only where the code cannot express a constraint.

## User-facing text

TODO: one place for strings (file/module), the key rule, how the tests use them.

## Tests

**Every new feature MUST have tests.**

- TODO: where the tests of each layer live and what runs them
- Write tests BEFORE or TOGETHER with the implementation.
- Minimum coverage: key paths + edge cases (authorisation errors, missing resource,
  validation, empty lists, duplicates, range boundaries).
- Specific assertions: where the content of a response matters, check the content.
- Full verification of the stack in one command:
  `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` (`verify.command`).

### Interface tests

TODO: how to run the UI scope (`verify.scopes`), where the visual artefacts land and which
views must be looked at when a screen changes.

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

- TODO: version pinning, lock files, the update channel (Dependabot), the major policy.
