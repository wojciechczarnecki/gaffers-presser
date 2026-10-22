# PLAN 017 — Retrieval evaluation run on the GW6 window and rolling out the winners

## Owner summary

- **Approach:** Phase A (set v2 with labels) was merged in PR #38, so this plan covers phase B.
  Three code changes come first. The evaluation gets one extra number, recall within the
  depth, which AC9 needs. The alert window rule is changed in `app/fpl/deadlines.py`
  (BACKLOG #53), and the alerts, the ingest catch-up and the slice builder all use it. Then the
  agent makes one `evaluate` run per embedding model on a scratch container: test and dev,
  each with `--max-cost-usd 0.10`, about $0.05 in all. The winner is picked by AC7's rule,
  applied exactly. Last come the winner in production (`app/corroboration/sources.py`, and
  `DEFAULT_EMBEDDING_MODEL` if the model changes), the report, ADR 0009 and the documents.
- **Main risks:**
  - The winner is unknown until the runs finish, so step 5 is written for any outcome. Two
    outcomes the SPEC's tie rule does not settle stop the run with an escalation: a full-text
    winner, whose model would then be picked only by price, and a tie that only the mode can
    break.
  - AC9 needs one extra column in the spec 016 tooling. It is additive, and the plan treats it
    as the "blocks the run" exception.
  - The new floor never comes after the moment it is computed for: only the previous
    gameweek's matches that have ended (kickoff + 2 h) by then count, and with none ended the
    floor is the previous deadline (owner decision 2026-10-22). While N−1's matches are still
    being played, the window of one deadline therefore moves forward as matches end; see
    Risks.
- **New dependency:** no.
- **Data migration:** no. `post_embedding.embedding` has no fixed dimension, and embeddings
  are keyed by (post, model). Alembic runs only on the scratch container.
- **Manual scenarios for the owner:** 1, and only if the embedding model changes: re-index
  the Compose database and confirm before the merge (AC8).

## Approach

**What was read and how:**

- `specs/017-retrieval-evaluation-run/SPEC.md` — read in full, including `## Owner decisions`.
- `docs/CONVENTIONS.md` — read in full.
- `docs/EVALUATION.md` — searched for "retrieval procedure" and "scratch". I read the whole
  section "The retrieval procedure" (lines 89–204): the commands, scratch databases, strata,
  the result header, and the wrong `--until 2026-10-10T18:30` example.
- `docs/DECISIONS.md` — searched for "window_floor", "2026-10-07", "ADR", "retriev". This
  found the 2026-10-07 ingest row (the "previous real deadline" part that #53 supersedes), the
  2026-09-29 retrieval rows (RRF `k = 60`, depth 50) and the ADR convention row.
- `docs/BACKLOG.md` — searched for rows 16, 17, 50, 53, 54, 55, "free" and "Q&A". Row #16 is
  P2 with the trigger "the retrieval evaluation report". Row #50 covers docstrings plus the stale
  comment in `alerts/config.py:55`. Row #53 is P1. The highest number in use is 55.
- `docs/ROADMAP.md` — searched for "retrieval", "Stage 2" and "Q&A". This found the open
  Stage 2 item (lines 66–78) and the Stage 4 item "Q&A agent with cited sources" (line 133).
- `docs/DEPLOYMENT.md` — searched for "EMBEDDING_MODEL" and "index". Line 97 names the
  default model, and lines 110–111 give the re-index command.
- `README.md` — searched for "retriev" and the headings. There is a "Retrieval" paragraph and
  a command block under `## Development` (lines 167–188), and no results section yet.
- `docs/adr/README.md` and `docs/adr/0006-default-extraction-model-openrouter.md` — read for
  the ADR format (Status, Context, Options, Decision, Consequences).
- Phase A's merge commit `78e82b6` (PR #38) — its message holds the label counts, strata,
  judge estimates, token spend and the judge-choice deviation, all of which the report needs.
- Code read in full: `app/corroboration/sources.py`, `app/retrieval/config.py`,
  `app/fpl/deadlines.py`, `app/retrieval/variants.py`, `app/alerts/service.py`,
  `app/tweets/loop.py`, `app/retrieval/evaluation/{runner,player_metrics,compare,players}.py`.
- Code read in part: `app/alerts/config.py` (lines 40–75), `app/retrieval/search.py`
  (`search` signature and defaults), `app/retrieval/evaluation/cli.py` (`evaluate`),
  `app/retrieval/evaluation/schema.py` (`load_eval_corpus`), `app/tweets/sources/paging.py`
  (`collect_new` and the floor) and `app/alerts/breaking.py` (the window call).
- Tests read: `tests/fpl/test_deadlines.py`, `tests/corroboration/test_sources.py` (variant
  and constant tests), `tests/alerts/test_slots.py` (window tests),
  `tests/tweets/test_loop.py` (floor tests), `tests/retrieval/evaluation/test_players.py`
  (window tests) and `tests/test_docs.py` (lines 1–32 and 140–185).

**Precondition (state on the branch).** `backend/evals/retrieval/v2/` holds `set.json`
(`"version": "v2"`), `corpus.jsonl` (2557 posts), `queries.jsonl` and `strata.json`.
`prices.toml` has the three candidate rows: qwen $0.01, 3-large $0.13 and gemini $0.15 per
million tokens, against the baseline's $0.02. The labels are 13,906 Opus pairs
(`claude-opus-5-5 (session)`), 769 Sonnet pilot pairs and 139 owner pairs.

**Reference judge.** The reference judge is `claude-opus-5-5 (session)`: only Opus labelled
the whole `player` slice. On its final pilot numbers, AC4's rule points to Sonnet. The data PR
recorded keeping Opus as a deviation ("owner to confirm"), and the owner merged that PR. The
report states this. It is not reopened here, but the confirmation is not in `## Owner
decisions`, so the PR description lists it under "for the owner to confirm" at the final
review gate.

**1. Recall within the depth (AC9).** AC9's second number is the recall of the unclaimed
relevant posts within the top 50, with claimed posts dropped and no cut to 10. The tooling
does not compute it, and a result file keeps only the cut and the top 10 per configuration,
so the number cannot be derived afterwards.

- Variant A, chosen: add `recall_at_depth` beside `recall_at_cut` in `QueryNumbers` and
  `PlayerAggregate`, in the JSON row and in the `evaluate` and `compare` tables. It is defined as
  |ranked[:depth] ∩ unclaimed relevant| / |unclaimed relevant|. AC9 reads the depth-50 row of
  the winner's (model, mode, variant).
- Variant B, rejected: a one-off scratch script that searches again. It is not reproducible
  from the committed result files, and the report's number could not be checked.

The change is additive: no existing number changes, and `compare` reads older files with
`.get`. It is the SPEC's "blocks the run" exception, because without it AC9 cannot be
delivered. As the SPEC's out-of-scope list requires for such a change, step 1 records it as a
`minor` entry in `## Deviations`.

**2. The alert window (AC12, AC13).**

- Variant A, chosen: keep `window_floor` pure and change what its first argument means.
  - It was the previous deadline and becomes the previous gameweek's end.
  - A new DB helper in `app/fpl/deadlines.py` computes that end:
    `previous_gameweek_end(session: Session, as_of: datetime, gameweek_at: datetime | None = None) -> datetime | None`.
  - The helper takes the gameweek with the latest `deadline_at` at or before `gameweek_at`
    (default `as_of`), the same choice `deadline_at_or_before` makes. Among that gameweek's
    fixtures with a non-null `Fixture.kickoff_at`, it keeps those that ended by `as_of`
    (`kickoff_at + MATCH_LENGTH <= as_of`, owner decision 2026-10-22) and returns the latest
    such end. The fixtures are matched on both `Fixture.season` and `Fixture.gameweek_fpl_id`
    of that gameweek, because the database holds more than one season.
  - A gameweek with no ended fixture — none with a kickoff, or none finished by `as_of` —
    returns its deadline (the fallback). No gameweek returns `None`. The result is therefore
    never later than `as_of` when `gameweek_at` is left at its default.
  - `gameweek_at` exists only for the slice builder, which picks gameweek N−1 by its deadline
    but counts matches ended by N's deadline. Passing `previous_deadline` as `as_of` there
    would count no match at all (none has ended at N−1's own deadline).
- Variant B, rejected: give `window_floor` a `Session`. That would turn its pure tests into
  DB tests and push a query into a function that is pure today.

Why the edge cases need no extra code:

- A blank N−1 drops out naturally, because a postponed fixture carries a later
  `gameweek_fpl_id`.
- A rehearsal deadline is never a `Gameweek` row, so the last real gameweek counts, as today.
- Before GW1 the helper returns `None`, and `window_floor` gives the deadline minus the
  lookback.

Call sites:

- `alerts/service.py::alert_window_start`. Today it feeds `window_start(session, as_of)` from
  corroboration. It switches to the helper, so `alerts` no longer imports `window_start`.
  Corroboration's own CLI default (`window_start`, the latest deadline) is not part of the
  alert window and stays.
- `tweets/loop.py` uses the helper instead of `deadline_at_or_before`.
- The `alerts` rule in `retrieval/evaluation/players.py` feeds
  `previous_gameweek_end(session, deadline, gameweek_at=previous_deadline)` (or `None` when
  there is no previous deadline) into `window_floor`, with `deadline` (N's) as the moment.
  `MATCH_LENGTH` moves to `app/fpl/deadlines.py`, and `players.py` imports it.

**3. The run.** `evaluate` drops and recreates the `retrieval_eval` schema in the database
`DATABASE_URL` names. It therefore runs only against a throwaway `pgvector/pgvector:pg16`
container on port 55432, with `DATABASE_URL` set on every command. A variable set in the
environment overrides `backend/.env` in pydantic-settings. Alembic runs on that container
only, so the `vector` extension and the `public` tables exist.

**4. The winner (AC7).** Step 5 applies the rule exactly from the JSON values, not from the
3-decimal table:

1. Take the highest `recall_at_cut`.
2. If production's configuration (`openai/text-embedding-3-small`, hybrid, `web_name`, 20) is
   among the tied rows, it wins.
3. Otherwise the tie breaks on the cheaper model (qwen 0.01 < 3-small 0.02 < 3-large 0.13 <
   gemini 0.15), then the smaller depth, then the variant in the order `web_name`, `full_name`,
   `names`.

Production then gets a `SEARCH_MODE` constant beside `QUERY_VARIANT` and `SEARCH_LIMIT`, and
`retrieval_candidates` passes it to `search`. The constant is added even if the mode stays
hybrid, so that one test pins all three values.

Patterns reused:

- the `_recording_search` monkeypatch and the `RecordingTracer` in
  `tests/corroboration/test_sources.py`;
- `seed_reference` (`tests/corroboration/helpers.py`) and `add_fixture`
  (`tests/retrieval/evaluation/test_players.py`) for seeded calendars;
- `_seed_gw5_and_gw6` and `_floors_of_one_poll` in `tests/tweets/test_loop.py`;
- the `_rows()` helper in `tests/test_docs.py` for the document checks.

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | — | n/a — delivered by phase A (PR #38, `78e82b6`); step 4 checks the set exists before the run |
| AC2 | 4, 6 | n/a — price rows came in phase A; step 4 passes `--max-cost-usd 0.10` on every run and step 6 records the spend in the report |
| AC3 | — | n/a — delivered by phase A (pooling and judging) |
| AC4 | 6 | n/a — delivered by phase A; step 6 copies the pilot numbers into the report |
| AC5 | — | n/a — delivered by phase A (labels committed, data PR merged) |
| AC6 | 1, 4 | `tests/retrieval/evaluation/test_runner_v2.py` and `test_compare.py` (row and column); the four committed files, plus the `compare` run in step 4 and in the end-to-end check |
| AC7 | 5, 6 | n/a — the SPEC waives a test: the rule is applied by reading the result files, and step 6 shows it in the report |
| AC8 | 5 | `tests/corroboration/test_sources.py::test_corroboration_searches_with_the_evaluated_winner`; `tests/retrieval/test_config.py` if the model changes; the existing `tests/corroboration` and `tests/alerts` suites |
| AC9 | 1, 6, 8 | `tests/retrieval/evaluation/test_player_metrics.py` (`recall_at_depth`); `tests/test_docs.py::test_backlog_has_reranking_and_ann_entries` (#16's priority by the rule) |
| AC10 | 6 | `tests/test_docs.py::test_retrieval_report_v2_has_every_section` |
| AC11 | 7 | `tests/test_docs.py::test_adr_0009_and_decisions_row_for_the_retrieval_winner` |
| AC12 | 2, 3 | `tests/fpl/test_deadlines.py` (each calendar case); `tests/alerts/test_slots.py::test_alert_window_starts_after_the_previous_gameweeks_last_match`; `tests/tweets/test_loop.py::test_the_catch_up_floor_equals_the_alert_window_start`; `tests/retrieval/evaluation/test_players.py` (alerts rule) |
| AC13 | 2, 3, 7, 8 | `tests/test_docs.py::test_backlog_window_row_and_017_018_renumbering` (no #53 row; #50 without the comment part); `tests/test_docs.py::test_decisions_supersede_the_previous_deadline_window` |
| AC14 | 8 | `tests/test_docs.py::test_readme_results_free_slice_row_and_roadmap` |

## Steps

- [x] 1. **Recall within the depth in the evaluation (AC9 number, AC6 column).** Files:
      `backend/app/retrieval/evaluation/player_metrics.py`, `runner.py` (`_format_row`,
      `format_player_table`), `compare.py`, tests
      `backend/tests/retrieval/evaluation/test_player_metrics.py`, `test_compare.py` and
      `test_runner_v2.py`.
      - Write these tests first:
        - 15 unclaimed relevant posts in the top 20, plus 2 claimed relevant posts:
          `recall_at_cut` 10/15 and `recall_at_depth` 15/15.
        - Depth 20 against depth 50 when a relevant post sits at rank 30: the depth-20 number
          misses it, the depth-50 number counts it.
        - With no unclaimed relevant post, the value is `None` and stays out of the mean.
        - The runner's row has `recall_at_depth`.
        - `compare` prints a `recall@depth` column, and an older result file without the
          key prints `-`.
      - Then add the field: `recall_at_depth = |set(ranked[:depth]) ∩ unclaimed| /
        |unclaimed|`, or `None` when nothing is unclaimed. Aggregate it with `_mean` like
        `recall_at_cut`.
      - Record the change in `## Deviations` as `minor` (the SPEC's "blocks the run" exception
        to "no change to the spec 016 tooling").
      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/evaluation/test_player_metrics.py tests/retrieval/evaluation/test_compare.py tests/retrieval/evaluation/test_runner_v2.py && uv run ruff check app/retrieval/evaluation tests/retrieval/evaluation`
      `iterations: 1`
- [x] 2. **`previous_gameweek_end` and the new `window_floor` rule (AC12 core, AC13
      docstring).** Files: `backend/app/fpl/deadlines.py` and
      `backend/tests/fpl/test_deadlines.py`.
      - Tests first, each with a seeded calendar of `Gameweek` and `Fixture` rows (add a
        local `add_fixture` like the one in `test_players.py`). Each asserts
        `window_floor(previous_gameweek_end(session, as_of), next_deadline, 7 days, as_of)`:
        - **A normal week:** N−1 fixtures at Sat 14:00 and Mon 19:00. The window starts on
          Mon 21:00, after the previous deadline.
        - **A blank N−1:** the Mon fixture is moved to gameweek N. The Sat match plus 2 h
          counts.
        - **An international break:** the last N−1 match is 14 days before the deadline. The
          deadline minus 7 days wins.
        - **Before GW1:** with no gameweek before, the helper returns `None` and the window is
          the deadline minus the lookback.
        - **No N−1 fixtures**, or only fixtures with a null kickoff: the previous deadline.
        - **Another season:** a fixture with the same gameweek number in another season does
          not count.
        - **A match not yet ended by `as_of`** (owner decision 2026-10-22): N−1 fixtures at
          Sat 14:00 and Mon 19:00, `as_of` Mon 20:00. Only the Sat match counts, and the
          window starts on Sat 16:00, not after `as_of`.
        - **No N−1 match ended by `as_of`:** `as_of` between N−1's deadline and its first
          match's end. The floor is N−1's deadline.
        - **`gameweek_at`:** `previous_gameweek_end(session, N's deadline,
          gameweek_at=N−1's deadline)` gives N−1's last match plus 2 h.
        - Across all cases above with the default `gameweek_at`, the result is never later
          than `as_of`.
      - Then add `MATCH_LENGTH = timedelta(hours=2)` and `previous_gameweek_end`.
      - Rename `window_floor`'s first parameter to `previous_end`, and rewrite its docstring
        for the new rule. AC13 asks for the docstring explicitly. The existing pure
        `window_floor` tests stay, with their wording updated where it says "previous
        deadline".
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_deadlines.py && uv run ruff check app/fpl tests/fpl`
      `iterations: 0`
- [x] 3. **The alerts, the ingest catch-up and the slice builder on the new rule (AC12,
      AC13 comments).** Files: `backend/app/alerts/service.py`, `backend/app/alerts/config.py`,
      `backend/app/tweets/loop.py`, `backend/app/retrieval/evaluation/players.py` and the tests
      listed below.
      - Tests first:
        - `tests/alerts/test_slots.py::test_alert_window_starts_after_the_previous_gameweeks_last_match`:
          seed GW5 fixtures that all ended before `NOW`. `alert_window_start` for `REAL` and
          for `REHEARSAL` gives the last kickoff plus 2 h. The rehearsal uses the last real
          gameweek.
        - `tests/tweets/test_loop.py::test_the_catch_up_floor_equals_the_alert_window_start`:
          seed GW5 and GW6 with GW5 fixtures. The floor of one poll at moment T equals
          `alert_window_start(session, T, <AlertDeadline of GW6>, AlertConfig(...,
          max_lookback=<the poller's lookback, 5 days in _floors_of_one_poll>))`, and both equal the last kickoff plus 2 h (T after that
          end).
        - `tests/retrieval/evaluation/test_players.py`: rename and update
          `test_a_lineup_before_the_last_match_is_stale_under_alerts_and_outside_after_last_match`.
          Under `alerts` the window now starts at `LAST_END`, so the line-up claim is outside
          it. Add `test_alerts_window_follows_the_production_floor`: with fixtures the start
          is the last match plus 2 h, and without them it is the previous deadline.
          `test_alerts_window_starts_at_the_previous_deadline` (no fixtures) stays as it is.
        - One test per scenario of the owner decision of 2026-10-22:
          - `tests/alerts/test_slots.py::test_the_digest_before_a_midweek_deadline_counts_only_ended_matches`:
            GW5 fixtures on Sat and on Mon evening, `as_of` the D-1@20:00 slot before a
            Tuesday deadline, earlier than the Mon match's end. `alert_window_start` for
            `REAL` gives the Sat match plus 2 h, and the start is not after `as_of`.
          - `tests/alerts/test_slots.py::test_a_rehearsal_during_matches_counts_only_ended_matches`:
            `as_of` during GW5's matches, the same check for `REHEARSAL`; with `as_of` before
            the first match ends, the start is GW5's deadline.
          - `tests/tweets/test_loop.py::test_the_catch_up_during_matches_stops_at_the_previous_deadline`:
            GW5 fixtures, `now` before the first GW5 match ends. The floor of one poll is GW5's
            deadline (as before this change), not a time after `now`.
      - Then:
        - `alert_window_start` returns `window_floor(previous_gameweek_end(session, as_of),
          deadline.deadline_at, config.max_lookback, as_of)`. Drop the `window_start` import,
          and replace the docstring with a comment stating the new rule.
        - Fix the comment at `alerts/config.py:55`: the window counts back from the
          deadline, and it starts after the previous gameweek's last match.
        - In `tweets/loop.py` the floor uses `previous_gameweek_end(session, now)`. Update its
          comment.
        - In `players.py` the `alerts` rule uses `previous_gameweek_end(session, deadline,
          gameweek_at=previous_deadline) if previous_deadline else None`, and `MATCH_LENGTH`
          is imported from `app.fpl.deadlines`.
      - The existing alert, corroboration and tweet tests seed no fixtures, so they take the
        previous-deadline fallback and must pass unchanged.
      Automatic verification: `cd backend && uv run pytest -q -n auto tests/fpl tests/alerts tests/tweets tests/corroboration tests/retrieval/evaluation/test_players.py tests/worker && uv run ruff check app tests`
      `iterations: 1`
- [x] 4. **The evaluation runs (AC6, AC2 cap).** Files:
      `backend/evals/retrieval/results/test-v2-{openai-text-embedding-3-small,qwen-qwen3-embedding-8b,openai-text-embedding-3-large,google-gemini-embedding-001}.json`
      (committed). The dev runs go to `results/dev/`, which is gitignored, and are kept
      locally.
      1. Check the precondition:
         `python3 -c "import json;print(json.load(open('backend/evals/retrieval/v2/set.json'))['version'])"`
         prints `v2`, and `grep -c '"labelled_by":"claude-opus-5-5 (session)"' backend/evals/retrieval/v2/queries.jsonl`
         is greater than 0.
      2. Start the scratch database:
         `docker run -d --rm --name presser-eval-017 -e POSTGRES_USER=eval -e POSTGRES_PASSWORD=eval -e POSTGRES_DB=eval -p 55432:5432 pgvector/pgvector:pg16`
         and wait for `docker exec presser-eval-017 pg_isready -U eval`.
         Then run `cd backend && DATABASE_URL=postgresql+psycopg://eval:eval@localhost:55432/eval uv run alembic upgrade head`.
         This is a test container, never the Compose database on 5432.
      3. For each MODEL in `openai/text-embedding-3-small`, `qwen/qwen3-embedding-8b`,
         `openai/text-embedding-3-large` and `google/gemini-embedding-001`, and each SPLIT in
         `test` and `dev`, run in the background:
         `cd backend && DATABASE_URL=postgresql+psycopg://eval:eval@localhost:55432/eval uv run python -m app.retrieval.evaluation evaluate --split SPLIT --reference-judge "claude-opus-5-5 (session)" --embedding-model MODEL --max-cost-usd 0.10`.
         Run them one at a time, because every run recreates the same schema. Save each
         printed estimate line to the scratchpad for the report.
      4. Check every test file's header:
         - `set_version` is `v2` and `split` is `test`;
         - `reference_judge` is the Opus name;
         - `judge_owner_estimates` is not null;
         - `depths` is `[20, 30, 50]`;
         - every row has `recall_at_depth`;
         - the player counts give 188 queries and 119 with no unclaimed relevant post.
         Note every configuration whose `unjudged_share` is above 0.
      5. Run `compare` on the four test files and save its output for the report.
      6. Stop the container with `docker stop presser-eval-017`.
      7. Commit only the four test files:
         `data: add retrieval evaluation results on set v2 (spec 017)`.
      A failed run (a cost refusal or an embedding error) goes into the self-correction loop
      on its cause. It never goes around `--max-cost-usd`.
      Automatic verification: `cd backend && uv run python -m app.retrieval.evaluation compare evals/retrieval/results/test-v2-*.json` prints 4 files with depths 20,30,50 and 108 player rows (4 models × 3 modes × 3 variants × 3 depths)
      `iterations: 0` (qwen dropped by the owner decision of 2026-10-22: 3 files and 81 player rows; the three test files and their dev runs ran at the scratch container, the baseline files were reused from the earlier attempt: valid, `git_commit` f70b9db, clean)
- [x] 5. **Pick the winner and put it into production (AC7, AC8).** Files:
      `backend/app/corroboration/sources.py`, `backend/app/retrieval/config.py` (only if the
      model changes), `backend/tests/corroboration/test_sources.py` and
      `backend/tests/retrieval/test_config.py` (only if the model changes).
      - Apply the rule in "Approach" (4) with a scratch script, for example
        `$SCRATCH/winner.py`, that reads the four test files. Compare `round(recall_at_cut, 9)`.
        Print the tied set and the winner, and keep the output for the report.
      - **Stop with an escalation (decision)**, and do not edit production, if either of these
        holds:
        - The winner's mode is `fulltext`. Full-text rows are equal across models, so the
          model would then be chosen by price alone, which AC11's ADR cannot justify.
        - Rows tie after model, depth and variant, so that only the mode separates them. The
          SPEC's rule does not order the modes.
      - Tests first:
        - Add `test_corroboration_searches_with_the_evaluated_winner`. It monkeypatches
          `sources.search` with a recorder of `(query, mode, limit)`. For a player whose
          names differ by variant (`GABRIEL_MAGALHAES`), it asserts the literal winner
          values: the variant's text, the mode and the depth as the limit.
        - Update only the assertions that name the old value:
          `test_query_variant_constant_defaults_to_web_name` (rename it for the new constant
          if the variant changes), and `search["mode"] == "hybrid"` in
          `test_candidates_search_uses_the_short_timeout_and_the_window_filters` if the mode
          changes.
        - If the model changes, update the literal in `tests/retrieval/test_config.py:28`.
      - Then:
        - Set `QUERY_VARIANT`, `SEARCH_LIMIT` and the new `SEARCH_MODE: Mode = "<mode>"` in
          `sources.py`, and pass `SEARCH_MODE` to `search`. `MAX_JUDGED` stays 10.
        - If the model changes, set `DEFAULT_EMBEDDING_MODEL`.
        - Leave `README.md` line 167 and `DEPLOYMENT.md` line 97 for step 8.
      Automatic verification: `cd backend && uv run pytest -q -n auto tests/corroboration tests/alerts tests/retrieval && uv run ruff check app tests`
      `iterations: 1` (winner: `openai/text-embedding-3-large`, hybrid, `names`, depth 20, recall at the cut 0.9333, a three-way tie across depths 20/30/50 settled by the smaller depth; `tests/retrieval/test_cli.py:150` also named the old default model)
- [x] 6. **The report `docs/reports/retrieval-eval-v2.md` (AC7, AC9, AC10, AC2 spend).**
      Files: the new report and `backend/tests/test_docs.py`.
      - Test first: `test_retrieval_report_v2_has_every_section`. The report exists and
        contains these headings or phrases: "Set", "Labelling", "Relevance", "Tokens and
        spend", "compare", "Winner", "Misses", "Reranking", "Stale", "Limits", "69", "0.05",
        `claude-opus-5-5`.
      - Write the report from these sources:
        - **The set:** `set.json`, `corpus.jsonl` line count and `queries.jsonl`, with queries
          per split and gameweek and the `with_initial` count from the header.
        - **The labelling:** the pilot, both judges' weighted precision, recall and kappa,
          their kappa with each other (0.957), the strata table with review rates, the label
          counts and the judge-choice deviation. All come from commit `78e82b6` (`git show
          78e82b6`) and the header's `judge_owner_estimates`.
        - **The relevance distribution:** relevant posts per query (min, median, max), the
          share of relevant pairs, and the 119 test queries with no unclaimed relevant post.
          A scratch script over `queries.jsonl` with the Opus and owner reference labels
          computes them.
        - **Tokens and spend:** phase A's session tokens (about 5.8M; pilot 0.48M, Opus rest
          3.98M, re-judges 1.33M, accepted 2026-10-22). Paid embedding spend: phase A about
          $0.08, plus phase B, the sum of the eight runs' `estimated_embedding_cost_usd`,
          labelled as an estimate.
        - **The `compare` table** from step 4, its `player` part only. `compare` also prints
          the `free` slice's table (v1 labels); the SPEC keeps the `free` slice out of the
          report, so that part is left out, and the report says so in one line.
        - **The winner:** the tied set and the rule applied, from step 5.
        - **Misses:** at least the five worst test queries for the winner's configuration,
          from the file's `queries` details (relevant ids not in `cut`).
        - **Reranking:** the winner's `recall_at_cut`, and `recall_at_depth` from the
          depth-50 row of the same model, mode and variant. Apply AC9's two conditions, and
          state the decision.
        - **Stale counts per gameweek:** stale claims and stale anchors per gameweek, from
          `queries.jsonl`. GW6 shows 0 because of the break; GW7 shows 121 of 332 claims.
        - **The limits:** as AC10 lists them. Also state any non-zero unjudged share.
        - **The noise:** differences below about 0.05 on 69 queries are within noise.
      Automatic verification: `cd backend && uv run pytest -q tests/test_docs.py`
      `iterations: 0`
- [x] 7. **ADR 0009 and the DECISIONS rows (AC11, AC13 DECISIONS).** Files: new
      `docs/adr/0009-default-embedding-model.md`, `docs/DECISIONS.md` and
      `backend/tests/test_docs.py`.
      - Tests first:
        - `test_adr_0009_and_decisions_row_for_the_retrieval_winner`: the ADR exists and
          names all four models. A DECISIONS row links `adr/0009-` and names
          `QUERY_VARIANT`, the mode and `SEARCH_LIMIT`, each with its value.
        - `test_decisions_supersede_the_previous_deadline_window`: a row names
          `window_floor`, "last match" and "supersedes".
      - Then write the ADR:
        - **Status:** accepted.
        - **Context:** set v2 and the rule.
        - **Options:** a table of the four models, each with its price and its best recall at
          the cut, and its mode, variant and depth.
        - **Decision:** the model chosen or kept.
        - **Consequences:** a re-index if the model changes, and the noise.
      - Then add two DECISIONS rows dated the day of the step:
        - The embedding model and the production query, linking ADR 0009, with the
          reranking decision.
        - The alert window: the later of the previous gameweek's last kickoff plus 2 h and the
          deadline minus the lookback, counting only the matches ended by the moment the
          window is computed for (none ended → the previous deadline; owner decision
          2026-10-22), with the fallbacks, superseding the "previous real deadline" part of
          the 2026-10-07 row. The per-club window is the rejected
          alternative, as in the SPEC.
      Automatic verification: `cd backend && uv run pytest -q tests/test_docs.py`
      `iterations: 0`
- [x] 8. **BACKLOG, ROADMAP, EVALUATION.md, README and DEPLOYMENT (AC9 backlog, AC13,
      AC14).** Files: `docs/BACKLOG.md`, `docs/ROADMAP.md`, `docs/EVALUATION.md`, `README.md`,
      `docs/DEPLOYMENT.md` (only if the model changed) and `backend/tests/test_docs.py`.
      - Tests first:
        - Update `test_backlog_has_reranking_and_ann_entries`: the reranking row is ` P1 `
          with "spec 017's report" when needed, or ` P3 ` with "a later retrieval
          evaluation" when not, by step 6's result.
        - Update `test_backlog_window_row_and_017_018_renumbering`: no `| 53 |` row and no
          row with "last match" and "window_floor". Row #50 no longer names
          `alerts/config.py:55`. The checks for #46, #47 and #49 and "planned as 018" stay.
        - Add `test_readme_results_free_slice_row_and_roadmap`:
          - The README has a `## Retrieval results` heading that links
            `docs/reports/retrieval-eval-v2.md`.
          - BACKLOG has a P3 row `| 56 |` with "free" slice and the trigger "building the Q&A
            agent (Stage 4)".
          - The ROADMAP's Q&A line names `#56`.
          - The ROADMAP's Stage 2 retrieval item is `- [x]` and links the report and ADR
            0009.
          - `EVALUATION.md` contains `2026-10-10T10:00` and not `2026-10-10T18:30`, and says
            the `free` slice is not judged until Stage 4.
      - Then edit the documents:
        - **BACKLOG:** #16 gets the priority and trigger by the rule. Remove #53. Shorten
          #50 to the docstrings (the comment is fixed). Add #56, P3: "judge and evaluate the
          `free` slice of set v2 (English and Polish free questions)", trigger "building the
          Q&A agent (Stage 4)".
        - **ROADMAP:** tick the Stage 2 item with links to the report and ADR 0009, and add
          "(BACKLOG #56)" to the Stage 4 Q&A item. If reranking is needed, add a Stage 2 item
          for a reranking spec.
        - **EVALUATION.md:**
          - set the `export-corpus` example to `--until 2026-10-10T10:00Z`;
          - add a line saying the `free` slice is not judged until Stage 4;
          - note that the `alerts` window rule now follows the new production floor, so a
            rebuild of set v2's `player` slice would give other windows than the frozen set.
        - **README:**
          - add a `## Retrieval results` section before `## Documentation`, with the setup in
            two sentences, the `player` `compare` rows cut to the best configuration per
            model (no `free` slice numbers), and a link to the report;
          - if the model changed, update the default named on line 167.
        - **DEPLOYMENT:** if the model changed, update the default named on line 97.
      Automatic verification: `cd backend && uv run pytest -q tests/test_docs.py`
      `iterations: 1` (the Q&A line of the ROADMAP had to name #56 on its first line)
- [x] 9. **Full verification and the roadmap check.** Files: none new. Run the full
      verification, then the end-to-end checks below, and record the results under
      "Definition of Done".
      Automatic verification: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`
      `iterations: 1` (`tests/worker/test_cli.py:855` named the old default model)

## Risks and traps

- **The Compose database is production.** Every `evaluate` command carries
  `DATABASE_URL=…localhost:55432/eval`. A command without it would read `backend/.env` and
  drop `retrieval_eval` on the owner's database. Alembic runs only with that variable set. Do
  not re-index the Compose database or write to it.
- **Paid calls:** eight runs at about $0.002–$0.02 each, under `--max-cost-usd 0.10` each.
  The owner's cap is $1. The key comes from `backend/.env`. Nothing else that is paid runs,
  and there are no judge calls in phase B.
- **Run time:** each run re-embeds 2557 posts. In phase A an export embedding the corpus took
  about 80 min for 4 models. Run them in the background, one at a time, since they share one
  schema.
- **Embedding drift:** re-embedding can shift ranks slightly against the phase A pool, so
  unjudged posts may reach a cut. An unjudged post counts as not relevant. The report states
  each non-zero `unjudged_share`. No judging is added in phase B.
- **Ties:** with 69 counted queries and full-text rows identical across models, ties are
  likely. Compare exact values, never the 3-decimal table. The two unordered cases escalate
  (step 5).
- **The floor after `as_of` (settled, owner decision 2026-10-22).** The previous gameweek's
  last match plus 2 h could be later than the moment the window is computed for: the digest at
  `D-1@20:00` before a midweek deadline that follows a Monday-evening match, a rehearsal
  deadline set during a gameweek's matches, and the ingest catch-up during N−1's matches. Only
  matches ended by `as_of` count, and with none ended the floor is the previous deadline, so
  the floor is never after `as_of` and the ingest catch-up during matches pages back to N−1's
  deadline as before. Steps 2 and 3 test each scenario.
- **One window per deadline, once N−1 has ended.** `alert_window_start`'s old docstring says
  counting from the deadline keeps one window for all of a deadline's alerts. Under the guard
  this holds only once all of N−1's matches have ended; an alert computed during them gets an
  earlier start, which moves forward as matches end. The replacement comment says so.
  `test_window_is_the_same_for_every_alert_of_a_deadline` seeds no fixtures and stays green.
- **The docs tests pin wording.** `test_backlog_window_row_and_017_018_renumbering` and
  `test_backlog_has_reranking_and_ann_entries` currently assert #53 and P2. Update them in
  step 8 together with the documents.
- **A model change** leaves the live search on full-text until the owner re-indexes, because
  hybrid degrades with the vector leg marked failed (DECISIONS 2026-09-29). An
  `EMBEDDING_MODEL` set in the owner's `.env` would override the new default. The PR
  description says both.
- **Result file size:** each test file holds per-query details for 27 configurations, a few
  MB. They are committed like `corpus.jsonl`.
- **Time zones:** the corpus and the deadlines are UTC (`Z`), and the GW6 deadline is 10:00
  UTC, not 18:30.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

- `cd backend && uv run python -m app.retrieval.evaluation compare evals/retrieval/results/test-v2-*.json`
  prints `set v2  split test  files 4`, depths `20,30,50` for each file, and 108 player rows
  with a `recall@depth` column.
- `cd backend && python3 -c "import json,glob;[print(f, json.load(open(f))['header']['reference_judge'], json.load(open(f))['header']['judge_owner_estimates'] is not None) for f in sorted(glob.glob('evals/retrieval/results/test-v2-*.json'))]"`
  prints four lines, each with `claude-opus-5-5 (session) True`.
- The winner in `app/corroboration/sources.py` (`QUERY_VARIANT`, `SEARCH_MODE`,
  `SEARCH_LIMIT`) and `DEFAULT_EMBEDDING_MODEL` match the "Winner" section of the report.
  Check with `grep -nE "QUERY_VARIANT|SEARCH_MODE|SEARCH_LIMIT" backend/app/corroboration/sources.py`
  and `grep -n DEFAULT_EMBEDDING_MODEL backend/app/retrieval/config.py`.
- On the scratch container only, run `alembic upgrade head`, seed nothing, and call
  `uv run python -m app.worker status` with the scratch `DATABASE_URL`. It starts without an
  error, which checks that the imports are wired after the window change.
- The full `verify.command` is green.

### Manual (performed by the owner)

- Only if the embedding model changed (AC8): re-index the Compose database before the merge
  with `cd backend && uv run python -m app.retrieval index --model <new model>`, and confirm
  in the PR.
  Pass when: `cd backend && uv run python -m app.retrieval status` lists `<new model>` with 0
  missing and 0 failed posts. `uv run python -m app.retrieval search "Saka" --mode hybrid`
  shows no failed vector leg. If the model is kept, this item is `n/a — model unchanged`.

## Definition of Done

- [x] all steps ticked
- [x] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto` fully green
- [x] end-to-end verification (automatic) performed, result recorded here:
      - `compare` on the test files prints `set v2  split test  files 3` (qwen dropped, owner
        decision of 2026-10-22) with depths 20,30,50 and 81 player rows with the
        `recall@depth` column.
      - The headers of all six result files (three test, three dev): `set_version` v2,
        `reference_judge` `claude-opus-5-5 (session)`, `judge_owner_estimates` present, depths
        `[20, 30, 50]`, every row has `recall_at_depth`, test files give 188 queries and 119 with
        no unclaimed relevant post. Non-zero `unjudged_share` is at most 0.0012 and is stated in
        the report.
      - Winner in code matches the report: `QUERY_VARIANT = "names"`, `SEARCH_MODE = "hybrid"`,
        `SEARCH_LIMIT = 20`, `DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-large"`.
      - `python -m app.worker status` with the scratch `DATABASE_URL` starts without an error.
      - The full `verify.command` is green (2182 passed).
- [x] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, ADR 0009, `docs/BACKLOG.md`,
      `docs/EVALUATION.md`, `README.md` (and `docs/DEPLOYMENT.md` if the model changed)
- [x] if the model changed: the PR description gives the owner's re-index command and the
      `EMBEDDING_MODEL` note (for the final review: the model changed)
- [x] the PR description lists phase A's judge-choice deviation (Opus kept while AC4's rule
      picks Sonnet) for the owner to confirm
- [x] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

- 2026-10-22 — plan-review — `decision` — AC12's floor can come after `as_of` (the D-1 digest before a midweek deadline after a Monday-evening match, a rehearsal deadline set during matches, the ingest catch-up during the previous gameweek's matches), which empties the window; how to guard it? — Count only the previous gameweek's matches that ended (kickoff + 2 h) by `as_of`; when none has ended, the floor falls back to the previous deadline. One shared function for the alerts and the ingest stays (2026-10-16); add one test case per scenario in steps 2 and 3.
- 2026-10-22 — implement — `tooling` — Step 4 stalls on `qwen/qwen3-embedding-8b`: OpenRouter embeds about 100 posts per 10 min with repeated `ReadTimeout`s, over 15 h for the qwen test run alone; how to proceed? — Drop `qwen/qwen3-embedding-8b` from the candidates (this changes the 2026-10-16 decision of three candidates): the run compares the baseline `openai/text-embedding-3-small` with `openai/text-embedding-3-large` and `google/gemini-embedding-001`, test and dev each. The report and the ADR state that qwen was dropped for its embedding throughput on OpenRouter, not for its quality.
- 2026-10-22 — implement — `permission` — The pipeline guard refuses `alembic upgrade head` on the scratch container (`localhost:55432`) in step 4; how to prepare the scratch schema? — The owner starts `presser-eval-017` and runs the migration on it; the implementer then checks the scratch schema (read only) and resumes at step 4's run loop without running alembic itself.
- 2026-10-22 — final-review — `decision` — Phase A kept Opus as the reference judge although AC4's rule points to Sonnet on the final pilot numbers; is the deviation accepted? — Accepted: Opus stays the reference judge of set v2 (the owner confirmed it at the final review gate).
- 2026-10-22 — final-review — `gate` — Which final review findings are accepted? — `accepted`: F1, F2, F3, F4, F5, F6, F7, F8; `rejected`: none

## Review log

### 2026-10-22 — plan review

Findings:

- `blocker` — AC12's rule can put the window start after the moment it is computed for, and
  the plan (like the SPEC) has no case for it. `previous_gameweek_end(session, as_of)` takes the
  gameweek with the latest deadline at or before `as_of`, so whenever `as_of` falls before that
  gameweek's last match plus 2 h, `window_floor` returns a start later than `as_of`, and the
  alert's claims and search are empty. This happens (a) for the digest at the default slot
  `D-1@20:00` (Warsaw) before a midweek deadline when N−1 ends with a Monday-evening match
  (festive and midweek rounds), (b) for a rehearsal deadline (`ALERT_REHEARSAL_DEADLINE`, any
  time) set during a gameweek's matches, and (c) for the ingest catch-up during N−1's matches
  (already in Risks). The SPEC rules out a guard ("No guard for an N−1 kickoff after the current
  deadline is needed") with an argument about N's deadline only, and fixes the rule as "the
  latest `fixture.kickoff_at` of gameweek N−1 plus 2 hours". A guard (for example, counting only
  N−1 matches that ended by `as_of`, or falling back to the previous deadline when the end is
  after `as_of`) changes the SPEC's rule, so this is a SPEC gap and is escalated. A note was
  added to Risks; steps 2 and 3 get the chosen case and its test after the owner's decision.
- `minor` — `compare` also prints the `free` slice's table, and step 6 copied "the `compare`
  table" whole into the report, while the SPEC keeps the `free` slice out of the report. Step 6
  and the README part of step 8 now take the `player` part only.
- `minor` — the SPEC requires a tooling change made under the "blocks the run" exception to be
  recorded as a deviation; the plan did not say so. Approach (1) and step 1 now record
  `recall_at_depth` as a `minor` deviation.
- `minor` — phase A's judge-choice deviation (Opus kept while AC4's rule picks Sonnet) ends in
  "Owner to confirm" in PR #38, and no entry in `## Owner decisions` records the confirmation.
  The plan now lists it in the PR description for the final review gate (Approach, Definition
  of Done); it does not reopen phase A.
- `minor` — `previous_gameweek_end` did not say how fixtures are matched to the gameweek; the
  database holds several seasons. Approach (2) now matches on season and gameweek, and step 2
  has an "another season" test.

Checked and found sound:

- Coverage: every phase B AC (AC6–AC14) has steps and a proving test or the SPEC's waiver
  (AC7); AC1–AC5 are phase A, delivered in PR #38 (`78e82b6`), and the matrix matches the steps.
- `recall_at_depth` cannot be derived from existing result files: `runner.py` keeps only the
  cut and the top 10 per configuration, so the additive column is needed for AC9.
- The evaluation search equals production's: `search(limit=depth, depth=50)` in the runner and
  `search(limit=SEARCH_LIMIT)` with the default leg depth 50 in `sources.py`; the per-query
  windows are frozen in `queries.jsonl`, so the `players.py` rule change does not alter the run.
- Every `window_floor` / `alert_window_start` call site (`alerts/service.py`,
  `alerts/breaking.py` through `alert_window_start`, `tweets/loop.py`,
  `retrieval/evaluation/players.py`) is covered; no alert, tweet, corroboration or worker test
  seeds `Fixture` rows, so they take the fallback unchanged.
- The test helpers the plan reuses exist (`_seed_gw5_and_gw6`, `_floors_of_one_poll`,
  `add_fixture`, `seed_reference`, `GABRIEL_MAGALHAES`, `_recording_search`, `_rows`), as do the
  tests it updates and `tests/retrieval/test_config.py`'s default-model literal.
- The run's safety: every `evaluate` and Alembic command carries the scratch `DATABASE_URL`;
  `--max-cost-usd 0.10` covers the costliest model (about 143k tokens × $0.15/M ≈ $0.02); dev
  results go to the gitignored `results/dev/`; the file names follow `default_run_name`.
- The tie-rule escalations of step 5 (a full-text winner, a mode-only tie) are real gaps of
  AC7 and are left as conditional escalations.
- No new dependency and no data migration; the Owner summary flags agree. The manual item has
  a `Pass when:` line. The plan is in English.

Not approved: the blocker above is a SPEC gap the review cannot settle in the plan.

### 2026-10-22 — plan review, after the owner decision

The owner decision of 2026-10-22 (`## Owner decisions`) settles the blocker above: only the
previous gameweek's matches ended (kickoff + 2 h) by `as_of` count, and with none ended the
floor is the previous deadline. It was applied in the Owner summary, Approach (2), steps 2, 3
and 7 and Risks: the helper filters fixtures by `kickoff_at + MATCH_LENGTH <= as_of`, step 2
adds the "not yet ended" and "none ended" cases and a never-after-`as_of` check, and step 3
adds one test per scenario (the D-1 digest before a midweek deadline, a rehearsal during
matches, the ingest catch-up during matches).

Findings:

- `major` — with the guard, the slice builder's call `previous_gameweek_end(session,
  previous_deadline)` would count matches ended by N−1's own deadline, i.e. none, so the
  `alerts` rule would always fall back to the previous deadline and stop following production.
  The helper now takes an optional `gameweek_at` (which gameweek) beside `as_of` (which
  matches have ended), and `players.py` calls it with N's deadline as `as_of` and
  `gameweek_at=previous_deadline` (Approach (2), step 2 test, step 3).
- `minor` — step 3's catch-up test compared the poll's floor with `alert_window_start` under a
  7-day lookback, while `_floors_of_one_poll` builds the poller with 5 days. The test now uses
  the poller's lookback.
- `minor` — the old risk "the ingest pages back only to the newest page during N−1's matches"
  no longer holds under the guard, and the "one window for all of a deadline's alerts" claim of
  `alert_window_start`'s docstring holds only once N−1 has ended. Risks and the Owner summary
  now say so, and the DECISIONS row of step 7 names the guard.

Checked and found sound:

- The guard keeps the floor at or before `as_of` at every call site: `alert_window_start`
  (digest, news and breaking alerts through it), `tweets/loop.py` (`now`), and the slice
  builder (N's deadline, after which no N−1 match is played, as the SPEC argues).
- Existing alert, tweet, corroboration and evaluation tests seed no fixtures, so they still
  take the previous-deadline fallback; `test_window_is_the_same_for_every_alert_of_a_deadline`
  stays green.
- The frozen `queries.jsonl` windows do not change with the builder rule, so the evaluation
  runs of step 4 are unaffected by steps 2 and 3.
- The rest of the first review's "checked" list still stands: nothing outside steps 2, 3, 7
  and Risks changed. No new dependency, no data migration, the manual item has its
  `Pass when:` line, and the plan is in English.
- Compliance: `docs/CONVENTIONS.md` read in full; the plan writes tests before code, mirrors
  `app/` in `tests/`, replaces the `alert_window_start` docstring with a comment and keeps the
  `window_floor` docstring only because AC13 names it; the DECISIONS 2026-10-07 row is the one
  the new row supersedes.

Approved: the only blocker is settled by the owner's decision and applied, the remaining
findings were fixed in the plan, and every phase B AC has steps with runnable verification.

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

- `minor` — step 1: added `recall_at_depth` beside `recall_at_cut` to the spec 016 evaluation tooling (`player_metrics.py`, `runner.py`, `compare.py`); AC9 needs it and it cannot be derived from result files (the SPEC's "blocks the run" exception).
- `minor` — step 2: `previous_gameweek_end` breaks ties between seasons by the later season, so a same-deadline gameweek of another season cannot make the choice depend on row order.
- `minor` — step 3: shared helper `add_fixture` added to `tests/corroboration/helpers.py`; three existing tests in `tests/retrieval/evaluation/test_players.py` (lookback cap, `claimed_ids_equal_sql_claims`, the CLI test) were adjusted because their `world()` fixtures now start the `alerts` window at the last match (their purpose unchanged). Under the new rule no claim of a newly built slice can be stale, so the `stale: 1` expectation became `stale: 0`; the stale code stays for the frozen v2 data.
- `minor` — step 4: three models instead of four (qwen dropped, owner decision of 2026-10-22), so `compare` prints 3 files and 81 player rows; the dev run of each model went to `results/dev/` as planned. The baseline pair of runs was reused from the earlier attempt (same code commit f70b9db).
- `minor` — step 5: `tests/retrieval/test_cli.py::test_status_on_empty_database` named the old default model in its expected status line; updated to the new default (a test naming the old value, as the plan allows).

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_

### 2026-10-22 — final review (report)

Three independent perspectives (compliance, quality, tests) ran as separate subagents; every
finding below was checked in the code or the data before it was kept.

AC → evidence:

| AC | Evidence |
|---|---|
| AC1–AC5 | phase A (PR #38, `78e82b6`): `set.json` v2 with the GW5–GW7 corpus window, 2557 posts, 269 `player` queries; price rows; 13,906 Opus pairs; the pilot numbers in the report match `78e82b6`; the judge-choice deviation goes to the PR description |
| AC6 | `backend/evals/retrieval/results/test-v2-{openai-text-embedding-3-small,openai-text-embedding-3-large,google-gemini-embedding-001}.json` (qwen dropped by the owner decision of 2026-10-22), headers with set v2, reference judge, label counts and `judge_owner_estimates`, depths 20/30/50, `recall_at_depth` on every row; `test_runner_v2.py`, `test_compare.py` |
| AC7 | report "Winner": exact maximum 0.933318694 tied on 3-large/hybrid/`names` at 20/30/50, production 0.90576 not in the tie, smaller depth wins → 20 (re-checked from the files) |
| AC8 | `app/corroboration/sources.py` (`QUERY_VARIANT = "names"`, `SEARCH_MODE = "hybrid"`, `SEARCH_LIMIT = 20`, `MAX_JUDGED = 10`), `app/retrieval/config.py` (3-large); `test_sources.py::test_corroboration_searches_with_the_evaluated_winner`, `test_config.py`; the owner's re-index before the merge stays open |
| AC9 | `player_metrics.py` `recall_at_depth` + `test_player_metrics.py`; report "Reranking" (0.933 ≥ 0.80; 0.998 − 0.933 = 0.065 < 0.10 → not needed); BACKLOG #16 P3; `test_docs.py::test_backlog_has_reranking_and_ann_entries` |
| AC10 | `docs/reports/retrieval-eval-v2.md`, every section (see F3); `test_docs.py::test_retrieval_report_v2_has_every_section` |
| AC11 | `docs/adr/0009-default-embedding-model.md`; DECISIONS 2026-10-22 row; `test_docs.py::test_adr_0009_and_decisions_row_for_the_retrieval_winner` |
| AC12 | `app/fpl/deadlines.py::previous_gameweek_end` + `window_floor`, call sites in `alerts/service.py`, `tweets/loop.py`, `retrieval/evaluation/players.py`; `tests/fpl/test_deadlines.py` (every calendar case, see F1), `tests/alerts/test_slots.py`, `tests/tweets/test_loop.py`, `test_players.py::test_alerts_window_follows_the_production_floor` |
| AC13 | DECISIONS superseding row, BACKLOG #53 removed and #50 shortened, `window_floor` docstring, `alerts/service.py` comment, `alerts/config.py:55`; `test_docs.py::test_backlog_window_row_and_017_018_renumbering`, `::test_decisions_supersede_the_previous_deadline_window` |
| AC14 | README `## Retrieval results`, BACKLOG #56, ROADMAP Stage 2 ticked and Q&A item naming #56, EVALUATION.md `2026-10-10T10:00Z` and the `free` slice line; `test_docs.py::test_readme_results_free_slice_row_and_roadmap` |

Findings:

- **F1** `worth-fixing` — backend/tests/fpl/test_deadlines.py:108-110, 182, 187, 206 — the calendar has `D6 = D5 + 7 days` and `LOOKBACK = 7 days`, so the expected previous deadline `D5` equals the lookback floor `D6 − LOOKBACK`: a `previous_gameweek_end` that returns `None` instead of the deadline when no match qualifies keeps all three fallback tests green (mutation run) — make the GW5→GW6 gap shorter than the lookback (e.g. `D6 = D5 + 4 days`) and assert `previous_gameweek_end(...) == D5` directly.
- **F2** `worth-fixing` — backend/tests/retrieval/evaluation/test_runner_v2.py:176, 236 — the fixture gives 0.25 for both `recall_at_cut` and `recall_at_depth`, so `_format_row` writing `recall_at_cut` into `"recall_at_depth"` stays green across `test_player_metrics`, `test_runner_v2` and `test_compare` (mutation run), and a wrong field in the result JSON would ship — add one unclaimed relevant post ranked between the cut and the depth so the two values differ, and assert both exactly.
- **F3** `worth-fixing` — docs/reports/retrieval-eval-v2.md:62 — "121 relevant pairs among 10,292 pooled pairs (1.2%)": 10,292 counts judgement entries on the test split (Opus, Sonnet pilot and owner), while the distinct pooled pairs are 9,686 (= 104 owner-reviewed + 9,582 judge-only in the header), so AC10's share is stated against the wrong denominator — write "121 of 9,686 pooled pairs (1.25%)".
- **F4** `nit` — backend/app/retrieval/evaluation/players.py:52-72 — `gameweek_window` computes the previous gameweek's end twice: the stale marker (`previous_end`, fixtures of `gameweek − 1`, no `as_of` guard) and the `alerts` start (`previous_gameweek_end`, by deadline order, matches ended by N's deadline); they agree on real calendars, but a divergence would mark claims inside a newly built window as stale — comment why they differ, or derive both from one helper.
- **F5** `nit` — backend/app/corroboration/sources.py:133-138 — with `QUERY_VARIANT = "names"` the default path no longer short-circuits: every corroborated player opens a Session and `load_player_names(..., aliases=None)` re-parses the aliases TOML, on the alert hot path — load the aliases once per run and pass them in (as `resolve_player` does), or cache `load_aliases`.
- **F6** `nit` — backend/app/fpl/deadlines.py:60 — no test sits on the boundary of `Fixture.kickoff_at <= as_of - MATCH_LENGTH` (a match ending exactly at `as_of`); mutating `<=` to `<` stays green — add a case with `as_of = MON + 2 h` expecting `MON + 2 h`.
- **F7** `nit` — README.md:52 — the results table's "recall within the depth" shows each row at its own depth (0.941 for the winner at 20), while AC9, ADR 0009 and BACKLOG #16 use the depth-50 value (0.998) — add a one-line note that AC9 reads the depth-50 row.
- **F8** `nit` — docs/EVALUATION.md:133 — "`evaluate` reports only the `player` slice's numbers for set v2", but `evaluate` and `compare` still print an empty, unjudged `free` table (the report says so itself) — say the `free` table printed for set v2 is unjudged and to be ignored.

Rejected: none (two perspective findings were lowered to `nit` — F4, because the two computations agree on every real calendar, the slice builder runs only on demand and the frozen v2 windows do not change; F5, because a small TOML parse per player is negligible beside the embedding call and the search).

Left out: 11 nit findings

### 2026-10-22 — final review (apply)

All eight findings accepted at the gate (`## Owner decisions`); fixed as follows:

- **F1** — `backend/tests/fpl/test_deadlines.py`: `D6 = D5 + 4 days`, shorter than the 7-day
  lookback, and the three fallback tests assert `previous_gameweek_end(...) == D5` directly; the
  mutation `return None` instead of the deadline now fails 3 tests.
- **F2** — `backend/tests/retrieval/evaluation/test_runner_v2.py`: new
  `test_recall_at_depth_is_written_apart_from_recall_at_cut` sets the judge's cut to one post,
  so the second retrievable relevant post sits between the cut and the depth (0.125 against
  0.25, asserted on the metrics and in the result JSON); writing `recall_at_cut` into
  `recall_at_depth` now fails it.
- **F3** — `docs/reports/retrieval-eval-v2.md`: "121 relevant pairs among 9,686 pooled pairs
  (1.25%)".
- **F4** — `backend/app/retrieval/evaluation/players.py`: a comment on why `gameweek_window`
  computes the previous gameweek's end twice (the after-last-match rule and stale marker
  against production's floor) and when they agree.
- **F5** — the aliases are loaded once per alert build: `build_slot_alert` puts them into the
  corroboration runtime (`CorroborationRuntime.player_aliases`), `retrieval_candidates` takes
  `aliases=` and passes them to `load_player_names`, and `build_candidates` loads them once
  per run; tests `test_sources.py::test_passed_aliases_are_used_without_reading_the_aliases_file`
  and `test_slots.py::test_the_aliases_are_loaded_once_per_alert_not_per_player`.
- **F6** — `backend/tests/fpl/test_deadlines.py`: `test_a_match_ending_exactly_at_the_moment_counts`
  (`as_of = MON + 2 h` gives `MON + 2 h`); the mutation `<=` → `<` now fails it.
- **F7** — `README.md`: a note that "recall within the depth" is each row at its own depth and
  that the reranking check reads the winner's depth-50 row (0.998).
- **F8** — `docs/EVALUATION.md`: the `free` table printed for set v2 is unjudged and to be
  ignored.

Verification after the fixes: `verify.command` green (2186 passed). BACKLOG: no new items and
no fired triggers; #53 was delivered and removed in the implementation.
