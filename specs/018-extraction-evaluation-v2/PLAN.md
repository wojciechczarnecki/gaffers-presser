# PLAN 018 — Extraction evaluation v2: GW6 line-up leaks, prompt v5 and a match check

## Owner summary

- **Approach:** Code first, then data, then runs. The evaluation commands move to
  `python -m app.extraction.evaluation`. A third LangGraph node, `match_check`, runs after
  linking. It makes one call per post and club for linked line-up events. It gets the club's
  previous and next Premier League fixture from the FPL data, and it stores a true, false or
  null answer in a new nullable column `extraction_event.for_next_pl_match` (migration 0014).
  `current_extractions(fresh_lineups_only=True)` drops a false answer through one predicate,
  `kept_at_read`, which `evaluate` also uses. Set v2 is v1 relabelled plus about 80 new cases
  from the retrieval v2 corpus. The Claude Code session judge pre-labels them (Sonnet at
  medium effort, if a pilot on v1's dev split holds; Opus otherwise). Then the owner reviews
  the changed and new cases, prompt v5 is tuned on dev, the six models run on test, and the
  selection rule picks the default and the fallback.
- **Main risks:** the implementation stops twice for the owner. The first stop is the review
  of the set (step 11), which is the owner's own work, so the implementer escalates and waits.
  The second is a possible permission refusal of `claude -p` or of the read-only Compose reads.
  Composition targets may also need a second candidate draw (at least 8 other-match line-ups,
  at least 15 test line-up events). The default model may change, and with it ADR 0006's
  default (ADR 0010 then supersedes it).
- **New dependency:** no.
- **Data migration:** yes. Migration `0014` adds the nullable boolean
  `extraction_event.for_next_pl_match`, and existing rows stay `null`. It was accepted in
  SPEC → "Owner decisions" (2026-10-25). Agents run it only on test containers, and the owner
  runs `alembic upgrade head` on Compose.
- **Manual scenarios for the owner:** 1. After migrating Compose, re-extract one #54 post
  (City v AEK) and check that its line-up events are stored `false` and are missing from the
  filtered read.

## Approach

### What the plan rests on

- `specs/018-extraction-evaluation-v2/SPEC.md`: read in full, including `## Owner decisions`.
- `docs/CONVENTIONS.md`: read in full. The points this plan uses:
  - prompts live in `app/content/`;
  - tests use a fake LLM, and evaluations run outside `pytest`;
  - database tests run on a container;
  - agents run `env -u TWEET_SOURCE` near `backend/.env`;
  - a commit after every green step.
- `docs/DECISIONS.md`: searched for "extraction", "line-up", "LINEUP_MAX_LEAD", "judge",
  "2026-10-2". The search found:
  - 2026-10-24 (the 60 h filter at read time; stored events unchanged; the evaluation
    builders apply the same filter);
  - 2026-10-21 (the relevance rule for other matches' line-ups);
  - 2026-10-22 (the format of the ADR 0009 row);
  - the table's append-only rule.
- `docs/ROADMAP.md`: searched for "018". It found the Stage 2 item at lines 80–88 that is to
  be split.
- `docs/BACKLOG.md`: searched for rows 12, 23, 46, 47, 54 and 55 and read them. Row 47's last
  column says "spec 018" and becomes "spec 019".
- `docs/EVALUATION.md`: read in full. The retrieval procedure is the pattern for export,
  estimate and import. "Other evaluations" is to be rewritten.
- `docs/DEPLOYMENT.md`: searched for "migration" and "app.extraction". It found the
  per-migration notes (`0011`–`0013`, lines 221–234) and the old command paths at lines 90–92.
- `docs/adr/0006-default-extraction-model-openrouter.md`: read lines 1–40 (the options table,
  the default and the fallback rule). `docs/adr/` was listed, and the next number is 0010.
- `docs/reports/extraction-eval-v1.md`: read lines 15–40 (the format of the label comparison
  and the agreement of the v1 pre-labels: dev F1 0.739, test 0.882).
- `README.md`: read lines 30–60 and 130–200 (the results section and the extraction commands).
- Code read in full:
  - `app/extraction/`: `flow.py`, `schemas.py`, `models.py`, `store.py`, `service.py`,
    `linking.py`, `cli.py`, `generation.py`, `tracing.py`, `config.py`, `__main__.py`;
  - `app/extraction/evaluation/`: `cases.py`, `runner.py`, `metrics.py`, `compare.py`;
  - `app/core/eval_results.py`, `scripts/session_judge.sh`, `app/content/__init__.py`;
  - `app/content/prompts/extraction.md`, `link_disambiguation.md`;
  - `app/retrieval/evaluation/judging.py` (lines 1–280: export, estimate, import);
  - `app/llm/models.py::pair_compatible`, `app/llm/chat.py` (the defaults).
- Code read in part:
  - `app/extraction/evaluation/review.py` (lines 1–60);
  - `app/retrieval/evaluation/cli.py::export_corpus_command`;
  - `app/fpl/models/reference.py::Fixture`.
- Tests read in part:
  - `tests/extraction/test_cli.py` (the test list, `test_help`), `test_flow.py` (fixtures),
    `test_current_extractions.py` (the helper list), `fakes.py` (lines 1–80);
  - `tests/extraction/evaluation/test_eval_set.py`, `test_results_files.py`;
  - `tests/test_session_judge.py` (the harness), `tests/test_readme.py` (lines 1–60),
    `tests/test_docs.py` (lines 130–176), `tests/db/test_migrations.py` (lines 78–175),
    `tests/test_module_boundaries.py`, `tests/content/test_prompts.py`.
- Data inspected:
  - `evals/retrieval/v2/set.json`;
  - the format of `corpus.jsonl`: 2,557 posts, 2,342 of them source posts from 2026-10-01, and
    228 of those match the line-up keywords;
  - the GW6 and GW7 windows in `queries.jsonl`: 2026-10-03T10:00Z to 2026-10-10T10:00Z and
    2026-10-10T10:00Z to 2026-10-17T10:00Z;
  - the format of `evals/extraction/v1/` and where the three #23 posts are: all three are in
    the corpus, and only 2104158837540622748 is in v1.

### Design

1. **The entry point (BACKLOG #46)** follows the presser and retrieval layout:
   - `app/extraction/evaluation/cli.py` holds every evaluation command;
   - `app/extraction/evaluation/__main__.py` runs it;
   - `app/extraction/cli.py` keeps `reextract` and the shared dependency helpers
     (`ExtractionCliDeps`, `get_deps`, `fail`, `build_spec_from_settings`, `db_engine`,
     `_parse_iso`), and the evaluation CLI imports them;
   - the move itself changes no behaviour (step 1), and the new options come in later steps.
2. **One read-time predicate (AC7, AC19).** A new pure module `app/extraction/lineups.py`
   holds:
   - `LINEUP_EVENTS`, `LINEUP_MAX_LEAD` and `is_stale_lineup`, moved from `store.py`, which
     re-imports them so that the existing imports keep working;
   - the new
     `kept_at_read(event_type, certainty, created_at, next_kickoff, for_next_pl_match) -> bool`:
     non-line-up events are kept; a line-up with `for_next_pl_match is False` is dropped;
     otherwise the event is kept unless `is_stale_lineup`.

   `current_extractions` and the evaluation runner both call `kept_at_read`. The rejected
   variant was a SQL `WHERE` in `current_extractions` and a Python copy in the evaluation,
   because the SPEC asks for one function.
3. **Storage (AC5, AC8).** Migration `0014_match_check.py` adds `for_next_pl_match BOOLEAN NULL`
   to `extraction_event`, following the `op.add_column` pattern of `0013`. The column is also
   added to:
   - `ExtractionEvent.for_next_pl_match: bool | None = None`;
   - `LinkedEvent` (with a default of `None`) and `EventRecord`.

   `save_extraction` writes the column, and `current_extractions` returns it, so the review and
   SQL keep the trace.
4. **Fixtures (AC4, AC14).** A new module `app/extraction/fixtures.py` holds:
   - `FixtureRecord(season, fpl_id, gameweek, kickoff_at, team_h_fpl_id, team_a_fpl_id)`;
   - `FixtureIndex(fixtures)`, whose `around(team_fpl_id, at) -> (previous | None, next | None)`
     gives the latest kickoff `<= at` and the earliest kickoff `> at` (the same `>` as the SQL
     subquery in `current_extractions`), skipping fixtures with no kickoff;
   - `FixtureIndex.next_kickoff(team_fpl_id, at)`;
   - `load_fixtures(session)` for the current season (`app.fpl.calendar.current_season`, as
     `load_players`);
   - `load_fixtures_snapshot(path)` and `fixtures_snapshot(fixtures) -> dict`;
   - `render_fixture(fixture, team_fpl_id, index) -> "v <opponent> (H|A), <ISO kickoff UTC>"`;
   - `render_fixtures_context(fixtures, index, at)`: one line per club with its previous and
     next fixture, for the judge's input.

   `PlayerIndex` gets `player(fpl_id) -> PlayerRecord | None`. Fixtures are Premier League
   only, because FPL has no other competition.
5. **The match-check node (AC3–AC6, AC9, AC10).**
   - `build_flow(spec, index, tracer=None, prices=None, fixtures: FixtureIndex | None = None)`
     gets a third node, `match_check`, wired `extract → link → match_check → END`.
   - Grouping: the node takes the linked events with `event_type in LINEUP_EVENTS`, groups them
     by the player's club (`index.player(fpl_id).team_fpl_id`) and calls `fixtures.around(club,
     post.created_at)` for each club.
   - When the call is skipped: with no `fixtures`, no next fixture or no club, the node makes
     no call and the events keep `None`. Each remaining club gets one
     `call(MatchCheck, MATCH_CHECK_PROMPT.text, _render_match_check(post, team, previous, next))`.
   - The schema is `MatchCheck(BaseModel): for_next_pl_match: bool` in `schemas.py`.
   - The answer goes onto that club's line-up events with `dataclasses.replace`. Usage and
     `llm_calls` are added to the state.
   - Failures are fail-open: an exception from `call` (already traced by `call` with its error
     class) is caught, and a `None` parse (traced as `NoParsedResult` or the parsing error)
     leaves `None`. Neither raises, so `run_with_retries` never re-runs the flow for a match
     check.
   - The input is:

     ```
     Post: <text>
     Posted at: <ISO UTC>
     Club: <team.name>
     Previous PL fixture: <render_fixture> | none
     Next PL fixture: <render_fixture>
     ```

     The club comes from FPL data, never from the extract node's `team` mention.
   - The prompt is `app/content/prompts/match_check.md` (`version: 1`).
   - `PROMPT_VERSION` becomes
     `f"extraction@{E}+link_disambiguation@{L}+match_check@{M}"`. It reaches the stored
     `extraction.prompt_version`, the trace span and the evaluation run header unchanged.
   - Production wiring: `service.extract_post` loads `load_fixtures(session)` next to
     `load_players` and passes a `FixtureIndex`, and `prelabel` does the same. Existing flow tests
     pass no fixtures and so keep their scripted responses.
   - Variants considered for the grouping: one call per event (rejected, because the SPEC asks
     for one call per post and club) and the club from the mention (rejected by AC4).
6. **The set directory (AC14).** A set lives in `evals/extraction/<v>/` and holds
   `cases.jsonl`, `players.json`, `fixtures.json` and `set.json`.
   - `set.json` follows `SetManifest` in a new `app/extraction/evaluation/dataset.py`:
     - `version`;
     - `list_handles` (copied from `evals/retrieval/v2/set.json`);
     - `prelabels`: a list of `{judge, prompt_versions, git_commit, inputs, imported_at}`;
     - `backlog_cases`: `{"23": [ids], "54": [ids]}`;
     - `backlog_54_lineups`: [ids];
     - `selection`: `{seed, quotas, pool}`.
   - `load_set(dir)` reads them. For v1 it falls back to the only `players-*.json` and has no
     fixtures and no manifest.
   - The commands take `--set DIR`, with the default `evals/extraction/v2`. `--cases` and
     `--players` disappear, and `compare-labels` keeps `--baseline` and `--revision`.
   - `snapshot-players --set` writes `players.json`, and the new `snapshot-fixtures --set`
     writes `fixtures.json` (season, every fixture). Both are read-only on the database.
   - Results go to `results/<v>/` (test) and `results/<v>/dev/` (gitignored) for a set other
     than v1. v1's files stay where they are, so the existing v1 result tests keep reading
     `results/*.json`.
7. **The evaluation on the whole flow (AC19, AC20).**
   - `run_evaluation(..., fixtures: FixtureIndex)` builds the flow with fixtures.
   - For every predicted raw event the runner gets `next_kickoff`:
     `fixtures.next_kickoff(club, case.created_at)` for a linked player, and `None` when the
     player is unlinked, as the SQL join gives `NULL`.
   - It then calls `kept_at_read` twice: once with the stored answer (giving `predicted`, the
     scored events) and once with `for_next_pl_match=None` (the 60 h filter alone).
   - `CaseResult` gets `other_match: bool`, `raw_predicted` (the events with
     `for_next_pl_match` and `kept`), `dropped_by_check` and `kept_by_lead_only`.
   - `compute_metrics` adds:
     - `other_match_cases`;
     - `other_match_false_alarms` (tagged cases left with an event);
     - `other_match_false_alarm_rate`;
     - `lineups_dropped_by_check`: expected `benched` or `confirmed_starter` events paired by
       `_match_pairs` with an event the check answered false;
     - `other_match_with_raw_events` and `other_match_caught_by_lead_only` (for the report's "60
       h filter alone" figure).
   - The thresholds are unchanged, and the latency of `_TimedFlow` already includes the third
     node.
   - Every node uses the one `spec` (`fallback=False`), so every step runs with the model under
     test.
8. **Set v2 and its checks (AC12, AC13).**
   - The new tag `other_match_lineup` marks a line-up naming Premier League players for any
     match other than their club's next Premier League match (a cup or European tie, a
     national team, a match already played), whether or not the post names the match, and
     expects no event. It sits beside the v1 tags: a v1 case tagged `cup_european_lineup` or
     `national_lineup` whose line-up names Premier League players gets it too (through the
     judge's flag, so it is a changed case the owner reviews). It is not added to
     `NO_EVENT_TAGS`, because v1 has no such case and `composition_problems` must stay green on
     v1; `composition_problems_v2` checks it.
   - `composition_problems_v2(cases, players, manifest, v1_ids)` in `cases.py` runs
     `composition_problems` (the v1 rules) and adds:
     - every v1 id is present;
     - at least 70 new real cases with `created_at` in [2026-10-01T00:00Z, 2026-10-18T00:00Z);
     - at least 15 test `benched` or `confirmed_starter` events in cases created in the GW6–GW7
       windows [2026-10-03T10:00Z, 2026-10-17T10:00Z);
     - at least 8 `other_match_lineup` cases, at least 5 of them in test, none with an event;
     - the #23 ids with their labels (two with no event, one with no `confirmed`);
     - every `backlog_54_lineups` id tagged;
     - real new cases split by `int(id) % 10 < 3 → dev`;
     - real new cases' authors in `list_handles` (AC25).
   - `compare-labels` also prints one line per changed case (`<id>: added N, removed N,
     relabelled <mention> <fields>, tags +<added> -<removed>`) from `compare_events` and the
     tag difference, so that every relabelled case is listed (AC12), a tag-only change
     included. A relabel whose field tuple is empty (the mention spelled differently) is not a
     change.
9. **The session judge for extraction (AC16–AC18).** `app/extraction/evaluation/judging.py`
   follows `app/retrieval/evaluation/judging.py`.
   - `export_inputs` writes into `out/`:
     - `inputs/judge-prompt.md`: `extraction.md`'s text, a blank line and the new
       `extraction_prelabel.md` (version 1: label as the extraction rules say, use the fixtures
       to give no line-up event for another match, and set `other_match_lineup`);
     - one `inputs/<neutral id>.md` per post: `_render_post` and `render_fixtures_context` (all
       20 clubs around the post's time), with neutral ids `I0001`…, shuffled with the seed;
     - `out/mapping.json` (outside `inputs/`): neutral id → the source (`case` and its id, or
       `post` and its fields), `required`, and the prompt versions.
   - The sources are the cases of a set (`--cases PATH`, the v1 relabel) or corpus posts:
     - `--corpus PATH --since --until` reads the corpus through its own minimal Pydantic model,
       because extraction may not import `app.retrieval` (`tests/test_module_boundaries.py`);
     - `--keywords` keeps the source posts matching `LINEUP_KEYWORDS` (XI, line-up/lineup,
       starting, starts, bench/benched, subs, team news);
     - `--rest-sample N --seed S` adds a seeded sample of the posts the keywords miss;
     - `--x-id` (repeatable) is always included and marked `required`;
     - `--exclude-cases PATH` leaves out the ids already in the set.
   - `estimate_export(out)` uses the retrieval formula: characters / 4 plus 3,000 per call,
     with the reply-format sentence counted.
   - The verdict is `PrelabelVerdict(ExtractionOutput)` with `extra="forbid"` and one added
     required field, `other_match_lineup: bool`. The events part is the extraction output
     schema itself, validated strictly: `ExtractedEvent` ignores unknown keys, so the import
     validates each event with a `extra="forbid"` subclass and refuses an event with an extra
     key, as the shell validator does.
     - The flag adds the tag, which the extraction output cannot express, and keeps the owner
       from tagging about 25 cases by hand.
     - The rejected variant was tags set only in the owner's `review`, which would be a second
       review pass if the composition fails.
   - `import_verdicts` works as follows:
     - Any invalid file refuses the import with the list, and nothing is written.
     - A `case` input without a verdict also refuses the import (AC12: every v1 case). A `post`
       input without one is skipped and listed.
     - Events are linked through `PlayerIndex` (a single candidate gives `fpl_id`, several give
       `null`), with no LLM call.
     - For a `case` input, a judge event that `compare_events` pairs with a v1 event of the
       same player and whose own `fpl_id` is `null` (an ambiguous name the import could not
       link) takes the v1 event's `fpl_id`; the import makes no LLM call, so this keeps a
       linking gap from posing as a label change.
     - A `case` input whose events are unchanged (`compare_events`: zero added, zero removed
       and no relabel with a non-empty field tuple) and whose tags are unchanged is copied
       verbatim from v1 (`reviewed: true`). Otherwise it becomes `reviewed: false` with the
       judge's events and the tag added.
     - `post` inputs become candidates, and `--select N --seed S` picks them by tier:
       1. required;
       2. flagged `other_match_lineup` (quota 25);
       3. with a line-up event (quota 35);
       4. with another event (quota 10);
       5. with no event (quota 10).

       A tier over its quota gets a seeded sample. A short tier leaves its places to the
       next.
     - Ids already in the set are skipped. A `prelabels` entry naming the judge is appended to
       `set.json` (AC18).
   - `scripts/session_judge.sh` gets an `extraction` schema: the `FORMAT` sentence and a
     validation of `events` (each with a string `player`, an optional string or null `team`,
     an `event_type` of the four, a `certainty` of the three, no other keys) and of a boolean
     `other_match_lineup`. An invalid reply moves to `raw/<id>.invalid.json`.
10. **The judge model (the SPEC's open question).** A pilot runs Sonnet at medium effort on
    v1's 45 dev cases. Sonnet judges the rest if, against v1's reviewed labels, at least 80% of
    the cases are unchanged and the pre-label F1 is at least 0.85 (`compare-labels
    --baseline`). Otherwise Opus at default effort judges everything, the pilot included.
    - The yardstick: v1's paid pre-labels scored 0.739 and 0.882.
    - The estimate is about 5.5k tokens a call and about 430 calls: 136 v1 cases, about 270
      corpus candidates, the #23 and #54 posts and the pilot. That makes about 2.4M tokens.
    - The implementer escalates before judging if `estimate` exceeds 3M tokens, the cap of
      spec 017's phase A.
11. **The default (AC21).** A new `app/extraction/evaluation/selection.py` has
    `choose_models(runs, catalogue) -> (default, fallback)`.
    - Default: the cheapest model passing every threshold. If none passes, the model at or
      under 5 PLN with the most thresholds passed, then the higher F1.
    - Fallback: the best other model at or under 5 PLN (most thresholds, then F1) that is
      `pair_compatible` with the default.
    - It reads the v2 result files, and a test over the committed files asserts the result
      equals `DEFAULT_MODEL` and `DEFAULT_FALLBACK_MODEL` in `app/llm/chat.py`.

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 1, 6, 8 | `tests/extraction/evaluation/test_cli.py::test_help_lists_every_evaluation_command`; `tests/extraction/test_cli.py::test_help` (reextract only) |
| AC2 | 16 | `tests/test_readme.py::test_extraction_evaluation_commands_use_the_new_module` |
| AC3 | 5 | `tests/extraction/test_flow.py::test_match_check_one_call_per_club`, `::test_no_match_check_without_lineup_events`, `::test_match_check_two_clubs_two_calls` |
| AC4 | 4, 5 | `tests/extraction/test_flow.py::test_match_check_input_from_fixture_data`; `tests/extraction/test_fixtures.py::test_around_previous_and_next` |
| AC5 | 3, 5 | `tests/extraction/test_flow.py::test_match_check_answer_on_that_clubs_lineup_events_only`, `::test_no_call_for_unlinked_or_club_without_next_fixture`; `tests/extraction/test_store.py::test_save_extraction_stores_the_match_check_answer` |
| AC6 | 5 | `tests/extraction/test_flow.py::test_match_check_failure_is_fail_open` (raises; no parsed result; generation traced with its error class); `tests/extraction/test_service.py::test_failed_match_check_keeps_the_extraction_extracted` |
| AC7 | 3 | `tests/extraction/test_current_extractions.py::test_fresh_lineups_only_drops_a_lineup_the_check_answered_false`, `::test_null_and_true_answers_meet_the_lead_filter`, `::test_unfiltered_read_returns_every_event`; `tests/extraction/test_lineups.py::test_kept_at_read` |
| AC8 | 2 | `tests/db/test_migrations.py::test_upgrade_downgrade_upgrade`, `::test_models_match_migration`, `::test_match_check_migration_leaves_existing_events_null`; `tests/test_docs.py::test_deployment_lists_migration_0014` |
| AC9 | 5, 6 | `tests/extraction/test_flow.py::test_prompt_version_names_three_prompts`; `tests/extraction/test_service.py` (stored `prompt_version == PROMPT_VERSION`, existing); `tests/extraction/evaluation/test_cli.py::test_evaluate_header_names_the_three_prompts` |
| AC10 | 5 | `tests/content/test_prompts.py::test_match_check_prompt_has_a_version`, `::test_flow_sends_the_loaded_match_check_prompt` |
| AC11 | 9, 10, 11 | `tests/content/test_prompts.py::test_extraction_prompt_v5_rules`; `tests/extraction/evaluation/test_eval_set_v2.py::test_backlog_23_cases_labelled` |
| AC12 | 7, 8, 10, 11 | `tests/extraction/evaluation/test_eval_set_v2.py::test_every_v1_case_in_v2`, `::test_every_case_reviewed`; `tests/extraction/evaluation/test_compare.py::test_changed_cases_listed`; `tests/extraction/evaluation/test_cli.py::test_compare_labels_lists_each_changed_case`, `::test_evaluate_refuses_unreviewed` |
| AC13 | 7, 10, 11 | `tests/extraction/evaluation/test_cases.py::test_composition_v2_*`; `tests/extraction/evaluation/test_eval_set_v2.py::test_composition_v2` |
| AC14 | 4, 6, 10 | `tests/extraction/evaluation/test_cli.py::test_evaluate_reads_the_set_directory_without_a_database`; `tests/extraction/evaluation/test_eval_set_v2.py::test_fixtures_snapshot_covers_every_case` |
| AC15 | 12, 13, 15 | `tests/extraction/evaluation/test_results_files.py::test_v2_test_runs_share_one_prompt`; `tests/test_docs.py::test_extraction_report_v2_has_every_section` (dev iterations section) |
| AC16 | 8 | `tests/extraction/evaluation/test_judging.py::test_export_one_input_per_post_with_neutral_ids`, `::test_mapping_outside_inputs`, `::test_estimate_counts_calls_and_tokens`; `tests/extraction/evaluation/test_cli.py::test_estimate_prints_calls_and_tokens` |
| AC17 | 8 | `tests/test_session_judge.py::test_extraction_writes_a_valid_verdict`, `::test_extraction_rejects_an_invalid_verdict`; `tests/extraction/evaluation/test_judging.py::test_import_refuses_an_invalid_verdict` |
| AC18 | 8 | `tests/extraction/evaluation/test_judging.py::test_imported_cases_unreviewed_with_the_judge_in_the_manifest`; `tests/extraction/evaluation/test_review_cli.py::test_review_shows_an_imported_case` |
| AC19 | 3, 6 | `tests/extraction/evaluation/test_runner.py::test_scores_the_events_kept_at_read`, `::test_runner_and_store_share_the_predicate`; `tests/extraction/evaluation/test_metrics.py::test_other_match_false_alarms`, `::test_lineups_dropped_by_check` |
| AC20 | 5, 13 | `tests/extraction/evaluation/test_results_files.py::test_v2_one_test_run_per_candidate` (header fields), `::test_v2_test_spend_within_cap`; `tests/extraction/test_flow.py::test_every_call_uses_the_flow_model` |
| AC21 | 14 | `tests/extraction/evaluation/test_selection.py`; `tests/extraction/evaluation/test_results_files.py::test_v2_rule_picks_the_configured_defaults`, `::test_adr_0010_names_the_defaults` |
| AC22 | 15 | `tests/test_docs.py::test_extraction_report_v2_has_every_section`; `tests/extraction/evaluation/test_results_files.py::test_report_v2_names_every_test_run` |
| AC23 | 14, 16 | `tests/test_docs.py::test_decisions_cover_match_check_and_set_v2`, `::test_adr_0010_weighs_alternatives_a_c_d` |
| AC24 | 16 | `tests/test_docs.py::test_roadmap_018_ticked_and_019_open`, `::test_backlog_23_46_54_removed_and_12_narrowed` |
| AC25 | 7, 17 | the full verification command; `tests/extraction/evaluation/test_eval_set_v2.py::test_composition_v2` (authors of new real cases are watched accounts) |

## Steps

All commands run from `backend/` (`cd /home/czarny/Projects/gaffers-presser/backend`).
"Verify" = `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`.

- [ ] 1. **The evaluation entry point (AC1, move only).** Files:
      - new: `app/extraction/evaluation/cli.py`, `app/extraction/evaluation/__main__.py`
        (`main()` with `prog_name="python -m app.extraction.evaluation"`);
      - changed: `app/extraction/cli.py`.

      What moves:
      - `snapshot-players`, `prelabel`, `evaluate`, `spend`, `compare-labels`, `review` and
        their helpers (`EVALS_DIR`, the defaults, `THRESHOLD_LABELS`, `RUN_NAME_PATTERN`,
        `default_output_dir`, `_review_cases`, …) move unchanged into the new CLI;
      - `app/extraction/cli.py` keeps `reextract` and the dependency helpers, and drops its
        placeholder `_callback` comment.

      Tests:
      - the evaluation tests in `tests/extraction/test_cli.py` (from
        `test_snapshot_players_*` to `test_spend_empty_directory`, plus the evaluation
        parameters of `test_provider_option_removed`) move to
        `tests/extraction/evaluation/test_cli.py`, which imports
        `app.extraction.evaluation.cli`;
      - `tests/extraction/test_review_cli.py` moves to
        `tests/extraction/evaluation/test_review_cli.py`;
      - `tests/extraction/evaluation/test_eval_set.py` and `test_results_files.py` import the
        constants from the new module;
      - first, write `tests/extraction/evaluation/test_cli.py::test_help_lists_every_evaluation_command`
        (subprocess `-m app.extraction.evaluation --help` lists the six commands; the export,
        import and estimate names are added in step 8);
      - extend `tests/extraction/test_cli.py::test_help`: no evaluation command appears in
        `-m app.extraction --help`.
      - `tests/test_readme.py`'s `EXTRACTION_COMMANDS` keeps the old strings until step 16.

      Automatic verification:
      `uv run pytest -q tests/extraction -n auto && uv run python -m app.extraction.evaluation --help && uv run python -m app.extraction --help`, then Verify.
- [ ] 2. **Migration 0014 (AC8): the data migration on its own.** Files:
      - new: `migrations/versions/0014_match_check.py` (`revision "0014"`, `down_revision
        "0013"`; `op.add_column("extraction_event", sa.Column("for_next_pl_match",
        sa.Boolean(), nullable=True))`; the downgrade drops the column);
      - changed: `app/extraction/models.py` (`for_next_pl_match: bool | None = None`) and
        `docs/DEPLOYMENT.md` (a `0014` note beside `0013`: a nullable column, existing rows
        `null`, it downgrades cleanly, and the owner runs it on Compose).

      Tests first:
      - `tests/db/test_migrations.py::test_match_check_migration_leaves_existing_events_null`:
        upgrade to `0013`, insert a tweet, an extraction and an event, upgrade to `head`, and
        assert the column exists and is `NULL`; downgrade `-1` and assert the column is gone.
        Use its own `PostgresContainer`, as `test_tweet_migration_adds_only_new_tables` does;
      - `tests/test_docs.py::test_deployment_lists_migration_0014`.

      Only the test containers are migrated, never Compose.

      Automatic verification:
      `uv run pytest -q tests/db/test_migrations.py tests/test_docs.py -k "migration or 0014"`,
      then Verify.
- [ ] 3. **The read-time predicate and storage (AC5 storage, AC7, AC19 predicate).** Files:
      - new: `app/extraction/lineups.py`;
      - changed: `app/extraction/store.py` (it imports `LINEUP_EVENTS`, `LINEUP_MAX_LEAD` and
        `is_stale_lineup` from `lineups`, and `kept_at_read` replaces the
        `is_stale_lineup(...)` call in `current_extractions`, passing
        `event.for_next_pl_match`; `save_extraction` writes the column;
        `EventRecord.for_next_pl_match: bool | None = None` is filled);
      - `app/extraction/schemas.py` (`LinkedEvent.for_next_pl_match: bool | None = None`, the
        last field).

      Tests first:
      - `tests/extraction/test_lineups.py::test_kept_at_read`, parametrized over: `out` with
        `False` kept; `benched` `likely` with `False` dropped; `confirmed_starter` `confirmed`
        with `True` at a 61 h lead dropped; `None` at a 10 h lead kept; `True` at a 10 h lead
        kept;
      - `tests/extraction/test_current_extractions.py`: `test_fresh_lineups_only_drops_a_lineup_the_check_answered_false`
        (`rumour` certainty, a 10 h lead), `test_null_and_true_answers_meet_the_lead_filter`
        and `test_unfiltered_read_returns_every_event` (with `fresh_lineups_only=False`, the
        false one is returned with its answer). Extend the `_event` helper with the answer;
      - `tests/extraction/test_store.py::test_save_extraction_stores_the_match_check_answer`.

      The existing tests importing `LINEUP_MAX_LEAD` and `is_stale_lineup` from `store` stay
      green.

      Automatic verification:
      `uv run pytest -q tests/extraction/test_lineups.py tests/extraction/test_current_extractions.py tests/extraction/test_store.py tests/alerts tests/corroboration -n auto`,
      then Verify.
- [ ] 4. **Fixture data (AC4 data, AC14 snapshot format).** Files:
      - new: `app/extraction/fixtures.py` (approach item 4);
      - changed: `app/extraction/linking.py` (`PlayerIndex.player(fpl_id)`).

      Tests first, in `tests/extraction/test_fixtures.py`:
      - `test_around_previous_and_next` (a kickoff equal to `at` is previous, never next);
      - `test_around_skips_fixtures_without_kickoff`;
      - `test_no_next_fixture_at_season_end`;
      - `test_snapshot_round_trip`;
      - `test_load_fixtures_reads_the_current_season` (`db_session`, two seasons);
      - `test_render_fixture_home_and_away`;
      - `test_render_fixtures_context_one_line_per_club`.

      Add `tests/extraction/test_linking.py::test_player_by_fpl_id`.

      Automatic verification:
      `uv run pytest -q tests/extraction/test_fixtures.py tests/extraction/test_linking.py`,
      then Verify.
- [ ] 5. **The match-check node (AC3–AC6, AC9 storage, AC10, AC20 one model).** Files:
      - new: `app/content/prompts/match_check.md` (`version: 1`). It describes the input
        fields and asks whether the line-up the post gives or reports for the club is for the
        club's next Premier League match. A line-up for another competition, a match already
        played (published after the previous fixture's kickoff and about it) or a national
        team is false. Answer `for_next_pl_match`;
      - changed: `app/extraction/schemas.py` (`MatchCheck`), `app/extraction/flow.py` (the
        node, `MATCH_CHECK_PROMPT`, `PROMPT_VERSION`, the `fixtures` parameter),
        `app/extraction/service.py` (`load_fixtures`, passed as `FixtureIndex`) and
        `app/extraction/evaluation/cli.py::prelabel` (passes fixtures from the database).

      Tests first, in `tests/extraction/test_flow.py`, with the `FakeChatModel` script being
      extraction, then match-check replies:
      - `test_match_check_one_call_per_club`: three line-up events of one club give one call;
      - `test_no_match_check_without_lineup_events`: an `out` event alone gives no call;
      - `test_match_check_two_clubs_two_calls`;
      - `test_match_check_input_from_fixture_data`: assert the received `HumanMessage` holds
        the post text, the ISO time, `Team One`, the previous and next opponents with `(H)` /
        `(A)` and the kickoffs, and not the extract node's `team` mention;
      - `test_match_check_answer_on_that_clubs_lineup_events_only`: an `out` event of the same
        club keeps `None`;
      - `test_no_call_for_unlinked_or_club_without_next_fixture`;
      - `test_match_check_failure_is_fail_open`: a raised `RuntimeError` and a reply that does
        not parse each leave `None` with the flow result returned. A
        `RecordingExtractionTracer` has a generation with `error_class` set;
      - `test_prompt_version_names_three_prompts`;
      - `test_every_call_uses_the_flow_model`: one `FakeChatModel` receives all three call
        kinds (extraction, disambiguation, match check).

      Other tests first:
      - `tests/extraction/test_service.py::test_failed_match_check_keeps_the_extraction_extracted`
        (the stored status is `extracted`, and the events are stored with `null`) and a
        test that `extract_post` passes the season's fixtures;
      - `tests/content/test_prompts.py::test_match_check_prompt_has_a_version` and
        `::test_flow_sends_the_loaded_match_check_prompt` (the system message equals
        `load_prompt("match_check").text`).

      Automatic verification:
      `uv run pytest -q tests/extraction tests/content -n auto`, then Verify.
- [ ] 6. **The set directory, `snapshot-fixtures` and the evaluation on the whole flow (AC9
      header, AC14, AC19).** Files:
      - new: `app/extraction/evaluation/dataset.py` (`SetManifest`, `EvalSet`, `load_set`);
      - changed: `app/extraction/evaluation/cli.py` (`--set` on `snapshot-players`,
        `prelabel`, `evaluate`, `review` and `compare-labels`; the new `snapshot-fixtures`;
        `default_output_dir(split, set_version)`; the new metric lines in `evaluate`'s
        output), `app/extraction/evaluation/runner.py` (approach item 7),
        `app/extraction/evaluation/metrics.py` (the new `CaseResult` and `Metrics` fields) and
        `/home/czarny/Projects/gaffers-presser/.gitignore`
        (`backend/evals/extraction/results/*/dev/`).

      Tests first:
      - `tests/extraction/evaluation/test_runner.py`:
        - `test_scores_the_events_kept_at_read` (a fake answers false for a line-up, so the
          event is out of `predicted` and in `dropped_by_check`);
        - `test_lead_only_filter_reported` (a `confirmed` line-up 70 h before the next kickoff
          and answered true is dropped, and `kept_by_lead_only` is empty);
        - `test_runner_and_store_share_the_predicate` (`runner.kept_at_read is
          store.kept_at_read is lineups.kept_at_read`);
      - `tests/extraction/evaluation/test_metrics.py::test_other_match_false_alarms` and
        `::test_lineups_dropped_by_check`;
      - `tests/extraction/evaluation/test_cli.py`:
        - `test_evaluate_reads_the_set_directory_without_a_database` (`engine=None`, a tmp
          set with the four files);
        - `test_evaluate_header_names_the_three_prompts` (`header.prompt_version` contains
          `extraction@`, `link_disambiguation@1` and `match_check@1`, and `header.set_version
          == "v2"`);
        - `test_v2_dev_runs_go_to_the_set_dev_directory`;
        - `test_snapshot_fixtures_writes_the_season` (`db`).

      Adapt the moved evaluate tests to `--set`.

      Automatic verification:
      `uv run pytest -q tests/extraction/evaluation -n auto`, then Verify.
- [ ] 7. **The tag, the v2 composition and the listing of changed cases (AC12, AC13, AC25
      check).** Files: `app/extraction/evaluation/cases.py` (`other_match_lineup` in `Tag`,
      `composition_problems_v2`, the window constants with a comment naming the GW6 and GW7
      deadlines), `app/extraction/evaluation/compare.py` (`SplitComparison.changes: list[str]`)
      and `app/extraction/evaluation/cli.py` (`compare-labels` prints the changes).

      Tests first:
      - `tests/extraction/evaluation/test_cases.py::test_composition_v2_accepts_a_valid_set`
        and one `test_composition_v2_*` per rule: a missing v1 id, too few new cases, too few
        line-ups in the window, too few tagged cases, a tagged case with an event, a wrong #23
        label, an untagged #54 line-up, a wrong split and an unknown author. Each is built
        from a small synthetic set;
      - `tests/extraction/evaluation/test_compare.py::test_changed_cases_listed` (an event
        change, a tag-only change listed; a mention-spelling-only relabel not listed);
      - `tests/extraction/evaluation/test_cli.py::test_compare_labels_lists_each_changed_case`.

      Automatic verification:
      `uv run pytest -q tests/extraction/evaluation/test_cases.py tests/extraction/evaluation/test_compare.py tests/extraction/evaluation/test_cli.py`,
      then Verify.
- [ ] 8. **The session judge for extraction (AC16–AC18, AC1 names).** Files:
      - new: `app/extraction/evaluation/judging.py` (approach item 9) and
        `app/content/prompts/extraction_prelabel.md` (`version: 1`);
      - changed: `app/extraction/evaluation/cli.py` (`export-judge-inputs` (`--out`, `--set`, `--cases`,
        `--split`, `--corpus`, `--since`, `--until`, `--keywords`, `--rest-sample`, `--seed`,
        `--x-id`, `--exclude-cases`), `estimate` (`--export`) and `import-judge` with
        `--export`, `--set`, `--judge-model`, `--select`, `--seed`,
        `--quota other=25,lineup=35,event=10,none=10` and `--force`) and
        `scripts/session_judge.sh` (the `extraction` schema; the header comment names it).

      Tests first, in `tests/extraction/evaluation/test_judging.py`:
      - `test_export_one_input_per_post_with_neutral_ids` (ids match `I\d{4}`; the input
        holds the rendered post and 20 fixture lines; `judge-prompt.md` starts with the
        extraction prompt's text and holds the prelabel prompt);
      - `test_mapping_outside_inputs`;
      - `test_export_keywords_rest_sample_and_required_ids` (seeded, deterministic);
      - `test_estimate_counts_calls_and_tokens`;
      - `test_import_refuses_an_invalid_verdict` (an extra key, a bad `event_type`, a missing
        `other_match_lineup`: nothing is written);
      - `test_import_refuses_a_missing_case_verdict`;
      - `test_unchanged_v1_case_copied_reviewed` (also when the judge spells the mention
        differently or leaves an ambiguous name unlinked: the v1 case is copied verbatim);
      - `test_changed_v1_case_unreviewed_with_the_tag`;
      - `test_select_tiers_and_quotas`;
      - `test_imported_cases_unreviewed_with_the_judge_in_the_manifest`;
      - `test_import_links_unique_names_and_leaves_ambiguous_null`.

      Other tests first:
      - in `tests/test_session_judge.py` (the existing stub harness):
        - `test_extraction_writes_a_valid_verdict`;
        - `test_extraction_rejects_an_invalid_verdict` (moved to `raw/<id>.invalid.json`,
          and a rerun judges only it);
        - `test_extraction_sends_the_extraction_reply_format`;
      - `tests/extraction/evaluation/test_review_cli.py::test_review_shows_an_imported_case`;
      - `tests/extraction/evaluation/test_cli.py::test_estimate_prints_calls_and_tokens`;
      - extend `test_help_lists_every_evaluation_command` with the three names.

      Automatic verification:
      `uv run pytest -q tests/extraction/evaluation tests/test_session_judge.py -n auto`, then
      Verify.
- [ ] 9. **Extraction prompt v5: the three #23 rules (AC11 prompt).** Files:
      `app/content/prompts/extraction.md`. Set `version: 5` and add:
      - to the relevance rule: an FPL manager writing about his own team, his transfers or a
        hypothetical gives no event;
      - to the relevance rule: national-team fitness news saying the player is ready or very
        likely to start gives no event;
      - to certainty: a post listing players as injured or flagged restates the official FPL
        flags and is never `confirmed`.

      These are written before any v2 data is seen. Test first:
      `tests/content/test_prompts.py::test_extraction_prompt_v5_rules` (version 5, and the
      keywords "own team", "hypothetical", "national team", "flag").

      Automatic verification:
      `uv run pytest -q tests/content tests/extraction/test_flow.py`, then Verify.
- [ ] 10. **Build set v2 with session pre-labels (AC11–AC14 data, AC18).** The spec owner
      approved these read-only Compose reads (`DATABASE_URL` from `backend/.env`); no command
      here writes to Compose. Prefix every command with `env -u TWEET_SOURCE`.
      `$S` = a `018/` directory in the implementer's session scratchpad (never in the
      repository).
      - Write `tests/extraction/evaluation/test_eval_set_v2.py` first:
        - `test_schema`;
        - `test_every_v1_case_in_v2`;
        - `test_every_case_reviewed`;
        - `test_composition_v2`;
        - `test_backlog_23_cases_labelled`;
        - `test_fixtures_snapshot_covers_every_case`: every case's `created_at` lies within
          the snapshot's season fixtures, and every expected `fpl_id` is in `players.json`.

        It is red until step 11.
      - Snapshots: `uv run python -m app.extraction.evaluation snapshot-players --set evals/extraction/v2`
        and `uv run python -m app.extraction.evaluation snapshot-fixtures --set evals/extraction/v2`.
      - The #54 posts after 2026-10-17:
        - run `uv run python -m app.retrieval.evaluation export-corpus --set $S/compose --since 2026-10-19T00:00 --until 2026-10-21T12:00`
          (read-only);
        - find the City v AEK posts with a text search and the fixtures snapshot;
        - find the Brighton Conference League post (2026-10-15) and FPL_Harry's City XI for
          PSG (2026-10-14) in `evals/retrieval/v2/corpus.jsonl`;
        - write `set.json` with:
          - `version: "v2"`;
          - `list_handles` from `evals/retrieval/v2/set.json`;
          - `backlog_cases` with the three #23 ids and the #54 ids found;
          - `backlog_54_lineups` (the line-up posts among them).
      - Pilot:
        - `export-judge-inputs --cases evals/extraction/v1/cases.jsonl --split dev --out $S/pilot`,
          then `estimate --export $S/pilot`;
        - `../scripts/session_judge.sh $S/pilot 6 sonnet extraction medium`;
        - `import-judge --export $S/pilot --set evals/extraction/v2 --judge-model "claude-sonnet-5-5 (Claude Code session)"`;
        - `compare-labels --set evals/extraction/v2 --baseline evals/extraction/v1/cases.jsonl`;
        - apply the rule of approach item 10 and record the pilot figures and the choice in
          `## Deviations` as a `minor` note. If Opus is chosen, remove the pilot's cases from
          `cases.jsonl` and re-judge the dev cases with Opus.
      - The rest:
        - the export of the 91 v1 test cases (`--split test`);
        - the corpus export `--corpus evals/retrieval/v2/corpus.jsonl --since 2026-10-01T00:00Z --until 2026-10-17T10:00Z --keywords --rest-sample 40 --seed 18 --exclude-cases evals/extraction/v1/cases.jsonl`,
          plus `--x-id` for the two #23 posts not in v1 and the #54 posts in the corpus;
        - an export of the #54 Compose posts from `$S/compose/corpus.jsonl` with `--x-id`.
      - Estimate every export first, and if the sum of the estimates exceeds 3M tokens,
        escalate (`decision`) before judging. Then judge with the chosen model, and import with
        `--select 80 --seed 18` for the corpus exports.
      - Run `compare-labels` against v1 and the composition rules except `reviewed`. If a
        composition rule fails on the candidates, draw one more corpus export with a new seed
        (or widen `--rest-sample` for no-event cases), judge it and import it. More than two
        extra draws → escalate (`decision`).
      - Commit the set unreviewed (`data: build extraction set v2 candidates`).
      - A refused `claude -p` or Compose read → escalate (`permission`).

      Automatic verification:
      `uv run pytest -q tests/extraction/evaluation/test_eval_set_v2.py -k "schema or every_v1 or fixtures"`
      green, and `-k composition` failing only on unreviewed cases or labels the owner will
      settle, recorded in `## Deviations`. Then Verify with `test_eval_set_v2.py` deselected
      (`--deselect tests/extraction/evaluation/test_eval_set_v2.py`).
- [ ] 11. **The owner's review of set v2 (AC12, AC13, AC18): the implementer stops here.**
      - End the run with `RESULT: ESCALATE` (`KIND: decision`) and wait. The message gives:
        - the counts: changed v1 cases and new cases per split;
        - the command `cd backend && uv run python -m app.extraction.evaluation review --set evals/extraction/v2`
          (it walks only the unreviewed cases);
        - the reminder that `other_match_lineup` cases expect no event, and that the #23
          labels are as AC11 says;
        - the `compare-labels` output.
      - On resume:
        - run `test_eval_set_v2.py` in full;
        - if a composition rule fails after the review, draw more candidates as in step 10
          and escalate again for their review;
        - commit `data: review extraction set v2`, if the owner has not committed it.

      Automatic verification:
      `uv run pytest -q tests/extraction/evaluation/test_eval_set_v2.py`, then Verify.
- [ ] 12. **Dev iterations of prompt v5 (AC15).** Use the paid OpenRouter key from
      `backend/.env`.
      - Iterate:
        `env -u TWEET_SOURCE uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v2 --split dev --model openai/gpt-6-luna --run-name dev-v2-luna-iN`
        and the same for `google/gemini-3.1-flash-lite` (`dev-v2-gemini-iN`).
      - Edit `extraction.md` (and `match_check.md` if the dev misses point at it) on dev
        failures only, and stop at five iterations or once both pass every threshold on dev.
      - Record every iteration in `specs/018-extraction-evaluation-v2/dev-iterations.md` (run
        name, F1, the false-alarm rate, the two new metrics, the change made), not in the
        session scratchpad: an escalation restarts the implementer in a new session, and step
        15 reads this log.
      - Freeze: commit the prompts and the log (`feat: freeze extraction prompt v5`). The
        versions stay 5 and 1 (AC9 names match check 1), and the iterations are uncommitted
        edits whose dev headers show `git_dirty`.

      Automatic verification:
      `uv run pytest -q tests/content tests/extraction -n auto`, and
      `uv run python -m app.extraction.evaluation spend --results-dir evals/extraction/results/v2`
      prints at most 4.00 USD. Then Verify.
- [ ] 13. **The six test runs (AC20).**
      - Write `tests/extraction/evaluation/test_results_files.py::test_v2_one_test_run_per_candidate`
        first:
        - `results/v2/*.json` has one run per `CANDIDATES` model, plus at most one `-r2`;
        - each header has `set_version "v2"`, `model`, `reasoning_effort` and `git_commit`;
        - `prompt_version` has the three prompts, and there is one `prompt_version` across
          the files.

        Also write `::test_v2_test_runs_share_one_prompt` and `::test_v2_test_spend_within_cap`
        (the sum of `metrics.total_cost_usd` is at most 5).
      - Run, for each model in `tests/extraction/candidates.py`:
        `env -u TWEET_SOURCE uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v2 --split test --model M --run-name test-v2-<model slug>`.
        A run with `errored cases > 0` gets one repeat, `--run-name test-v2-<slug>-r2`.
      - `spend --results-dir evals/extraction/results/v2` (dev and test) must stay at most
        $5. Stop and escalate (`decision`) if the dev and test total reaches $4 before the
        last run.
      - Commit the result files.

      Automatic verification:
      `uv run pytest -q tests/extraction/evaluation/test_results_files.py` and the `spend`
      command above. Then Verify.
- [ ] 14. **The default model and ADR 0010 (AC21, AC23 ADR).** Files:
      - new: `app/extraction/evaluation/selection.py` and
        `docs/adr/0010-extraction-match-check-and-default-model.md`;
      - changed: `app/llm/chat.py` (`DEFAULT_MODEL` and `DEFAULT_FALLBACK_MODEL`, if they
        change), `docs/adr/0006-default-extraction-model-openrouter.md` (status "superseded in
        part by ADR 0010", if the default or the fallback changes), `backend/.env.example`
        (the comment, if it names the models) and the README's defaults sentence.

      The ADR records:
      - the match-check architecture B against A, C and D, with the SPEC's reasons;
      - storage at read time and fail-open;
      - the full test table of the six models;
      - the default, the fallback and the "no model passes" rule.

      Tests first:
      - `tests/extraction/evaluation/test_selection.py`, synthetic runs:
        - the cheapest passing model wins;
        - with none passing, the most thresholds then F1 at or under 5 PLN;
        - a model over 5 PLN never wins;
        - the fallback is pair-compatible;
        - with no compatible fallback, the result is `None`.
      - `tests/extraction/evaluation/test_results_files.py`:
        - `::test_v2_rule_picks_the_configured_defaults` (`choose_models` over the committed
          v2 files with `load_model_settings()` gives `config.DEFAULT_MODEL,
          config.DEFAULT_FALLBACK_MODEL`);
        - `::test_adr_0010_names_the_defaults`;
        - `test_adr_0006_names_the_defaults` is kept only while ADR 0006 still names the
          current defaults. Otherwise it asserts the "superseded" status.
      - `tests/test_docs.py::test_adr_0010_weighs_alternatives_a_c_d`.

      If `choose_models` gives no fallback, escalate (`decision`).

      Automatic verification:
      `uv run pytest -q tests/extraction tests/test_docs.py -n auto`, then Verify.
- [ ] 15. **The report `docs/reports/extraction-eval-v2.md` (AC22, AC15).** The sections:
      - "Set composition": the table per split, as in v1, plus the tagged cases, the
        `compare-labels` output against v1 and the pilot;
      - "Dev iterations": from `specs/018-extraction-evaluation-v2/dev-iterations.md`;
      - "Test results": per model, F1, linking, false alarms, `other_match_false_alarms`,
        `lineups_dropped_by_check`, PLN a month, and p50/p95 latency with the match check;
      - "The 60 h filter alone": `other_match_caught_by_lead_only` of
        `other_match_with_raw_events`, per model;
      - "Choice";
      - "Total spend", with the `spend` output;
      - "Threats to validity": the Claude pre-labels against the `claude-haiku-4.5`
        candidate, the noise on about 150 test cases, the judge model and the owner's partial
        review of v1; the two #23 posts in the test split (2106010889262436608 and
        2104158837540622748) that the v5 rules were written from; and the test split's
        over-sampling of line-up posts, which overstates the match check's share of the cost
        a month and of the latency against production;
      - "Re-extraction", the expected effect of the owner's optional `reextract`.

      Tests first:
      - `tests/test_docs.py::test_extraction_report_v2_has_every_section` (the headings, and
        "60 h", "claude-haiku-4.5" and "total spend");
      - `tests/extraction/evaluation/test_results_files.py::test_report_v2_names_every_test_run`.

      Automatic verification:
      `uv run pytest -q tests/test_docs.py tests/extraction/evaluation/test_results_files.py`,
      then Verify.
- [ ] 16. **The documents (AC2, AC23, AC24).**
      - `README.md`:
        - the extraction development section uses only `python -m app.extraction.evaluation`
          and set v2 (`--set`), lists the new commands, and states the match check and its
          storage;
        - a new `## Extraction results` section after `## Retrieval results`, with the test
          table and links to the report and ADR 0010.
      - `docs/EVALUATION.md`:
        - a new "The extraction procedure" section (the commands of step 10 in order,
          neutral ids, `mapping.json`, the estimate first, the `extraction` schema);
        - "Other evaluations" now names only the corroboration judge;
        - the `session_judge.sh` schema sentence names `extraction`.
      - `docs/DEPLOYMENT.md` lines 90–92: the new module path.
      - `docs/DECISIONS.md`, rows at the end, each dated on the day it is written:
        - the match check and its column, filtered at read time beside the 60 h filter (ADR
          0010);
        - fail-open;
        - set v2 with the session pre-labels, the owner's review of only the changed and new
          cases, and the `other_match_lineup` tag;
        - the default model and the "no model passes" rule (ADR 0010).
      - `docs/ROADMAP.md`: the item is split into an 018 item, ticked and linked to this
        SPEC, and an open 019 item for the corroboration half (#12's rest and #47).
      - `docs/BACKLOG.md`:
        - rows 23, 46 and 54 are removed;
        - row 12 keeps only the `contradicts` and corroboration half, with trigger "spec 019";
        - row 47's last column becomes "spec 019";
        - row 55's trigger still names #54, so reword it to "the match check (spec 018) is
          done and…".

      Tests first:
      - `tests/test_readme.py`:
        - `EXTRACTION_COMMANDS` becomes `app.extraction reextract` plus
          `app.extraction.evaluation <cmd>` for the evaluation commands;
        - the snapshot assertion changes to `app.extraction.evaluation snapshot-players`;
        - the new `test_extraction_evaluation_commands_use_the_new_module`: `git ls-files
          '*.md'` outside `specs/` has no match of `python -m app\.extraction
          (evaluate|review|prelabel)`, and README and `docs/EVALUATION.md` contain
          `app.extraction.evaluation evaluate`.
      - `tests/test_docs.py`:
        - `::test_decisions_cover_match_check_and_set_v2`;
        - `::test_roadmap_018_ticked_and_019_open`;
        - `::test_backlog_23_46_54_removed_and_12_narrowed`;
        - update `test_backlog_window_row_and_017_018_renumbering`: row 46 is gone, and row
          47 names "spec 019".

      Automatic verification:
      `uv run pytest -q tests/test_readme.py tests/test_docs.py`, then Verify.
- [ ] 17. **Full verification and the spec's own checks (AC25).**
      - Run Verify.
      - Run `git grep -nE "python -m app\.extraction (evaluate|review|prelabel)" -- '*.md' ':!specs'`,
        which must print nothing.
      - Run `git grep -nE "[0-9]{6,}@|league_id|entry_id" -- backend/evals/extraction/v2`,
        which must print nothing (no manager or league data).
      - Record the end-to-end results below.

      Automatic verification: the three commands above.

## Risks and traps

- **Compose is production.** These commands read the database `DATABASE_URL` names:
  - `snapshot-players`, `snapshot-fixtures` and `export-corpus` read Compose, and that is
    approved for step 10 only;
  - `reextract`, `prelabel` and the migration would write to it and are never run against it.

  Tests use their own containers. Run every command near `backend/.env` with
  `env -u TWEET_SOURCE`.
- **The owner's review is on the critical path** (step 11). The implementer must escalate
  there, not work around it, and must never set `reviewed: true` on a changed or new case
  itself.
- **Moving the CLI breaks imports in tests and docs.** `tests/test_readme.py`,
  `tests/extraction/evaluation/test_eval_set.py` and `test_results_files.py` import from
  `app.extraction.cli`. Step 1 moves them all at once.
- **Existing flow tests script exact replies.** The match check is called only with
  `fixtures`, so tests without fixtures keep their scripts. A service test that seeds fixtures
  and line-up events needs one more scripted reply.
- **Time semantics.** `FixtureIndex.around` must use `kickoff > at` for "next", like the SQL
  in `current_extractions`, or the evaluation and production disagree on the 60 h filter at the
  boundary. Every time is aware UTC.
- **The `other_match_lineup` flag in the verdict** goes beyond `ExtractionOutput` by one field
  (approach item 9). The events still validate as the extraction output schema. The plan review
  accepted this reading of AC17: the flag carries the SPEC's new tag, which the extraction
  output cannot express.
- **The judge prompt includes `extraction.md` as it was at export time (v5 before the dev
  iterations).** `set.json`'s `prelabels` entry records the git commit, so the labels' rules can
  be traced after the prompt is tuned.
- **v1 relabel noise.** A Sonnet slip on a v1 case shows up as a "change" and costs the owner a
  review. The pilot gate bounds this. The report states how many changes the owner reverted.
- **Spend.** The match check adds calls on line-up posts only. The six test runs were about
  $0.29 on v1, and set v2's test split is about 1.6 times larger. The $4 stop in step 13
  leaves room for one `-r2`.
- **Defaults change.** If the default or the fallback changes, the owner's `.env` may still
  set `LLM_MODEL`. The PR description says so.
- **AC2's grep covers the documents, not `specs/`.** The specs of 004, 005 and 016 are history
  and still contain the old command lines. The test excludes `specs/`.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

- `cd backend && uv run python -m app.extraction.evaluation --help` lists `snapshot-players`,
  `snapshot-fixtures`, `prelabel`, `evaluate`, `spend`, `compare-labels`, `review`,
  `export-judge-inputs`, `estimate` and `import-judge`.
  `uv run python -m app.extraction --help` lists only `reextract`.
- The migration round trip on a test container: `uv run pytest -q tests/db/test_migrations.py`
  passes.
- `uv run python -m app.extraction.evaluation compare-labels --set evals/extraction/v2 --baseline evals/extraction/v1/cases.jsonl`
  lists every changed v1 case, and `--set evals/extraction/v2` shows `ids only in the
  baseline: -` for both splits.
- `uv run pytest -q tests/extraction/evaluation/test_eval_set_v2.py` passes (composition, all
  reviewed, the #23 labels).
- `uv run python -m app.extraction.evaluation spend --results-dir evals/extraction/results/v2`
  shows a total of at most 5.00 USD.
- `uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v2 --split dev --model <the winner> --run-name e2e-018`
  on the winner. It writes to `results/v2/dev/` (gitignored), and its output shows the
  `other match false alarms` and `line-ups dropped by the check` lines with the header's three
  prompt versions.
- The full verification command passes.

Results are recorded here by the implementer.

### Manual (performed by the owner)

- Migrate Compose and re-extract one #54 post. In `backend/`, run `uv run alembic upgrade head`,
  then `uv run python -m app.extraction reextract --x-id <a City v AEK id from
  set.json backlog_54_lineups>`.
  Pass when: `SELECT event_type, certainty, for_next_pl_match FROM extraction_event ev JOIN
  extraction e ON e.id = ev.extraction_id WHERE e.tweet_x_id = <id> ORDER BY e.finished_at
  DESC` shows the newest extraction's line-up events with `for_next_pl_match = false`, and
  `uv run python -m app.worker status` shows the extraction loop healthy.

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto` fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, ADR 0010, `docs/EVALUATION.md`,
      `docs/DEPLOYMENT.md`, README and BACKLOG updated
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

## Review log

2026-10-25 — /pipeline:plan-review

Findings:

- `major` — The new `other_match_lineup` tag overlapped the v1 tags `cup_european_lineup` and
  `national_lineup` without a stated relation, so v1's other-match line-ups could go untagged
  and escape `other_match_false_alarms`, and `compare-labels` listed event changes only, so a
  tag-only change would be unreviewed but unlisted (AC12). Approach item 8 now defines the tag
  as every line-up of Premier League players for a match other than the club's next Premier
  League match, beside the v1 tags and outside `NO_EVENT_TAGS`; `compare-labels` lists tag
  changes; step 7's test covers it.
- `major` — The import's "unchanged" rule used `compare_events` as it is, which counts a
  differently spelled mention (an empty-field relabel) and an ambiguous name the import cannot
  link (`fpl_id` null against v1's id) as changes. That would send unchanged v1 cases to the
  owner and depress the pilot gate's 80% and F1 figures. Approach item 9 now carries v1's
  `fpl_id` onto an unlinked paired event and ignores empty-field relabels; step 8's test
  covers both.
- `major` — Step 12 bumped `match_check.md` to version 2 on any dev edit, which contradicts
  AC9 ("match check 1") and step 6's test asserting `match_check@1`. The match-check prompt now
  follows the extraction prompt: uncommitted dev edits, frozen at version 1.
- `minor` — The dev-iteration log lived in the session scratchpad, which a restarted
  implementer (after the step 11 or step 13 escalation) cannot read, and step 15 needs it for
  AC15. It now lives in `specs/018-extraction-evaluation-v2/dev-iterations.md`, committed with
  the freeze.
- `minor` — The import validated events with `ExtractedEvent`, which ignores unknown keys, so
  step 8's "extra key" test could pass only at the top level. Events are now validated with an
  `extra="forbid"` subclass.
- `minor` — The report's threats to validity missed two #23 posts that fall in the test split
  (x_id % 10 = 8) and shaped the v5 rules, and the line-up over-sampling of the test split,
  which overstates the match check's cost a month and latency. Step 15 now names both.

Checked and found correct:

- Coverage: every AC1–AC25 has a step and a proving test, and the matrix matches the steps.
- Order: no forward dependency (step 1 before step 5's `prelabel` edit; steps 3, 4 before 5
  and 6; prompt v5 in step 9 before the judge export of step 10; `snapshot-fixtures` in step
  6 before step 10).
- Code claims: `call` traces a raised call and a `NoParsedResult` with its error class, so
  the fail-open node meets AC6; `spend` uses `rglob`, so `results/v2/dev/` counts toward AC20;
  `current_extractions` filters only under `fresh_lineups_only`, and `claimed_post_ids` reads
  unfiltered, so the 2026-10-24 "still counts as claimed" rule holds; the v1 result tests glob
  `results/*.json` only, so `results/v2/` does not disturb them; `test_adr_0006_names_the_defaults`
  is handled in step 14; migration 0013 is the head; `export-corpus` takes `--set`, `--since`
  and `--until` (Europe/Warsaw); extraction may not import `app.retrieval`, and the plan reads
  the corpus through its own model.
- Decisions: 2026-09-28 (set format, split, thresholds, cheapest passing default), 2026-09-29
  (pair rule, OpenRouter only), 2026-10-12 (session judge, blind ids, mapping outside),
  2026-10-15 (one tracing path, `eval_results`, `current_extractions` as the read API, module
  boundaries), 2026-10-24 (the 60 h filter at read time, unchanged) are kept. The `data:`
  commit type follows the repository's practice for evaluation data.
- The `other_match_lineup` flag in the verdict is an acceptable reading of AC17.
- Step 11 is the implementer's escalation for the review the SPEC gives the owner, not owner
  work placed in `## Steps`.
- Owner summary: no new dependency; migration 0014 accepted in SPEC → Owner decisions; the
  manual scenario has a `Pass when:` line; the plan is in English.

The plan is ready: every finding was fixable in the plan and is fixed, and the only migration
is the one the owner accepted in the SPEC.

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_
