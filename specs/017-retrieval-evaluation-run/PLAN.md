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
  - During the previous gameweek's matches, the ingest catch-up now pages back only to the
    newest page. The new floor is in the future at that time. This follows from "one function
    for the alerts and the ingest" (owner decision); see Risks.
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
report states this. It is not reopened here.

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
delivered.

**2. The alert window (AC12, AC13).**

- Variant A, chosen: keep `window_floor` pure and change what its first argument means.
  - It was the previous deadline and becomes the previous gameweek's end.
  - A new DB helper in `app/fpl/deadlines.py` computes that end:
    `previous_gameweek_end(session: Session, t: datetime) -> datetime | None`.
  - The helper takes the gameweek with the latest `deadline_at` at or before `t`, the same
    choice `deadline_at_or_before` makes. It returns that gameweek's latest non-null
    `Fixture.kickoff_at` plus `MATCH_LENGTH` (2 h).
  - A gameweek with no fixture with a kickoff returns its deadline (the fallback). No gameweek
    returns `None`.
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
  `previous_gameweek_end(session, previous_deadline)` (or `None`) into `window_floor`.
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

- [ ] 1. **Recall within the depth in the evaluation (AC9 number, AC6 column).** Files:
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
      Automatic verification: `cd backend && uv run pytest -q tests/retrieval/evaluation/test_player_metrics.py tests/retrieval/evaluation/test_compare.py tests/retrieval/evaluation/test_runner_v2.py && uv run ruff check app/retrieval/evaluation tests/retrieval/evaluation`
- [ ] 2. **`previous_gameweek_end` and the new `window_floor` rule (AC12 core, AC13
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
      - Then add `MATCH_LENGTH = timedelta(hours=2)` and `previous_gameweek_end`.
      - Rename `window_floor`'s first parameter to `previous_end`, and rewrite its docstring
        for the new rule. AC13 asks for the docstring explicitly. The existing pure
        `window_floor` tests stay, with their wording updated where it says "previous
        deadline".
      Automatic verification: `cd backend && uv run pytest -q tests/fpl/test_deadlines.py && uv run ruff check app/fpl tests/fpl`
- [ ] 3. **The alerts, the ingest catch-up and the slice builder on the new rule (AC12,
      AC13 comments).** Files: `backend/app/alerts/service.py`, `backend/app/alerts/config.py`,
      `backend/app/tweets/loop.py`, `backend/app/retrieval/evaluation/players.py` and the tests
      listed below.
      - Tests first:
        - `tests/alerts/test_slots.py::test_alert_window_starts_after_the_previous_gameweeks_last_match`:
          seed GW5 fixtures. `alert_window_start` for `REAL` and for `REHEARSAL` gives the
          last kickoff plus 2 h. The rehearsal uses the last real gameweek.
        - `tests/tweets/test_loop.py::test_the_catch_up_floor_equals_the_alert_window_start`:
          seed GW5 and GW6 with GW5 fixtures. The floor of one poll at moment T equals
          `alert_window_start(session, T, <AlertDeadline of GW6>, AlertConfig(...,
          max_lookback=7 days))`, and both equal the last kickoff plus 2 h.
        - `tests/retrieval/evaluation/test_players.py`: rename and update
          `test_a_lineup_before_the_last_match_is_stale_under_alerts_and_outside_after_last_match`.
          Under `alerts` the window now starts at `LAST_END`, so the line-up claim is outside
          it. Add `test_alerts_window_follows_the_production_floor`: with fixtures the start
          is the last match plus 2 h, and without them it is the previous deadline.
          `test_alerts_window_starts_at_the_previous_deadline` (no fixtures) stays as it is.
      - Then:
        - `alert_window_start` returns `window_floor(previous_gameweek_end(session, as_of),
          deadline.deadline_at, config.max_lookback, as_of)`. Drop the `window_start` import,
          and replace the docstring with a comment stating the new rule.
        - Fix the comment at `alerts/config.py:55`: the window counts back from the
          deadline, and it starts after the previous gameweek's last match.
        - In `tweets/loop.py` the floor uses `previous_gameweek_end(session, now)`. Update its
          comment.
        - In `players.py` the `alerts` rule uses `previous_gameweek_end(session,
          previous_deadline) if previous_deadline else None`, and `MATCH_LENGTH` is imported
          from `app.fpl.deadlines`.
      - The existing alert, corroboration and tweet tests seed no fixtures, so they take the
        previous-deadline fallback and must pass unchanged.
      Automatic verification: `cd backend && uv run pytest -q -n auto tests/fpl tests/alerts tests/tweets tests/corroboration tests/retrieval/evaluation/test_players.py tests/worker && uv run ruff check app tests`
- [ ] 4. **The evaluation runs (AC6, AC2 cap).** Files:
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
- [ ] 5. **Pick the winner and put it into production (AC7, AC8).** Files:
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
- [ ] 6. **The report `docs/reports/retrieval-eval-v2.md` (AC7, AC9, AC10, AC2 spend).**
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
        - **The `compare` table** from step 4.
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
- [ ] 7. **ADR 0009 and the DECISIONS rows (AC11, AC13 DECISIONS).** Files: new
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
          deadline minus the lookback, with the fallbacks, superseding the "previous real
          deadline" part of the 2026-10-07 row. The per-club window is the rejected
          alternative, as in the SPEC.
      Automatic verification: `cd backend && uv run pytest -q tests/test_docs.py`
- [ ] 8. **BACKLOG, ROADMAP, EVALUATION.md, README and DEPLOYMENT (AC9 backlog, AC13,
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
            two sentences, the `compare` rows cut to the best configuration per model, and a
            link to the report;
          - if the model changed, update the default named on line 167.
        - **DEPLOYMENT:** if the model changed, update the default named on line 97.
      Automatic verification: `cd backend && uv run pytest -q tests/test_docs.py`
- [ ] 9. **Full verification and the roadmap check.** Files: none new. Run the full
      verification, then the end-to-end checks below, and record the results under
      "Definition of Done".
      Automatic verification: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`

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
- **Ingest during gameweek N−1's matches.** Between N−1's deadline and its last match plus 2 h,
  the floor is in the future, so a catch-up after downtime stores only the newest page. Before
  this change it paged back to N−1's deadline. Alerts never run then, because breaking alerts
  open after the last slot. The loss is of posts outside every alert window, which follows
  from "one function for the alerts and the ingest" (owner decision 2026-10-16). It is
  recorded as a BACKLOG proposal in the stage summary, not as a plan step.
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

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto` fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, ADR 0009, `docs/BACKLOG.md`,
      `docs/EVALUATION.md`, `README.md` (and `docs/DEPLOYMENT.md` if the model changed)
- [ ] if the model changed: the PR description gives the owner's re-index command and the
      `EMBEDDING_MODEL` note
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

## Review log

_(filled in by /pipeline:plan-review — one list item per finding, starting with its severity in backticks: `- `blocker` — …`, `major` or `minor`; with no finding, the one item `- `none` — no findings`)_

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_
