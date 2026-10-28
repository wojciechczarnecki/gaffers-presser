# PLAN 019 — Corroboration evaluation v2: a reviewed judge set, contradictions and a pass threshold

## Owner summary

- **Approach:** Code first, then data, then runs. The corroboration evaluation gets six
  additions:
  - a v2 case format (the session pre-label, haiku's v1 label);
  - a composition check that requires `contradicts`;
  - `evaluate` with the production slice, a pass verdict and the cost a month;
  - a review that shows disagreements first;
  - the session judge's export, estimate and import, with a `corroboration` schema in
    `scripts/session_judge.sh`;
  - two build commands that run only against a throwaway container. `build-scratch` loads the
    retrieval v2 corpus with the extraction v2 snapshots, embeds it with
    `text-embedding-3-large` and extracts the GW6/GW7 window with prompt v5 and the match
    check, capped at $0.90. `build-candidates` draws anchors and retrieved posts per window
    the way production does.

  The Claude Code session (Opus) pre-labels v1, blind, and about 250 candidates, and 55 new
  cases are selected with `contradicts` first. Then the implementer stops for your full
  review. After it come dev iterations, a frozen prompt, six test runs and the rule's winner.
  A separate `LLM_JUDGE_MODEL` setting is added only if the winner is not luna.
- **Main risks:**
  - The implementation stops once for your review of about 106 cases (step 9), and possibly a
    second time if your labels leave fewer than 8 `contradicts` (or 5 in test).
  - `claude -p` or `docker` may be refused by a permission rule.
  - The windows may give few real contradictions, so SQL-context pairs fill the gap, as the
    SPEC allows.
  - Extracting about 2,000 posts takes about an hour of runtime (cost about $0.5).
- **New dependency:** no.
- **Data migration:** no. No migration file is added. `alembic upgrade head` runs only on
  the throwaway test container of step 8, never on Compose.
- **Manual scenarios for the owner:** none beyond the review of set v2, which step 9 asks
  for through an escalation.

## Approach

### What the plan rests on

- `specs/019-corroboration-evaluation-v2/SPEC.md`: read in full, including `## Owner decisions`.
- `docs/CONVENTIONS.md`: read in full. The points this plan uses:
  - prompts live in `app/content/`;
  - LLM steps are tested against fakes, and evaluations run outside `pytest`;
  - database tests use a container;
  - `env -u TWEET_SOURCE` near `backend/.env`;
  - a commit after every green step;
  - exact pins, so this plan adds no dependency.
- `docs/DECISIONS.md`: searched for "corrobor", "judge", "session", "window" and "2026-10-2".
  The search found:
  - 2026-09-30, the judge's narrow role, the set format and the pre-label by another model;
  - 2026-10-06, 2026-10-22 and 2026-10-24: the alert window (`window_floor`,
    `previous_gameweek_end`), the retrieval configuration and `fresh_lineups_only` in the
    builders;
  - 2026-10-15: `eval_results`, the evaluation entry points, `current_extractions` as the
    public read API and the module data flow;
  - 2026-09-29: the corpus never goes into the live `tweet` table.
- `docs/ROADMAP.md`: searched for "019". It found the Stage 2 item at lines 88–91.
- `docs/BACKLOG.md`: searched for rows 12 and 47 (lines 25 and 42).
- `docs/EVALUATION.md`: searched for its headings, "corroboration", "scratch" and
  "container". It found:
  - the rules (lines 15–35);
  - the retrieval procedure, which runs on a scratch database (lines 89–135);
  - the extraction procedure, the pattern for export, estimate and import (lines 214–268);
  - "Other evaluations" (lines 271–281), which is to be rewritten.
- `docs/PROJECT.md`: searched for "corrobor" and "judge". It found line 87 (`LLM_MODEL`
  serves both).
- `docs/DEPLOYMENT.md`, `.env.example`: searched for `LLM_MODEL`. They found lines 65–77 and
  lines 29–41, which change only in case 2 of step 12.
- `README.md`: read lines 245–272 (the corroboration section and its commands).
- `docs/reports/extraction-eval-v2.md`: searched for "cost", "PLN" and "overlap". It found
  the cost-a-month format, the selection paragraph and the vendor-overlap threat.
- `specs/018-extraction-evaluation-v2/PLAN.md`: read lines 1–700. It is the pattern for the
  owner's stop (step 11 there), the dev-iteration log and the test runs.
- `specs/017-retrieval-evaluation-run/PLAN.md`: read lines 130–160 and 285–330 (the scratch
  container commands).
- Code read in full:
  - `app/corroboration/evaluation/`: `building.py`, `cases.py`, `cli.py`, `metrics.py`,
    `runner.py`, `__main__.py`;
  - `app/corroboration/`: `judge.py`, `config.py`, `schemas.py`, `runtime.py`, `sources.py`,
    `rules.py`, `service.py`;
  - `app/content/prompts/corroboration_judge.md`;
  - `app/extraction/evaluation/`: `judging.py`, `dataset.py`, `selection.py`;
  - `app/extraction/service.py`, `app/extraction/loop.py`;
  - `app/retrieval/evaluation/schema.py`;
  - `scripts/session_judge.sh`.
- Code read in part:
  - `app/retrieval/evaluation/labelling.py` (`CostTracer`, `index_missing` use) and
    `players.py::gameweek_window`;
  - `app/fpl/deadlines.py` (`previous_gameweek_end`, `window_floor`, `MATCH_LENGTH`);
  - `app/alerts/service.py::alert_window_start`;
  - `app/extraction/store.py` (`next_pending`, `current_extractions`);
  - `app/extraction/fixtures.py`, `app/extraction/cli.py` (the deps helpers);
  - `app/llm/chat.py` (`resolve_llm`, `single_model_config`, the defaults);
  - `app/core/eval_results.py`;
  - `app/fpl/models/reference.py` (`Season`, `Gameweek`, `Player`);
  - `app/retrieval/models.py::PostEmbedding`.
- Tests read in part:
  - `tests/corroboration/evaluation/test_eval_set.py`, `test_results_files.py` (whole);
  - `tests/corroboration/evaluation/test_review_cli.py` (lines 1–40);
  - the test lists of `test_cases.py`, `test_building.py`, `test_runner.py` and
    `test_metrics.py`;
  - `tests/test_session_judge.py` (the stub harness), `tests/test_module_boundaries.py`;
  - `tests/test_docs.py` (lines 20–45 and 295–315; `test_roadmap_018_ticked_and_019_open` and
    `test_backlog_23_46_54_removed_and_12_narrowed` change in step 14);
  - `tests/test_readme.py::test_corroboration_commands_documented`;
  - `tests/extraction/candidates.py` (the six models).
- Data inspected:
  - `evals/corroboration/v1/cases.jsonl` (the format);
  - `evals/retrieval/v2/set.json` (19 `list_handles`) and its `queries.jsonl` (GW6 and GW7
    deadlines 2026-10-10T10:00Z and 2026-10-17T10:00Z);
  - `evals/extraction/v2/players.json` (669 players, 20 teams) and `fixtures.json` (no
    deadlines). For GW3–GW8 the first kickoff minus 90 min equals the deadline, and that
    holds for GW6 and GW7, the two recorded ones.

### Design

1. **Case format v2 (AC4).** `JudgeCase` keeps its fields and gains two optional ones, so v1
   lines still load:
   - `prelabel: Label | None = None`: the label the session judge gave (immutable);
     `labelled_by` names that judge;
   - `v1_prelabel: Label | None = None`: for a v1 case, its original `expected`, which was
     haiku's label (`PRELABEL_MODEL` names it).

   `expected` starts equal to `prelabel` and becomes the owner's label in `review`.
   `has_player_event` and `reviewed` are unchanged. The rejected variant was a new case class
   for v2, which would duplicate the review, the runner and the metrics.
2. **The set directory.** `evals/corroboration/v2/` holds `cases.jsonl` and `set.json`. The
   manifest is `SetManifest` in a new `app/corroboration/evaluation/dataset.py`:
   - `version`;
   - `list_handles` (copied from `evals/retrieval/v2/set.json`);
   - `prelabels`: a list of `{judge, prompt_version, git_commit, inputs, imported_at}`, as
     in extraction;
   - `build`: the corpus path, `windows` (`[{gameweek, start, end}]`), `embedding_model`,
     `extraction_model`, `extraction_prompt_version`, `embeddings_cost_usd`,
     `extraction_cost_usd`, `query_embeddings_cost_usd`, `candidates` and
     `judge_calls_per_month`;
   - `selection`: `{seed, size, pool, min_contradicts}`.

   The constants in `cases.py` are:
   - `V1_CASES_PATH` (v1, unchanged);
   - `V2_SET_DIR`;
   - `DEFAULT_CASES_PATH = V2_SET_DIR / "cases.jsonl"`.

   `build-cases` keeps v1's behaviour, with its default output pinned to `V1_CASES_PATH`.
3. **Composition (AC2).**
   - `LABELS_NOT_YET_REQUIRED` is removed.
   - `MIN_CASES` and `MAX_CASES` become 90 and 130, and the new `MIN_CONTRADICTS = 8` and
     `MIN_TEST_CONTRADICTS = 5` are checked.
   - The other rules stay: duplicate ids, a dev share of 0.25–0.35 (v1's split is 15/51 =
     0.29), every label overall and in test, no `labelled_by` equal to the judge's default
     model, at least one known miss.
   - Only `labelled_by` is checked against the default, because it is the label that seeded
     `expected`. `v1_prelabel` stays as a comparison. If haiku won, the report names this
     under vendor overlap, and every case is owner-reviewed anyway.
   - v1 no longer passes the check, so its test only pins the file as built.
4. **Metrics, the verdict and the cost (AC9, AC10).**
   - `metrics.py` gains:
     - `THRESHOLDS = {"accuracy": 0.80, "false_support": 0.10, "contradicts_recall": 0.70}`;
     - `threshold_verdict(metrics) -> {"accuracy", "false_support", "contradicts_recall",
       "no_errors", "passed"}`, all booleans. A `None` value fails. `no_errors` is
       `errored == 0`.
   - `run_evaluation` adds three keys to the result:
     - `production`: `compute_metrics` over the outcomes whose case has
       `has_player_event: false`;
     - `verdict`: the verdict on the whole split only;
     - `projected_monthly_cost_pln`: the mean `cost_usd` per outcome × `calls_per_month` ×
       `usd_pln_rate`, or `None` when any of them is missing.
   - `evaluate` reads `judge_calls_per_month` from the cases file's `set.json`, if any, and
     `usd_pln_rate` from the settings. `format_metrics` prints the production block, one
     `threshold <name>: pass|fail` line each and `verdict: PASS|FAIL`.
   - `default_result_path(split, model, set_version)` gives three paths:
     - `results/<split>-<slug>.json` for v1 (unchanged);
     - `results/<v>/test-<slug>.json`;
     - `results/<v>/dev/dev-<slug>.json`.

     `.gitignore` gets `backend/evals/corroboration/results/*/dev/`.
5. **Review and agreement (AC7, AC8).**
   - `review` orders the walked positions in three groups, each keeping file order:
     1. unreviewed cases whose `prelabel` and `v1_prelabel` are both set and differ;
     2. other v1 cases (`v1_prelabel` set);
     3. the rest.
   - `render_case` prints `pre-label: X (by <labelled_by>)`, and for a v1 case a second line
     `v1 pre-label: Y (by anthropic/claude-haiku-4.5)` with `DISAGREE` when they differ.
     Accept and change keep their at-once save.
   - A new `agreement --cases PATH` command prints, over reviewed cases, two counts:
     `expected != prelabel` per `labelled_by`, and `expected != v1_prelabel` for haiku. It
     also prints one line per differing case. `prelabel_agreement(cases)` in `cases.py`
     computes this.
6. **The session judge (AC5, AC6).** A new `app/corroboration/evaluation/judging.py` follows
   `app/extraction/evaluation/judging.py`.
   - `export_inputs(sources, out, seed)` writes `inputs/judge-prompt.md` (exactly
     `JUDGE_PROMPT.text`) and one `inputs/I0001.md`… per source. Each input is
     `render_input(JudgeInput)` exactly, built by the runner's existing `_judge_input`, which
     is made public as `judge_input(player, anchor, post)` over the case models. The order is
     shuffled with the seed.
   - `out/mapping.json` (outside `inputs/`) holds the seed, `prompt_version`
     (`PROMPT_VERSION`), `git_commit`, and per id `{kind: "case"|"candidate", id, tier}`. The
     sources are v1 cases (`--cases`) or candidates (`--candidates`, a JSONL of the new
     `CandidateCase(id, player, anchor, post, has_player_event, tier)` in `cases.py`).
   - `estimate_export(out)`: characters / 4 + 3,000 per call, with `REPLY_FORMAT` counted, as
     in extraction.
   - `REPLY_FORMAT = 'Reply with JSON only, no prose and no code fence: {"label": "supports" |
     "contradicts" | "related" | "unrelated"}'`, the same string as the shell `FORMAT`.
   - `import_verdicts(export, set_dir, judge, *, select, seed, force, now)` works in this
     order:
     1. It validates every verdict with `JudgeVerdict(JudgeOutput)` (`extra="forbid"`,
        `strict=True`). Any invalid file refuses the import with the list, and nothing is
        written. A `case` without a verdict also refuses it. A `candidate` without one is
        skipped and listed.
     2. A v1 case becomes the v1 case with `expected = prelabel = verdict`,
        `labelled_by = judge`, `v1_prelabel = <v1 expected>`, `reviewed = False` and the
        same split.
     3. Candidates go through `select_new_cases` (below), then `assign_split` (the existing
        seeded stratified 30% dev), with `labelled_by = judge`, `prelabel = expected` and
        `reviewed = False`.
     4. Ids already in the set are dropped before step 3's selection (so an extra draw selects
        only cases not yet in the set), unless `--force`. A `prelabels` entry is appended to
        `set.json`, and `composition_problems` is printed.
     5. The import refuses a selection that would take the set above `MAX_CASES` (130).
   - `select_new_cases(cases, size=55, seed, min_contradicts=12, unrelated_cap=0.4)` in
     `building.py` takes, in order:
     1. every `contradicts` with `has_player_event: false`;
     2. `contradicts` SQL-context pairs, until 12 `contradicts` (a margin over 8 for your
        flips);
     3. the other labels in turn, known misses first, with `unrelated` capped at 40% of
        `size`. A short label leaves its places to the next.

     With `size` at or below the `contradicts` available, it therefore takes `contradicts`
     only, which is how an extra draw (step 8) tops up a shortfall without other labels.
   - `scripts/session_judge.sh` gets the `corroboration` schema: the `FORMAT` sentence and a
     check that the verdict is a dict with exactly the key `label` and one of the four
     values. An invalid reply goes to `raw/<id>.invalid.json`, and the header comment names
     the schema.
   - The judge model (the SPEC's open question) is Opus at default effort for every input,
     recorded as `claude-opus-5-5 (Claude Code session)`. No pilot runs, because v1 has no
     owner label to pilot against, the inputs are short, and the count (about 300 calls) is
     below spec 018's 430. If the estimates exceed 3M tokens, the implementer escalates
     before judging. If Opus is rate-limited part way, Sonnet at medium effort judges the
     remaining inputs, named in the `prelabels` entry.
7. **The scratch database (AC3).** A new `app/corroboration/evaluation/scratch.py` holds the
   pieces below.
   - `guard_scratch_url(url, configured_url)` raises `ScratchError` in either case:
     - the URL's port is 5432;
     - its `(host, port, database)` equals the configured `DATABASE_URL`'s.

     The build commands take `--database-url` explicitly and never use `DATABASE_URL` for
     their engine. This is how "no command of the build reads or writes the Compose database"
     is enforced.
   - `derive_deadlines(fixtures) -> {gameweek: earliest kickoff − 90 min}`. The constant is
     `DEADLINE_LEAD = timedelta(minutes=90)`, with a comment: FPL's deadline, checked against
     retrieval v2's recorded GW6/GW7 deadlines.
   - `seed_scratch(engine, corpus, list_handles, players, teams, fixtures)` refuses a database
     whose `tweet` table is not empty and is not all `eval-corpus` rows. It inserts into
     `public`:
     - `season`;
     - `team`;
     - `player` (columns the snapshot lacks, such as `position`, get neutral values);
     - `gameweek` (`deadline_at` derived, `finished` true when `deadline_at` is before the
       corpus end);
     - `fixture`;
     - one `list_membership` row;
     - `tweet` rows with source `eval-corpus` (the field mapping of `load_eval_corpus`).

     Then it runs `check_source_flags`. A re-run on a seeded database skips the seeding, so
     the command resumes.
   - `extract_window(engine, runtime, since, until, max_cost_usd, clock, stop)` runs
     `extract_post` (production, `record_latency=False`, `NULL` tracing) for every source
     post with `since <= created_at < until` and no `extracted` row, newest first. It stops
     before a call when `SUM(extraction.cost_usd)` reaches `max_cost_usd`.
   - `build_costs(engine) -> (embeddings_usd, extraction_usd)` returns the two stored sums.
   - The CLI is
     `build-scratch --database-url URL [--corpus evals/retrieval/v2/corpus.jsonl]
     [--context evals/extraction/v2] [--retrieval-set evals/retrieval/v2] [--max-cost-usd 0.90]`.
     1. It seeds the database.
     2. It runs `index_missing` with the production embedding (`resolve_embedding(settings)`,
        `text-embedding-3-large`, untraced).
     3. It computes the windows with `gameweek_window(session, season, gw, "alerts")` for
        GW6 and GW7, which uses the production floor (`previous_gameweek_end` +
        `window_floor`, DECISIONS 2026-10-22).
     4. It extracts `[min start, max end)` with the production runtime (`DEFAULT_MODEL`,
        `PROMPT_VERSION`, fixtures for the match check, the default fallback).
     5. It prints the counts, the two costs and the windows.

     The expected windows, from the seeded calendar, are GW6 [2026-10-03T10:00Z,
     2026-10-10T10:00Z) and GW7 [2026-10-12T21:00Z, 2026-10-17T10:00Z). GW6's last kickoff is
     2026-10-12T19:00Z, plus `MATCH_LENGTH`. Retrieval v2 recorded 2026-10-10T10:00Z for GW7
     under the rule before 2026-10-22, so only the deadlines are cross-checked against it.
8. **Candidates (AC1, AC3).** `build_window_candidates(engine, embedder, windows, prices,
   tracer)` in `building.py` works per window `[start, end)`:
   - The claims per player come from `current_extractions(created_from=start,
     created_until=end, sources_only=True, fresh_lineups_only=True)`, as `sql_claims` reads
     them.
   - The anchors come from `_anchors` (the newest claim, which is production's anchor at the
     deadline, plus the newest claim of another event type, an earlier slot's anchor).
   - `claimed_post_ids(session, player, start, end)` gives the claimed posts.
   - `retrieval_candidates(engine, player, start, end, claimed_ids=…, aliases=…, prices=…,
     tracer=CostTracer(NULL_TRACER))` uses `retrieval/evaluation/labelling.CostTracer` for the
     query-embedding cost. The configuration is the production `SEARCH_MODE`,
     `QUERY_VARIANT`, `SEARCH_LIMIT` and `MAX_JUDGED`.
   - SQL-context pairs: up to `SQL_CONTEXT_PER_ANCHOR = 3` other claims, the ones
     `label_claim(anchor, claim) == "contradicts"` first, then the newest.
   - The tiers are:
     - `sql-contradicts`;
     - `retrieved-likely`: a retrieved post where the player has a contradicting claim in
       the window, or whose text matches the opposite kind of keywords (`LINEUP_KEYWORDS`
       from extraction against an availability anchor, a new `AVAILABILITY_KEYWORDS` regex
       against a `confirmed_starter` anchor);
     - `retrieved`;
     - `sql`.
   - `sample_candidates(cands, max_inputs=250, seed)` keeps every `sql-contradicts` and up
     to 150 `retrieved-likely`, then fills with seeded samples of `retrieved` and then
     `sql`.
   - Ids are `jc-{fpl_id}-{anchor_x_id}-{post_x_id}`, as in v1.
   - The volume (for the cost a month) is the sum over windows of the candidates of each
     listed player's newest anchor, × `ALERT_SLOTS = 2` (digest and news,
     `ALERT_SLOTS_MINUTES` default, with a comment; corroboration may not import `alerts`),
     ÷ the number of windows × `GAMEWEEKS_PER_MONTH = 4`.
   - The CLI is `build-candidates --database-url URL --gameweek 6 --gameweek 7 --set
     evals/corroboration/v2 --out FILE [--max-inputs 250] [--seed 19] [--force]`. It writes
     the candidates file and `set.json` (`version`, `list_handles`, `build`). The costs come
     from `build_costs` plus the query embeddings, the models from the scratch rows. It
     refuses an existing `build` without `--force`. With `--force` (an extra draw) it keeps
     the recorded windows and volume and adds the new draw's query-embedding cost to
     `query_embeddings_cost_usd`, so no spend drops out of the record.
9. **The rule (AC12).** A new `app/corroboration/evaluation/selection.py` holds
   `choose_judge(runs) -> str | None`:
   - a `-r2` run replaces the model's first run;
   - passing runs are those with `verdict.passed`, and the cheapest by
     `projected_monthly_cost_pln` wins;
   - with none passing, the eligible runs are those with a cost of at most
     `MAX_MONTHLY_COST_PLN = 5.0` (a `None` cost is not eligible). Among them the winner has
     the most passed thresholds, then the lower false-support rate, then the cheaper cost.
10. **The default (AC12).**
    - Case 1, the winner is `DEFAULT_MODEL`: no configuration change. A test pins it.
    - Case 2: `CorroborationSettings.llm_judge_model: str = ""` (`LLM_JUDGE_MODEL`, following
      `LLM_MODEL` and `LLM_FALLBACK_MODEL`), and `DEFAULT_JUDGE_MODEL = <winner>` in
      `app/corroboration/config.py`.
      - `judge_model(settings)` returns the setting, or the default.
      - `build_runtime` builds the judge from `single_model_config(api_key,
        judge_model(settings), variable="LLM_JUDGE_MODEL")`, with no fallback (a failed judge
        call already leaves the post unjudged), and extraction keeps `resolve_llm`.
      - `evaluate`'s default model and the composition check use the judge's default.
      - The documents are `.env.example`, `docs/DEPLOYMENT.md`, PROJECT line 87, the README
        and ADR 0011.

### Patterns reused

- The export, estimate and import pattern: `app/extraction/evaluation/judging.py`.
- The run header: `app/core/eval_results.make_header`.
- The scratch container: `specs/017-retrieval-evaluation-run/PLAN.md`, step 4.
- The query-cost counter: `app/retrieval/evaluation/labelling.CostTracer`.
- The windows: `gameweek_window` from `app/retrieval/evaluation/players.py`.
- Indexing: `app/retrieval/indexing.index_missing`.
- Extraction: `app/extraction/service.extract_post`, with the runtime as
  `app/worker/cli.py` builds it.
- Readers: `app/retrieval/evaluation/dataset.load_corpus`,
  `app/extraction/linking.load_snapshot` and `app/extraction/fixtures.load_fixtures_snapshot`.
- These imports stay on the allowed edges of `tests/test_module_boundaries.py`, since
  corroboration may import retrieval, extraction and fpl.
- The six candidates: `tests/extraction/candidates.py::CANDIDATES`.

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 6, 8 | `tests/corroboration/evaluation/test_eval_set_v2.py::test_every_v1_case_in_v2_with_its_split`, `::test_at_least_45_new_cases_inside_their_window`; `tests/corroboration/evaluation/test_scratch.py::test_windows_from_the_seeded_calendar` |
| AC2 | 1, 4, 8, 9 | `tests/corroboration/evaluation/test_cases.py::test_a_set_missing_contradicts_is_reported`, `::test_too_few_contradicts_overall_and_in_test`, `::test_size_out_of_range`; `tests/corroboration/evaluation/test_judging.py::test_extra_draw_selects_only_new_contradicts_within_the_size_cap`; `tests/corroboration/evaluation/test_eval_set_v2.py::test_composition` |
| AC3 | 5, 6, 8 | `tests/corroboration/evaluation/test_scratch.py::test_refuses_the_configured_database_url`, `::test_refuses_port_5432`, `::test_extraction_stays_in_the_window_and_stops_at_the_cap`; `tests/corroboration/evaluation/test_building.py::test_candidates_use_the_production_retrieval_and_fresh_lineups`; `tests/corroboration/evaluation/test_eval_set_v2.py::test_build_recorded_and_within_one_dollar` |
| AC4 | 1, 4, 8 | `tests/corroboration/evaluation/test_cases.py::test_v2_fields_round_trip`; `tests/corroboration/evaluation/test_judging.py::test_v1_case_keeps_split_and_haiku_label_unreviewed`, `::test_manifest_names_the_judge_prompt_and_commit`; `tests/corroboration/evaluation/test_eval_set_v2.py::test_every_case_records_its_prelabels` |
| AC5 | 4 | `tests/corroboration/evaluation/test_judging.py::test_export_one_input_per_case_with_neutral_ids`, `::test_mapping_outside_inputs`, `::test_estimate_counts_calls_and_tokens`; `tests/corroboration/evaluation/test_cli.py::test_estimate_prints_calls_and_tokens` |
| AC6 | 4 | `tests/test_session_judge.py::test_corroboration_writes_a_valid_verdict`, `::test_corroboration_rejects_an_invalid_verdict`, `::test_corroboration_sends_the_reply_format`; `tests/corroboration/evaluation/test_judging.py::test_import_refuses_an_invalid_verdict`, `::test_new_cases_selected_split_and_unreviewed` |
| AC7 | 3 | `tests/corroboration/evaluation/test_review_cli.py::test_review_shows_disagreements_first`, `::test_review_shows_both_prelabels_for_a_v1_case`, `::test_accept_marks_the_case_reviewed_on_disk_after_the_decision` (existing) |
| AC8 | 3, 9, 13 | `tests/corroboration/evaluation/test_eval_set_v2.py::test_every_case_reviewed`; `tests/corroboration/evaluation/test_review_cli.py::test_agreement_counts_differences_per_prelabeller`; `tests/test_docs.py::test_corroboration_report_v2_has_every_section` (agreement section) |
| AC9 | 2 | `tests/corroboration/evaluation/test_metrics.py::test_threshold_verdict_passes`, `::test_threshold_verdict_fails_on_accuracy`, `::test_threshold_verdict_fails_on_false_support`, `::test_threshold_verdict_fails_on_contradicts_recall`, `::test_threshold_verdict_fails_on_an_errored_run`; `tests/corroboration/evaluation/test_runner.py::test_evaluate_prints_and_writes_the_verdict` |
| AC10 | 2 | `tests/corroboration/evaluation/test_runner.py::test_the_result_carries_the_production_slice` |
| AC11 | 10, 11 | `tests/corroboration/evaluation/test_results_files.py::test_v2_one_test_run_per_candidate`, `::test_v2_spend_within_cap` |
| AC12 | 7, 12 | `tests/corroboration/evaluation/test_selection.py`; `tests/corroboration/evaluation/test_results_files.py::test_v2_rule_picks_the_judge_default`; `tests/corroboration/test_runtime.py::test_the_judge_runs_on_its_default_model` |
| AC13 | 10, 11 | `tests/corroboration/evaluation/test_results_files.py::test_v2_test_runs_share_one_prompt`; `tests/test_docs.py::test_corroboration_report_v2_has_every_section` (dev iterations) |
| AC14 | 13 | `tests/test_docs.py::test_corroboration_report_v2_has_every_section`; `tests/corroboration/evaluation/test_results_files.py::test_report_v2_names_every_test_run` |
| AC15 | 12, 14 | `tests/test_docs.py::test_decisions_cover_corroboration_set_v2_threshold_and_default` (and `::test_adr_0011_names_the_judge_default` in case 2) |
| AC16 | 14 | `tests/test_docs.py::test_roadmap_019_ticked`, `::test_backlog_12_and_47_removed`; `tests/test_readme.py::test_corroboration_commands_documented`, `::test_corroboration_section_links_the_report`; `tests/test_docs.py::test_evaluation_has_the_corroboration_procedure` |
| AC17 | 5, 8, 15 | the full verification command; `tests/corroboration/evaluation/test_eval_set_v2.py::test_authors_are_watched_accounts`; `tests/corroboration/evaluation/test_scratch.py::test_refuses_the_configured_database_url` |

## Steps

All commands run from `backend/` (`cd /home/czarny/Projects/gaffers-presser/backend`).
"Verify" = `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`.
`$S` = a `019/` directory in the implementer's session scratchpad (never in the repository).

- [x] 1. **Case format v2, set paths and the v2 composition (AC2, AC4 format).** `iterations: 1`
      - Files: `app/corroboration/evaluation/cases.py` (approach items 1–3) and
        `app/corroboration/evaluation/cli.py` (the `build-cases` output default becomes
        `V1_CASES_PATH`; `review` and `evaluate` default to `DEFAULT_CASES_PATH`).
      - Tests first, in `tests/corroboration/evaluation/test_cases.py`:
        - `test_v2_fields_round_trip`;
        - `test_a_v1_line_without_the_new_fields_loads`;
        - `test_a_set_missing_contradicts_is_reported` (replaces
          `test_a_missing_contradicts_label_is_tolerated_until_backlog_12`);
        - `test_too_few_contradicts_overall_and_in_test`;
        - `test_size_out_of_range` (the 90 and 130 bounds);
        - `valid_set` builds 100 cases, with 10 `contradicts` (7 in test) and a 0.3 dev share.
      - Rewrite `tests/corroboration/evaluation/test_eval_set.py` as `test_v1_set_is_kept_as_built`:
        51 cases, 15 dev / 36 test, every `labelled_by == PRELABEL_MODEL`, no `contradicts`.
      - In `test_results_files.py`, pin `V1_CASES_PATH` and the literal
        `"corroboration_judge@1"` instead of `PROMPT_VERSION`, because the v1 run stays valid
        after a prompt bump.

      Automatic verification:
      `uv run pytest -q tests/corroboration/evaluation -n auto`, then Verify.
- [x] 2. **Metrics, the verdict, the cost a month and the per-set result paths (AC9, AC10).** `iterations: 1`
      - Files: `app/corroboration/evaluation/metrics.py`, `runner.py`, `cli.py` (`evaluate`)
        (approach item 4) and `/home/czarny/Projects/gaffers-presser/.gitignore`.
      - Tests first, in `tests/corroboration/evaluation/test_metrics.py`:
        - `test_threshold_verdict_passes` (at the boundaries: 0.80, 0.10, 0.70);
        - `test_threshold_verdict_fails_on_accuracy`;
        - `test_threshold_verdict_fails_on_false_support`;
        - `test_threshold_verdict_fails_on_contradicts_recall` (also `None` with no expected
          `contradicts`);
        - `test_threshold_verdict_fails_on_an_errored_run`.
      - Tests first, in `tests/corroboration/evaluation/test_runner.py`:
        - `test_the_result_carries_the_production_slice` (only `has_player_event: false`
          outcomes, with its own metrics);
        - `test_projected_monthly_cost` (from a `set.json` with `judge_calls_per_month`, and
          `None` without it or without `usd_pln_rate`);
        - `test_evaluate_prints_and_writes_the_verdict`;
        - `test_v2_result_paths` (test → `results/v2/test-<slug>.json`, dev →
          `results/v2/dev/dev-<slug>.json`, v1 unchanged).

      Automatic verification:
      `uv run pytest -q tests/corroboration/evaluation/test_metrics.py tests/corroboration/evaluation/test_runner.py`,
      then Verify.
- [x] 3. **The review order, both pre-labels and `agreement` (AC7, AC8 tooling).** `iterations: 0`
      - Files: `app/corroboration/evaluation/cases.py` (`render_case`, `prelabel_agreement`)
        and `cli.py` (`review` ordering, the new `agreement` command) (approach item 5).
      - Tests first, in `tests/corroboration/evaluation/test_review_cli.py`:
        - `test_review_shows_disagreements_first`;
        - `test_review_shows_both_prelabels_for_a_v1_case` (both labels, the `DISAGREE`
          marker);
        - `test_a_new_case_shows_one_prelabel`;
        - `test_agreement_counts_differences_per_prelabeller` (reviewed cases only).

        The existing accept, change and quit tests stay green.

      Automatic verification:
      `uv run pytest -q tests/corroboration/evaluation/test_review_cli.py tests/corroboration/evaluation/test_cases.py`,
      then Verify.
- [x] 4. **The session judge: export, estimate, import and the shell schema (AC4 manifest,
      AC5, AC6).** `iterations: 1`
      - Files:
        - new: `app/corroboration/evaluation/dataset.py` (`SetManifest`, `PrelabelEntry`,
          `BuildInfo`, `Selection`, `load_manifest`, `write_manifest`) and `judging.py`
          (approach item 6);
        - changed: `runner.py` (`judge_input` made public), `building.py`
          (`select_new_cases`), `cases.py` (`CandidateCase`), `cli.py`
          (`export-judge-inputs --out --seed (--cases PATH | --candidates PATH)`,
          `estimate --export`, `import-judge --export --set --judge-model [--select N]
          [--seed] [--force]`) and `/home/czarny/Projects/gaffers-presser/scripts/session_judge.sh`.
      - Tests first, in `tests/corroboration/evaluation/test_judging.py`:
        - `test_export_one_input_per_case_with_neutral_ids` (ids match `I\d{4}`; each input
          equals `render_input(judge_input(...))`; `judge-prompt.md` equals
          `JUDGE_PROMPT.text`; no input holds a label);
        - `test_mapping_outside_inputs`;
        - `test_estimate_counts_calls_and_tokens`;
        - `test_import_refuses_an_invalid_verdict` (an extra key, an unknown label, not JSON:
          nothing is written);
        - `test_import_refuses_a_missing_v1_case_verdict`;
        - `test_v1_case_keeps_split_and_haiku_label_unreviewed`;
        - `test_new_cases_selected_split_and_unreviewed`;
        - `test_selection_prefers_retrieved_contradicts_then_sql_pairs`;
        - `test_extra_draw_selects_only_new_contradicts_within_the_size_cap` (ids already in
          the set are dropped before selection; `--select 3` takes 3 `contradicts` only; a
          selection above 130 cases is refused);
        - `test_manifest_names_the_judge_prompt_and_commit`.
      - Tests first, elsewhere:
        - in `tests/test_session_judge.py`: `test_corroboration_writes_a_valid_verdict`,
          `test_corroboration_rejects_an_invalid_verdict` (moved to `raw/<id>.invalid.json`,
          and a rerun judges only it) and `test_corroboration_sends_the_reply_format` (equal
          to `judging.REPLY_FORMAT`);
        - new `tests/corroboration/evaluation/test_cli.py`:
          `test_help_lists_every_evaluation_command` and
          `test_estimate_prints_calls_and_tokens`.

      Automatic verification:
      `uv run pytest -q tests/corroboration/evaluation tests/test_session_judge.py -n auto`,
      then Verify.
- [x] 5. **The scratch database: guard, seed, index and extract (AC3, AC17 guard).** `iterations: 1`
      - Files: new `app/corroboration/evaluation/scratch.py` (approach item 7) and `cli.py`
        (`build-scratch`).
      - Tests first, in `tests/corroboration/evaluation/test_scratch.py`, on the `db` fixture
        container, with a fake embedder and a scripted `FakeChatModel` spec from
        `tests/extraction/fakes.py`:
        - `test_refuses_port_5432`;
        - `test_refuses_the_configured_database_url`;
        - `test_deadlines_are_first_kickoff_minus_90_minutes` (on the real
          `evals/extraction/v2/fixtures.json`: GW6 2026-10-10T10:00Z and GW7
          2026-10-17T10:00Z, equal to retrieval v2's recorded deadlines);
        - `test_seed_loads_corpus_players_fixtures_and_gameweeks` (a small corpus, the real
          snapshots);
        - `test_seed_refuses_a_database_with_other_posts`;
        - `test_windows_from_the_seeded_calendar` (GW6 [2026-10-03T10:00Z,
          2026-10-10T10:00Z), GW7 [2026-10-12T21:00Z, 2026-10-17T10:00Z));
        - `test_extraction_stays_in_the_window_and_stops_at_the_cap`;
        - `test_rerun_resumes_without_reseeding_or_reextracting`;
        - `test_build_costs_sum_the_stored_costs`.

      Automatic verification:
      `uv run pytest -q tests/corroboration/evaluation/test_scratch.py tests/test_module_boundaries.py`,
      then Verify.
- [x] 6. **The GW6/GW7 candidates and the build metadata (AC1, AC3).** `iterations: 1`
      - Files: `app/corroboration/evaluation/building.py` (`build_window_candidates`,
        `sample_candidates`, `AVAILABILITY_KEYWORDS`, the volume constants) and `cli.py`
        (`build-candidates`) (approach item 8). The v1 functions stay.
      - Tests first, in `tests/corroboration/evaluation/test_building.py` (`db`):
        - `test_candidates_use_the_production_retrieval_and_fresh_lineups` (a stale
          `confirmed` line-up is neither anchor nor candidate; a claimed post never comes
          back from retrieval; at most `MAX_JUDGED` per anchor);
        - `test_candidates_inside_their_window` (anchor and post);
        - `test_sql_pairs_contradicts_first`;
        - `test_tiers_and_sampling_deterministic` (every `sql-contradicts` kept, the cap
          honoured);
        - `test_volume_counts_newest_anchor_candidates_times_slots`;
        - `test_build_candidates_writes_the_build_section` (windows, costs, models, volume),
          `test_build_candidates_refuses_an_existing_build_without_force` and
          `test_build_candidates_force_adds_the_query_cost`.

      Automatic verification:
      `uv run pytest -q tests/corroboration/evaluation/test_building.py`, then Verify.
- [x] 7. **The selection rule (AC12 rule).** `iterations: 0`
      - Files: new `app/corroboration/evaluation/selection.py` (approach item 9).
      - Tests first, in `tests/corroboration/evaluation/test_selection.py`:
        - `test_cheapest_passing_wins`;
        - `test_no_pass_most_thresholds_then_lower_false_support_then_cheaper`;
        - `test_no_pass_excludes_runs_over_5_pln_or_without_a_cost`;
        - `test_repeat_replaces_the_first_run`;
        - `test_no_eligible_run_gives_none`.

      Automatic verification:
      `uv run pytest -q tests/corroboration/evaluation/test_selection.py`, then Verify.
- [x] 8. **Build set v2 with session pre-labels (AC1–AC4 data, AC17 data).** `iterations: 1`
      - Write `tests/corroboration/evaluation/test_eval_set_v2.py` first:
        - `test_schema`;
        - `test_every_v1_case_in_v2_with_its_split`;
        - `test_at_least_45_new_cases_inside_their_window` (each new case's anchor and post in
          one `set.json` window, and the windows equal the step 5 constants);
        - `test_every_case_records_its_prelabels` (`prelabel` and `labelled_by` set; a v1
          case's `v1_prelabel` equals its v1 `expected`);
        - `test_manifest_names_the_session_judge`;
        - `test_build_recorded_and_within_one_dollar` (the embeddings, extraction and query
          costs at most 1.00);
        - `test_authors_are_watched_accounts` (new cases' anchor and post `author_handle` in
          `list_handles`);
        - `test_composition`;
        - `test_every_case_reviewed`.

        It is red until step 9.
      - The container (a test container, never Compose):
        ```
        docker run -d --rm --name presser-eval-019 -e POSTGRES_USER=eval -e POSTGRES_PASSWORD=eval -e POSTGRES_DB=eval -p 55433:5432 pgvector/pgvector:pg16
        ```
        Wait for `docker exec presser-eval-019 pg_isready -U eval`. Then run
        `DATABASE_URL=postgresql+psycopg://eval:eval@localhost:55433/eval uv run alembic upgrade head`.
        Below, `URL` = that URL. In spec 017 a permission rule refused this migration on the
        scratch container (PLAN 017, Owner decisions 2026-10-22); a refusal here → escalate
        (`permission`), never migrate any other way.
      - The build:
        - `env -u TWEET_SOURCE uv run python -m app.corroboration.evaluation build-scratch --database-url $URL --max-cost-usd 0.90`
          runs in the background, about an hour, and can be resumed;
        - then
          `env -u TWEET_SOURCE uv run python -m app.corroboration.evaluation build-candidates --database-url $URL --gameweek 6 --gameweek 7 --set evals/corroboration/v2 --out $S/candidates.jsonl --max-inputs 250 --seed 19`.

        If the cap stops the extraction before the window is done, escalate (`decision`),
        because a partial extraction is not what production would have had.
      - Export and estimate:
        - `export-judge-inputs --cases evals/corroboration/v1/cases.jsonl --out $S/judge-v1 --seed 19`;
        - `export-judge-inputs --candidates $S/candidates.jsonl --out $S/judge-new --seed 19`;
        - `estimate --export` for each. If the sum exceeds 3M tokens, escalate (`decision`)
          before judging.
      - Judge:
        `../scripts/session_judge.sh $S/judge-v1 6 opus corroboration` and the same for
        `$S/judge-new`. Rerun each until every verdict is in. A refused `claude -p` or
        `docker` → escalate (`permission`).
      - Import:
        - `import-judge --export $S/judge-v1 --set evals/corroboration/v2 --judge-model "claude-opus-5-5 (Claude Code session)"`;
        - `import-judge --export $S/judge-new --set evals/corroboration/v2 --judge-model "claude-opus-5-5 (Claude Code session)" --select 55 --seed 19`.
      - If `composition_problems` reports a `contradicts` shortfall on the pre-labels, draw one
        more candidate export (`build-candidates --force --seed 20 --max-inputs 150`, exported
        and judged the same way) and import it with `--select` equal to the shortfall plus 2,
        capped so the set stays at or under 130 cases. Selection then takes only new
        `contradicts`. More than two extra draws → escalate (`decision`).
      - Commit the set unreviewed (`data: build corroboration set v2 candidates`).
      - Keep the container running until step 15. The data steps use only the files.

      Automatic verification:
      `uv run pytest -q tests/corroboration/evaluation/test_eval_set_v2.py --deselect tests/corroboration/evaluation/test_eval_set_v2.py::test_every_case_reviewed`
      green, `test_composition` included (the extra draws above exist to make it pass on the
      pre-labels; if they cannot, the step has already escalated), then Verify with
      `--deselect tests/corroboration/evaluation/test_eval_set_v2.py::test_every_case_reviewed`.
- [ ] 9. **Your review of set v2 (AC7, AC8): the implementer stops here.**
      - End the run with `RESULT: ESCALATE` (`KIND: decision`) and wait. The message gives:
        - the counts: v1 cases, new cases per split, and the v1 cases where the two
          pre-labels disagree;
        - the command `cd backend && uv run python -m app.corroboration.evaluation review`
          (it walks the unreviewed cases, disagreements first, and saves each decision at
          once);
        - a reminder of the labels: line-up news counts only for the next Premier League
          match, and a SQL-context pair is labelled like any other post.
      - On resume:
        - run `test_eval_set_v2.py` in full;
        - if the composition fails after the review (fewer than 8 `contradicts`, or 5 in
          test), draw more as in step 8 (the session scratchpad `$S` of step 8 does not survive
          the restart, so the draw is rebuilt from the still running container) and escalate
          again for their review;
        - run `agreement` as a check; step 13 runs it again for the report, because `$S` may
          not survive a later restart;
        - commit `data: review corroboration set v2`, unless the owner has committed it.

      Automatic verification:
      `uv run pytest -q tests/corroboration/evaluation/test_eval_set_v2.py && uv run python -m app.corroboration.evaluation agreement`,
      then Verify.
- [ ] 10. **Dev iterations and the frozen prompt (AC13).** Use the paid key from
      `backend/.env` (with `USD_PLN_RATE` set).
      - Iteration 0: for each model in `tests/extraction/candidates.py::CANDIDATES`, run
        `env -u TWEET_SOURCE uv run python -m app.corroboration.evaluation evaluate --split dev --model M --output evals/corroboration/results/v2/dev/dev-<slug>-i0.json`.
      - Edit `app/content/prompts/corroboration_judge.md` only for dev errors that the
        prompt's rules explain and that at least two models share. Never edit it from test
        cases. On the first edit set `version: 2`.
      - Stop at four iterations, or when nothing prompt-related is left. Re-run luna, plus the
        models that failed, per iteration (`-iN`).
      - Record every run in `specs/019-corroboration-evaluation-v2/dev-iterations.md`: the
        iteration, model, accuracy, false-support, `contradicts` recall, verdict, cost and the
        change made, and keep one line `Dev spend: <x> USD` (the sum of every dev result
        file's `cost_usd`) up to date, because the dev result files are gitignored and
        `test_v2_spend_within_cap` reads this line. Escalation restarts the implementer, and
        step 13 reads this log.
      - If the prompt changed, adapt `tests/content/test_prompts.py` to the version and
        commit `feat: freeze corroboration judge prompt v2`. Otherwise commit the log alone,
        with "no change". If the build plus the dev runs reach $4, escalate (`decision`).

      Automatic verification:
      `uv run pytest -q tests/content tests/corroboration -n auto`, then Verify.
- [ ] 11. **The six test runs (AC11, AC13).**
      - Write these first, in `tests/corroboration/evaluation/test_results_files.py`:
        - `test_v2_one_test_run_per_candidate`: `results/v2/test-*.json` holds one file per
          `CANDIDATES` model, plus at most one `-r2` each. Each header has `set_version
          "v2"`, `model`, `prompt_version`, `reasoning_effort`, `git_commit` and
          `git_dirty false`. Each file has `verdict` and `production`;
        - `test_v2_test_runs_share_one_prompt`: one `prompt_version`, equal to
          `PROMPT_VERSION`;
        - `test_v2_spend_within_cap`: `set.json` build costs plus the `Dev spend: <x> USD`
          line of `specs/019-corroboration-evaluation-v2/dev-iterations.md` plus every test
          file's `cost_usd`, at most 5.00 (AC11 counts the build, the dev runs and the test
          runs).
      - Run, for each model:
        `env -u TWEET_SOURCE uv run python -m app.corroboration.evaluation evaluate --split test --model M`.
        A run with an errored case gets one repeat,
        `--output evals/corroboration/results/v2/test-<slug>-r2.json`.
      - Commit the result files (`data: add corroboration test runs on set v2`).

      Automatic verification:
      `uv run pytest -q tests/corroboration/evaluation/test_results_files.py`, then Verify.
- [ ] 12. **The judge's default (AC12).**
      - Write
        `tests/corroboration/evaluation/test_results_files.py::test_v2_rule_picks_the_judge_default`
        first: `choose_judge` over the final v2 test runs equals the configured judge default.
      - Then
        `tests/corroboration/test_runtime.py::test_the_judge_runs_on_its_default_model`:
        `build_runtime` with a fake key and no model setting gives `runtime.judge.model`
        equal to that default.
      - Case 1 (the rule gives `DEFAULT_MODEL`): no product change. The tests pin
        `DEFAULT_MODEL`.
      - Case 2: the changes of approach item 10:
        - `app/corroboration/config.py`, `runtime.py`;
        - `evaluation/cli.py` (the default model) and `cases.py` (the composition default);
        - `.env.example`, `docs/DEPLOYMENT.md` (the variable), `docs/PROJECT.md` line 87;
        - new `docs/adr/0011-corroboration-judge-model.md` (the options table from the test
          runs, the rule and the new setting) and its row in `docs/adr/README.md`.

        Add `tests/corroboration/test_runtime.py::test_llm_judge_model_overrides_and_extraction_keeps_llm_model`,
        `tests/test_env_example.py` (the new key, if that test lists keys) and
        `tests/test_docs.py::test_adr_0011_names_the_judge_default`.
      - Record the case in `## Deviations` only if case 2 needs more than approach item 10.

      Automatic verification:
      `uv run pytest -q tests/corroboration tests/test_env_example.py tests/test_docs.py -n auto`,
      then Verify.
- [ ] 13. **The report (AC3 spend, AC8, AC13, AC14).**
      - File: `docs/reports/corroboration-eval-v2.md`, with these sections:
        - Summary;
        - The set: labels by split, `has_player_event`, the source windows, the build and its
          spend;
        - Pre-labels and the owner's agreement: the output of a fresh `agreement` run, Opus
          against you and haiku against you on v1;
        - Threshold;
        - Dev iterations, from `dev-iterations.md`;
        - Test results: one row per model with accuracy, per-label precision and recall,
          false-support, the production slice's accuracy and false-support, the verdict per
          threshold, PLN a month (with the volume and its derivation), errored cases and
          cost;
        - The rule and the winner;
        - Spend: build, dev and test, against $5;
        - Threats to validity: set size, vendor overlap (the Opus pre-labels, haiku's v1
          labels and the haiku candidate), the SQL-context pairs and the estimated volume;
        - Recommendations, as BACKLOG proposals only.
      - Tests first:
        - `tests/test_docs.py::test_corroboration_report_v2_has_every_section`;
        - `tests/corroboration/evaluation/test_results_files.py::test_report_v2_names_every_test_run`
          (every final test file's model and its accuracy at 3 decimals appear).

      Automatic verification:
      `uv run pytest -q tests/test_docs.py tests/corroboration/evaluation/test_results_files.py`,
      then Verify.
- [ ] 14. **Documents (AC15, AC16).**
      - `docs/DECISIONS.md`, three rows dated on the day:
        - set v2: the source, a test container built from the retrieval v2 corpus, the
          session pre-labels with v1 relabelled blind, the full review, the composition
          minimums;
        - the pass threshold, with the production slice reported apart;
        - the judge's default model and setting, citing the report (and ADR 0011 in case 2).
      - `README.md`'s corroboration section:
        - set v2 reviewed, linking `docs/reports/corroboration-eval-v2.md`;
        - the new commands (`build-scratch`, `build-candidates`, `export-judge-inputs`,
          `estimate`, `import-judge`, `review`, `agreement`, `evaluate`);
        - the "waiting for the owner's review" wording goes.
      - `docs/EVALUATION.md`: a new "The corroboration procedure" section (the scratch
        container, the build, export and estimate, judging, import, review and agreement, dev,
        test, the threshold and the rule). "Other evaluations" drops the corroboration
        paragraph and lists the four schemas.
      - `docs/ROADMAP.md`: the item is ticked and links the report (and ADR 0011 in case 2).
      - `docs/BACKLOG.md`: rows 12 and 47 are removed, and the report's recommendations become
        rows with triggers.
      - Tests first:
        - `tests/test_docs.py::test_roadmap_019_ticked` (this replaces the 019 half of
          `test_roadmap_018_ticked_and_019_open`, which keeps its 018 assertions);
        - `::test_backlog_12_and_47_removed` (this replaces the "12 narrowed" part of
          `test_backlog_23_46_54_removed_and_12_narrowed`);
        - `::test_decisions_cover_corroboration_set_v2_threshold_and_default`;
        - `::test_evaluation_has_the_corroboration_procedure`;
        - in `tests/test_readme.py`, update `test_corroboration_commands_documented` and add
          `test_corroboration_section_links_the_report`.

      Automatic verification: `uv run pytest -q tests/test_docs.py tests/test_readme.py`, then
      Verify.
- [ ] 15. **Close (AC17).**
      - Stop the container: `docker stop presser-eval-019`.
      - Check that nothing private leaked:
        `git diff origin/main --stat` lists no `.env` file, and
        `grep -rn "league_id\|manager" evals/corroboration/v2/cases.jsonl` finds nothing.
      - Run the full verification command.

      Automatic verification: Verify (the full command), green.

## Risks and traps

- **The owner's stop.** Step 9 ends the run with an escalation. On resume, step 9's checks run
  first. A label shortfall after the review loops back through step 8, with one more
  escalation.
- **Compose is production.** Every build command takes `--database-url` and refuses port 5432
  and the configured `DATABASE_URL`, and only the container on 55433 is migrated. `review`,
  `agreement`, `evaluate` and the judging commands need no database. Never run
  `alembic upgrade` without the `DATABASE_URL=` prefix.
- **The GW7 window differs from retrieval v2's.** That set recorded the rule from before
  2026-10-22. The plan uses the current production floor (GW7 from 2026-10-12T21:00Z) and
  cross-checks only the deadlines.
- **Few real contradictions.** The tiers put the likely ones first, and SQL-context pairs fill
  the gap (an owner decision). `select_new_cases` keeps 12 to absorb your flips.
- **Extraction runtime and cost.** About 2,000 posts run sequentially, so the command runs in
  the background and resumes. The $0.90 cap stops it, and a stopped build escalates instead
  of building from a partial window.
- **Session judge.** `claude -p` runs outside the repository. Opus may be rate-limited, in
  which case Sonnet finishes, named in the manifest. An invalid reply is judged again by a
  fresh call.
- **v1 tests.** `test_results_files.py` must pin v1's path and `corroboration_judge@1`, or a
  prompt bump breaks a valid historical run. `test_docs.py` has two tests that assert 019 is
  open and #12 is narrowed, and step 14 rewrites them.
- **The composition default check if haiku wins.** Only `labelled_by` is checked, and the
  report names the overlap.
- **Monthly volume is an estimate.** It is the candidates of the newest anchor × 2 slots × 4
  gameweeks a month, which overstates news slots without new posts. The report says so, and
  the 5 PLN line matters only when no model passes.
- **`git_dirty`.** Test runs must start from a clean tree (`test_v2_one_test_run_per_candidate`
  asserts `git_dirty false`), so commit before step 11.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

- `uv run python -m app.corroboration.evaluation --help` lists `build-cases`,
  `build-scratch`, `build-candidates`, `export-judge-inputs`, `estimate`, `import-judge`,
  `review`, `agreement` and `evaluate`.
- `uv run python -m app.corroboration.evaluation agreement` prints both pre-labellers' counts
  over every case.
- `uv run python -m app.corroboration.evaluation evaluate --split test --model <winner> --output $S/e2e.json`
  prints the production block and `verdict: PASS` (or the verdict the report gives). It
  writes `verdict` and `production` into `$S/e2e.json`.
- `uv run pytest -q tests/corroboration/evaluation/test_eval_set_v2.py tests/corroboration/evaluation/test_results_files.py`
  is green.
- Verify (the full command) is green.

Record the results here when done.

### Manual (performed by the owner)

n/a — the only hands-on part, the review of set v2, is requested by the step 9 escalation and proved by `test_every_case_reviewed`

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto` fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, `docs/EVALUATION.md`, `README.md`,
      `docs/BACKLOG.md` (and in case 2 ADR 0011, `docs/DEPLOYMENT.md`, `docs/PROJECT.md`,
      `.env.example`)
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

## Review log

### 2026-10-28 — plan review

Findings:

- `major` — Extra draws could break AC2's size bound and add the wrong labels: step 8 imported
  an extra draw "the same way" (`--select 55`), which would push the set past 130 cases and
  add non-`contradicts` cases, and the import skipped ids already in the set only after
  selection, so a re-draw could select only cases already present. Also `build-candidates
  --force` rewrote the `build` section, so the first draw's query-embedding spend dropped out.
  Changed: approach item 6 drops existing ids before selection and refuses a set above 130;
  a `size` at or below the available `contradicts` takes `contradicts` only; item 8's
  `--force` keeps windows and volume and adds the query cost; step 8 imports an extra draw
  with `--select` = shortfall + 2, capped at 130; new tests
  `test_extra_draw_selects_only_new_contradicts_within_the_size_cap` (step 4, AC2 row) and
  `test_build_candidates_force_adds_the_query_cost` (step 6).
- `major` — AC11's proving test missed the dev spend: AC11 caps the build, dev and test runs
  at $5, but `test_v2_spend_within_cap` summed only the build and the test files, and the dev
  result files are gitignored. Changed: step 10 keeps a `Dev spend: <x> USD` line in
  `dev-iterations.md`, step 11's test adds it, and the matrix row for AC11 names step 10.
- `minor` — Step 8 ran `alembic upgrade head` on the scratch container without the spec 017
  precedent, where a permission rule refused exactly that. Changed: a refusal escalates
  (`permission`), and no other migration route is used.
- `minor` — Step 8's automatic verification was self-contradictory ("green", yet
  `test_composition` "may fail") and its Verify deselected the whole v2 test file. Changed:
  `test_composition` must be green after step 8, and Verify deselects only
  `test_every_case_reviewed`.
- `minor` — Step 9 saved the `agreement` output in the session scratchpad, which a restart
  (the step 9 and step 10 escalations) does not carry over, and an extra draw after the
  review relied on step 8's `$S` files. Changed: step 13 runs `agreement` afresh, and step 9
  says the draw is rebuilt from the running container.

Checked and found correct:

- Every AC1–AC17 has steps and a proving test; the matrix matches the steps after the changes
  above.
- The cited code exists with the signatures the plan uses: `current_extractions(created_from,
  created_until, sources_only, fresh_lineups_only)`, `claimed_post_ids`,
  `retrieval_candidates`, `_anchors`, `assign_split`, `runner._judge_input`, `extract_post`
  (`record_latency`), `index_missing`, `gameweek_window(..., "alerts")` (built on
  `previous_gameweek_end` and `window_floor`, the production floor), `session_judge.sh DIR
  PARALLEL MODEL SCHEMA`.
- The scratch database: the build commands take `--database-url`, refuse port 5432 and the
  configured `DATABASE_URL`, and `alembic/env.py` takes the `DATABASE_URL` env variable over
  `.env` through `load_settings`; the Compose database is never touched.
- The token estimate formula and the 3M-token escalation follow spec 018
  (`CALL_OVERHEAD_TOKENS = 3000`); about 300 calls fit under it.
- The dev results gitignore follows the existing `backend/evals/extraction/results/*/dev/`
  pattern.
- Composition checks only `labelled_by` against the judge's default; AC4 separates "its
  pre-label" from haiku's original label, so `v1_prelabel` is a comparison, and the vendor
  overlap is a named threat (AC14).
- The owner's review is a step 9 escalation, as in spec 018, and the manual section is a
  justified `n/a`.
- The owner summary is consistent: no new dependency, no data migration (alembic runs only on
  a throwaway container), `LLM_JUDGE_MODEL` only in case 2. The plan is in English, as
  `language` requires.
- Spend: embeddings (~$0.02) plus extraction capped at $0.90 stays under $1; the $4
  build-plus-dev escalation keeps the $5 cap.

Decision: the plan is ready for implementation — every AC is covered by a step and a proving
test, the fixes above were made in the plan itself, and it adds no dependency or migration
that the owner has not ruled out.

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

- `minor` — `tests/test_docs.py::test_roadmap_018_ticked_and_019_open` was red before this spec's work (it matched the ROADMAP item by the text `spec 019`, which the item no longer contains); step 1 matches it by the SPEC link instead, ahead of step 14's rewrite.
- `minor` — `build_window_candidates` reads claims with `sources_only=True` (the plan names it for the anchors); an anchor can therefore come from an account outside the list whose post a list member quotes, so `test_eval_set_v2.py` checks public handles and that most anchors are list accounts instead of "every author is on the list".
- `minor` — `export_inputs` takes `cases=`, `candidates=` and the two source paths (not a `sources` list), and `import_verdicts` takes no `select`-time `min_contradicts`; the manifest `selection` records the default 12.

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_
