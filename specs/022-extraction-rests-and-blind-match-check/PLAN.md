# PLAN 022 — Rests give no event, and a match check blind to the event types

## Owner summary

- **Approach:** Code first, then the data, then the paid runs. The extraction prompt becomes v7:
  a rest, a rotation, a squad omission or a suspension that concerns only a cup, European or
  national-team match gives no event, while an injury or illness from any match keeps its
  `out`/`doubt`. The match check becomes `match_check` v3: its input lists the club's player
  names without their event types, and its rules no longer mention types. It still runs once per
  post and club for `out`, `doubt`, `benched` and `confirmed_starter`. `evaluate` gains one metric
  (the `other_match_absence` cases with an `out`/`doubt` before the check). Set v4 is set v3 with
  the wrong tag removed from 2115797596119740910, plus about 40 new GW9 posts from a read-only
  Compose export, including the four named posts. The session judge pre-labels the new posts and
  you review them. Then the default model runs the dev iterations and three test runs. A
  report and the documents close the spec.
- **Main risks:**
  - The implementation stops once, for your `review` of set v4 (step 6). AC6 asks for your
    review of every new and changed case. It may stop again if a `claude -p` call or a read-only
    Compose read is refused, if the judge export goes over 1.5M tokens, or if the mean of the
    three test runs fails a threshold (AC10: the report says so and the spec stops for you).
  - A blind check loses a hint the type gave it. "Injuries dropped by the check" measures that
    cost in every run (at most 1 per run).
  - Lane 021 changes `docs/DECISIONS.md`, `docs/ROADMAP.md` and alert code in parallel. A
    conflict on `git merge origin/main` is an escalation.
- **New dependency:** no (SPEC → "Owner decisions": "No new dependency and no migration").
- **Data migration:** no. The prompts and the evaluation change; `for_next_pl_match` and the
  event types are unchanged (SPEC → "Owner decisions").
- **Manual scenarios for the owner:** 1. After the merge, re-extract the current alert window on
  Compose and check in `app.alerts preview` that the FFScout rest post gives no absence (AC12).

## Approach

Read for this plan:

- `specs/022-extraction-rests-and-blind-match-check/SPEC.md` — read in full.
- `docs/CONVENTIONS.md` — read in full.
- `specs/020-availability-and-match-check/PLAN.md` — read in full (steps 2, 3, 12–19, the risks
  and the deviations). That plan built set v3 and the match check v2, and this plan reuses its
  pipeline: snapshots, `export-corpus`, `export-judge-inputs`, `session_judge.sh`,
  `import-judge`, `review`, `evaluate` and `spend`.
- `docs/DECISIONS.md` — searched for "match check", "2026-10-30", "fail-open", "set v3". This
  found the 2026-10-26 match-check row, the 2026-10-30 rows (`available`, the match check for
  absences, extraction set v3) and the append-only rule: a row already on `main` is superseded
  by a new row, and the old row gets a "(superseded in part by …)" prefix.
- `docs/ROADMAP.md` — searched for "020", "Stage 2". The 020 item is the last ticked Stage 2
  extraction item, and the new item goes after it.
- `docs/EVALUATION.md` — searched for "The extraction procedure". That section names
  `evals/extraction/v3` in every command and lists the set v3 tags.
- `docs/DEVELOPMENT.md` — searched for "evaluate", "extraction/v3". The extraction evaluation
  examples name `evals/extraction/v3`. No command or option changes.
- `docs/BACKLOG.md` — searched for "#55", "#60", "61". Nothing is closed by this spec.
- `docs/adr/0010-extraction-match-check-and-default-model.md` — searched for "threshold". This
  gave the five thresholds that `threshold_flags` in `app/extraction/evaluation/metrics.py`
  encodes (F1 ≥ 0.85, linking ≥ 0.95, false alarms ≤ 5%, ≤ 5 PLN a month, no errored case).
- `docs/reports/extraction-eval-v3.md` — searched for its `##` headings (Summary, Set
  composition, Dev iterations, Test results, Corroboration regression, Total spend, Threats to
  validity, Re-extraction).
- Code read in full: `app/extraction/flow.py`, `app/content/prompts/extraction.md` (v6),
  `app/content/prompts/match_check.md` (v2), `app/content/prompts/extraction_prelabel.md` (v2),
  `app/extraction/evaluation/cases.py`, `dataset.py`, `runner.py` and `metrics.py`. Code read in
  part: `app/extraction/evaluation/cli.py` (`evaluate`, `spend`, `export-judge-inputs`,
  `import-judge`, `review`), `judging.py` (`select_candidates`, `import_verdicts`) and
  `scripts/session_judge.sh` (header). Tests read: `tests/extraction/test_flow.py` (the
  match-check block), `tests/content/test_prompts.py`,
  `tests/extraction/evaluation/test_results_files.py`, `test_eval_set_v3.py`, and the
  2026-10-30 tests of `tests/test_docs.py`.
- Data inspected: `evals/extraction/v3/cases.jsonl` (298 cases: 91 dev and 207 test, 29
  synthetic, 14 `other_match_absence` with 12 in test). 2116079251476611126 is a test case
  tagged `other_match_absence` with no events. 2115797596119740910 is a dev case tagged
  `other_match_absence` with two `available` events. None of the other four named posts is in v3.
  The latest real case is 2026-10-30T15:26Z.

Design:

1. **`match_check` v3, blind to types** (`app/extraction/flow.py`, `_render_match_check`). The
   `Events:` block becomes `Players:`, with one `- <mention>` line per checked player of that
   club. The lines keep event order, and a repeated mention appears once. The other lines
   (`Post`, `Posted at`, `Club`, `Previous PL fixture`, `Next PL fixture`) stay as they are. The
   selection in `match_check_node` (`MATCH_CHECKED_EVENTS`, linked events only, one call per
   club, fail-open on an error or a null answer) is unchanged, so AC4 holds by the existing tests.
   The prompt `app/content/prompts/match_check.md` becomes version 3. It asks the same question
   about "the post's news about these players". It no longer says "the events extracted" or
   `- <player>: <event type>`, and it has no rule keyed to a type. Its rules:
   - true: an injury or illness, whichever match it comes from; team news (a line-up, an
     absence, a return) about the next Premier League match, named or posted in the days before
     it with no other match in between;
   - false: news that concerns only another match. That covers a line-up for a cup, European,
     national-team, friendly, women's or youth match, or for a Premier League match already
     played. It also covers a player rested, rotated or left out of such a squad with no injury
     given, and a suspension served in another competition. A post that names the next Premier
     League gameweek only to say the player was rested in the other match to be fresh for it
     ("rested for GW9" after a cup tie) is still about the other match's absence: false. A
     player said to be rested from or left out of the next Premier League match itself is true;
   - the opponent and timing rules of v2 stay, reworded without types.
2. **Extraction prompt v7** (`app/content/prompts/extraction.md`). The v6 bullet "A squad rest,
   a rotation or a suspension served in a cup or European tie is extracted as the post says it
   (`out` or `doubt`): the match check decides later which match it concerns." is replaced by:
   "A player rested, rotated or left out of the squad for a cup, European or national-team match
   with no injury or illness given gives no event, and neither does a suspension served in such
   a match: the absence concerns only that match. An injury or illness from any match keeps its
   `doubt` or `out`." One v6 sentence needs a scope to stay consistent with the new rule: the
   relevance-rule bullet "A player who misses a national-team, cup or European match, or leaves
   the national-team camp injured, is `doubt` …" reads, without a reason, as "a cup rest is
   `doubt`". It becomes "A player who misses a national-team, cup or European match through an
   injury or illness, or leaves the national-team camp injured, is `doubt` …", the rest of the
   bullet unchanged; this keeps that bullet's own meaning (its first sentence is about injuries
   and illnesses). Every other v6 rule stays word for word. That includes the rule that a
   player who misses a European or cup squad *because of a knock* is `doubt`. The new rule
   names "no injury or illness given", so the two rules do not clash. `extraction_prelabel.md` v2
   already says "such an absence gives no `out` and no `doubt` event", so it is consistent with
   v7 and stays unchanged.
3. **The raw-absence metric (AC8).** `Metrics.other_match_absence_raw_absences: int = 0` counts
   the `other_match_absence` cases that did not error and have an `out` or `doubt` among
   `raw_predicted`, before the read-time filter. `evaluate` prints
   `other match absence cases with a raw absence: k of n`. This is a new output line, not a new
   command or option (SPEC: "no CLI change"). The FFScout post's outcome per run comes from the
   result file's `case_results` (`raw_predicted` with `for_next_pl_match`, and `predicted`).
4. **The mean of three runs (AC7).** A pure function `mean_of_runs(metrics: Sequence[dict]) ->
   dict[str, float]` in `metrics.py` returns the mean of `f1`, `linking_accuracy`,
   `false_alarm_rate`, `projected_monthly_cost_pln`, `errored_cases`,
   `other_match_absence_false_alarms`, `injuries_dropped_by_check` and
   `other_match_absence_raw_absences` (a missing cost makes the mean cost `None`). The tests and
   the report both feed the mean to `threshold_flags`, so ADR 0010's thresholds have a single
   definition. Variant rejected: a new `mean` CLI command (SPEC: no CLI change). Variant
   rejected: the mean computed only in the test file (the report needs the same numbers).
5. **Set v4** (`backend/evals/extraction/v4/`): `cases.jsonl` starts as a copy of v3.
   2115797596119740910 loses `other_match_absence` and gets `reviewed: false` and
   `review_source: null`, so the owner reviews it as the one changed case. `set.json` is v3's
   with `version: "v4"`, keeping `list_handles`, `prelabels` and `backlog_cases`. `players.json`
   and `fixtures.json` are fresh read-only snapshots. New cases: a read-only `export-corpus` of
   the GW9 window (from 2026-10-28T00:00 Europe/Warsaw to the earliest gameweek 9 kickoff in the
   new `fixtures.json`). They go through `export-judge-inputs --availability-keywords
   --exclude-cases <v3 cases> --x-id` for the four new named posts (2116079251476611126 is
   already a v3 case and stays excluded), then the session judge, then
   `import-judge --select 40`. The named posts are `required`, so `select_candidates` always
   keeps them. `import_verdicts` gives new cases the v3 split rule (`x_id % 10 < 3` → dev), so
   about two thirds go to test, and 2116079251476611126 stays a test case from v3.
   `composition_problems_v4` in `cases.py` calls `composition_problems_v3(cases, snapshot,
   manifest, v2_ids)`, which holds every v3 rule. It then adds:
   - every v3 id is present;
   - the four new named posts are present: Kroupi.Jr 2115825835525488887 with a `benched` event
     whose mention contains "Kroupi" and no `out`/`doubt` for him, and 2116448660980732008 with
     no event;
   - 2116079251476611126 is in test with no `out`/`doubt`;
   - 2115797596119740910 has no `other_match_absence` tag.

   The constants `SPEC_022_CASES`, `KROUPI_CASE`, `TIPS_CASE`, `REST_POST_CASE` and
   `WRONG_TAG_CASE` sit next to the v3 ones. v3's labels need no relabelling under v7: the v3
   composition rule already forbids an `out`/`doubt` in `other_match_absence` cases, and v3's
   suspensions served in a cup were labelled with no event (020 deviation, step 15).
6. **Measurement.** Dev iterations use `--split dev` only, at most five, and edit
   `extraction.md` and `match_check.md` only for dev failures. The versions stay 7 and 3. The
   three test runs are `test-v4-openai-gpt-6-luna-1`, `-2` and `-3`, on a clean committed tree.
   A run with an errored case gets one repeat, `-<n>-r2`, which replaces it as that run's final.
   The spend log `specs/022-extraction-rests-and-blind-match-check/dev-iterations.md` follows
   020's line format (`Extraction dev spend: <x> USD`, `Discarded test spend: <x> USD`), so the
   cap test can add it to the result files' `total_cost_usd`.

Existing patterns reused: the test shapes of `tests/extraction/evaluation/test_eval_set_v3.py`
and `test_results_files.py` (the v3 block), `composition_problems_v3`, the
`tests/content/test_prompts.py` version-and-phrase tests, the `_checked_flow` helper and the
`FIXTURES` and `CHECK_INDEX` data in `tests/extraction/test_flow.py`, and the
`tests/test_docs.py` row lookups.

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 2 | `tests/content/test_prompts.py::test_extraction_prompt_v7_rules` |
| AC2 | 2, 7, 8 | `tests/extraction/test_flow.py::test_an_extraction_with_no_event_makes_no_match_check`, `tests/extraction/test_service.py::test_a_post_with_no_event_is_stored_with_no_events`; the measurement: `test_results_files.py::test_v4_mean_passes` |
| AC3 | 1 | `tests/extraction/test_flow.py::test_match_check_input_holds_no_event_type`, `tests/content/test_prompts.py::test_match_check_prompt_v3_rules` |
| AC4 | 1 | `tests/extraction/test_flow.py::test_match_check_one_call_per_club`, `test_out_and_starter_of_one_club_make_one_check`, `test_doubt_and_benched_of_one_club_make_one_check`, `test_no_match_check_for_available_only`, `test_match_check_failure_is_fail_open`; `tests/extraction/test_lineups.py` (the `kept_at_read` table, unchanged) |
| AC5 | 4, 5, 6 | `tests/extraction/evaluation/test_cases.py::test_composition_v4_*`, `tests/extraction/evaluation/test_eval_set_v4.py::test_composition_v4`, `test_every_v3_case_in_v4`, `test_named_gw9_cases` |
| AC6 | 5, 6 | `tests/extraction/evaluation/test_eval_set_v4.py::test_new_and_changed_cases_reviewed_by_the_owner`, `test_every_case_reviewed`, `test_prelabel_judge_named` |
| AC7 | 3, 7, 8 | `tests/extraction/evaluation/test_metrics.py::test_mean_of_runs`; `test_results_files.py::test_v4_three_test_runs`, `test_v4_mean_passes`, `test_v4_each_run_passes_the_absence_checks` |
| AC8 | 3, 8, 9 | `test_metrics.py::test_other_match_absence_raw_absences`, `tests/extraction/evaluation/test_cli.py::test_evaluate_prints_the_raw_absences`; `test_results_files.py::test_v4_mean_raw_absences_at_most_one`, `test_report_v4_gives_the_rest_post_per_run` |
| AC9 | 7, 8, 9 | `test_results_files.py::test_v4_spend_within_cap`; `tests/test_docs.py::test_extraction_report_v4_has_every_section` ("Total spend") |
| AC10 | 9 | `tests/test_docs.py::test_extraction_report_v4_has_every_section`, `test_results_files.py::test_report_v4_names_the_test_runs` |
| AC11 | 10 | `tests/test_docs.py::test_decisions_cover_rests_and_blind_match_check`, `test_evaluation_md_names_set_v4`, `test_roadmap_022_ticked` |
| AC12 | — | manual (the owner, after the merge; "Manual" below) |

## Steps

- [x] 1. **The blind match check, `match_check` v3 (AC3, AC4).** `iterations: 0` Files:
      `app/extraction/flow.py` (`_render_match_check`: design item 1),
      `app/content/prompts/match_check.md` (version 3, design item 1).

      Tests first:
      - `tests/extraction/test_flow.py`:
        - `test_match_check_input_from_fixture_data`: the asserts `"Events:"` and
          `"- Haaland: benched"` are replaced by `"Players:"` and `"- Haaland"` as a line of
          its own (`"- Haaland" in sent.splitlines()`). The fixture and club asserts stay;
        - new `test_match_check_input_holds_no_event_type`: a post whose text uses none of the
          type words ("Haaland and Jones news."), with events `out` (Haaland) and
          `confirmed_starter` (Jones), both of Team One. The rendered human message has
          neither `"Events:"` nor `": out"` nor `": confirmed_starter"`, and no line after
          `Players:` contains any of `EVENT_TYPES` (`app.extraction.evaluation.cases`). The
          `Players:` block is exactly `["- Haaland", "- Jones"]`;
        - new `test_a_repeated_mention_is_listed_once`: two events for "Haaland" (`doubt` and
          `benched`) give one `- Haaland` line and one call, and both events get the answer;
        - new `test_doubt_and_benched_of_one_club_make_one_check` (AC4: `doubt` and `benched`
          of one club make 2 LLM calls in total, both get the answer). The existing
          `test_match_check_one_call_per_club`, `test_out_and_starter_of_one_club_make_one_check`,
          `test_no_match_check_for_available_only`,
          `test_match_check_answer_on_that_clubs_checked_events_only` and
          `test_match_check_failure_is_fail_open` stay unchanged;
      - `tests/content/test_prompts.py`: `test_match_check_prompt_v3_rules` replaces
        `test_match_check_prompt_v2_rules`. It checks version 3 and the phrases "rest",
        "rotat", "suspension", "injury", "illness", "cup", "european", "national",
        "for_next_pl_match" and "players". It also checks that "event type", "events
        extracted" and "<event type>" are gone (case-insensitive);
      - `tests/extraction/evaluation/test_cli.py` (about line 1023): `"match_check@2"` becomes
        `"match_check@3"`.

      Automatic verification:
      `cd backend && uv run pytest -q tests/extraction/test_flow.py tests/content tests/extraction/evaluation/test_cli.py tests/extraction/test_lineups.py`,
      then Verify (`cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`).

- [x] 2. **Extraction prompt v7 (AC1, AC2).** `iterations: 0` Files: `app/content/prompts/extraction.md`
      (version 7, design item 2).

      Tests first:
      - `tests/content/test_prompts.py::test_extraction_prompt_v7_rules` replaces
        `test_extraction_prompt_v6_rules`. It checks version 7 and every phrase v6 checked
        ("own team", "hypothetical", "national team", "flag", "never `confirmed`",
        "`available`", "trained", "not suspended", "fatigue", "fake", "one of the five
        types"). It also checks the new rule's phrases on the whitespace-normalised lowercase
        text: "rested, rotated or left out of the squad", "with no injury or illness given
        gives no event", "a suspension served in such a match" and "an injury or illness from
        any match keeps its", plus the scoped bullet's "misses a national-team, cup or european
        match through an injury or illness". It checks that "the match check decides later" and "is extracted
        as the post says it" are gone, and that "there is no positive availability type"
        stays absent;
      - `tests/extraction/test_flow.py::test_an_extraction_with_no_event_makes_no_match_check`:
        a fake model returns `ExtractionOutput(events=[])` with `FIXTURES`. The flow returns no
        events and `llm_calls == 1`;
      - `tests/extraction/test_service.py::test_a_post_with_no_event_is_stored_with_no_events`
        (db): the service runs with a fake model returning no events and stores an `extracted`
        row with zero events. If an existing test already asserts exactly this, name it in
        `## Deviations` instead of adding a duplicate.

      Automatic verification:
      `cd backend && uv run pytest -q tests/content tests/extraction/test_flow.py tests/extraction/test_service.py`,
      then Verify.

- [x] 3. **The raw-absence metric and the mean of runs (AC7, AC8 code).** `iterations: 1` Files:
      `app/extraction/evaluation/metrics.py` (`other_match_absence_raw_absences`,
      `mean_of_runs`: design items 3 and 4), `app/extraction/evaluation/cli.py` (the
      `evaluate` line printed after `other match absence false alarms`).

      Tests first:
      - `tests/extraction/evaluation/test_metrics.py`:
        - `test_other_match_absence_raw_absences`. Four cases: an `other_match_absence` case
          with a raw `doubt` answered false (counted 1, while the false alarms count 0), an
          `other_match_absence` case with only a raw `benched` (0), an errored
          `other_match_absence` case (not counted), and a case without the tag that has a raw
          `out` (0). The result is 1;
        - `test_mean_of_runs`: three metric dicts give the expected means, and `threshold_flags`
          on them is all true at F1 0.86/0.84/0.88 (mean 0.86). A run with
          `projected_monthly_cost_pln` `None` makes the mean cost `None`;
      - `tests/extraction/evaluation/test_cli.py::test_evaluate_prints_the_raw_absences`: the
        stubbed `evaluate` prints `other match absence cases with a raw absence: ` and the
        result JSON's `metrics` holds `other_match_absence_raw_absences`.

      Automatic verification: `cd backend && uv run pytest -q tests/extraction/evaluation -n auto`,
      then Verify.

- [x] 4. **Set v4 composition rules (AC5 code).** `iterations: 0` Files: `app/extraction/evaluation/cases.py`
      (`composition_problems_v4(cases, snapshot, manifest, v2_ids, v3_ids)` and the constants
      of design item 5). `DEFAULT_SET_DIR` stays `v3` until step 6 (020 deviation:
      `tests/extraction/test_linking.py` reads the default set).

      Tests first, in `tests/extraction/evaluation/test_cases.py`, each on a small synthetic set
      built from the existing v3 test helpers:
      - `test_composition_v4_accepts_a_valid_set`;
      - `test_composition_v4_needs_every_v3_case`;
      - `test_composition_v4_named_cases_present`;
      - `test_composition_v4_kroupi_is_benched` (an `out` for Kroupi fails, as does a missing
        `benched`);
      - `test_composition_v4_tips_post_has_no_event`;
      - `test_composition_v4_rest_post_in_test_without_absence`;
      - `test_composition_v4_wrong_tag_removed`;
      - `test_composition_v4_keeps_the_v3_rules` (an `other_match_absence` case with an `out`
        still fails).

      Automatic verification:
      `cd backend && uv run pytest -q tests/extraction/evaluation/test_cases.py`, then Verify.

- [x] 5. **Build set v4 with session pre-labels (AC5, AC6 data).** `iterations: 1` The owner-approved SPEC
      (AC5: new real cases from the GW9 window) is the request for these Compose reads, and they
      are the only Compose reads of this plan: `snapshot-players`, `snapshot-fixtures` and
      `export-corpus`, all read-only. No command here writes to Compose. Prefix every CLI
      command with `env -u TWEET_SOURCE` and run from `backend/`. `$S` is the session
      scratchpad.
      - Write `tests/extraction/evaluation/test_eval_set_v4.py` first, modelled on
        `test_eval_set_v3.py`:
        - `test_schema` (version `v4`, `prelabels` non-empty);
        - `test_every_v3_case_in_v4`;
        - `test_named_gw9_cases` (the five ids of `SPEC_022_CASES` present, and
          2116079251476611126 in test);
        - `test_wrong_tag_removed`;
        - `test_fixtures_snapshot_covers_every_case`;
        - `test_prelabel_judge_named` (the last `prelabels` entry names the session judge and
          `extraction@7`);
        - `test_every_case_reviewed`;
        - `test_new_and_changed_cases_reviewed_by_the_owner`. Every case whose id is not in
          v3, or whose `tags` or `expected_events` differ from v3's, has `reviewed: true` and
          `review_source == "owner"`;
        - `test_composition_v4` (`composition_problems_v4(...) == []`).

        The last three stay red until step 6.
      - Create `evals/extraction/v4/`. Copy `v3/cases.jsonl` and `v3/set.json` (set `version`
        to `"v4"`). Run `uv run python -m app.extraction.evaluation snapshot-players --set evals/extraction/v4`
        and `... snapshot-fixtures --set evals/extraction/v4`. If the fresh `players.json` lacks
        an `fpl_id` that a v3 case uses, escalate (`decision`); do not edit the snapshot.
      - Remove the wrong tag with a short Python one-off (no new code in `app/`): case
        2115797596119740910 loses `other_match_absence` and gets `reviewed: false` and
        `review_source: null`. The file is written with `write_cases`.
      - The corpus:
        `uv run python -m app.retrieval.evaluation export-corpus --set $S/compose --since 2026-10-28T00:00 --until <the earliest gameweek-9 kickoff in evals/extraction/v4/fixtures.json, in Europe/Warsaw> --version v4-scratch`.
      - Candidates:
        `uv run python -m app.extraction.evaluation export-judge-inputs --set evals/extraction/v4 --corpus $S/compose/corpus.jsonl --availability-keywords --exclude-cases evals/extraction/v3/cases.jsonl --x-id 2115825835525488887 --x-id 2116172084082602140 --x-id 2116226240323465435 --x-id 2116448660980732008 --seed 22 --out $S/judge-v4`.
        A named post missing from the corpus → escalate (`decision`). If the printed estimate
        exceeds 1.5M tokens, re-export with `--since 2026-10-29T00:00`. If it still exceeds
        1.5M, escalate (`decision`) before judging.
      - Judge: `../scripts/session_judge.sh $S/judge-v4 6 sonnet extraction medium`, and re-run
        it once for any failures. Inputs that still fail are judged with `opus`, and that is
        recorded in `## Deviations`.
      - Import: `uv run python -m app.extraction.evaluation import-judge --export $S/judge-v4 --set evals/extraction/v4 --judge-model "claude-sonnet-5-5 (Claude Code session)" --select 40 --seed 22`.
      - Run `composition_problems_v4` through
        `uv run pytest -q tests/extraction/evaluation/test_eval_set_v4.py -k composition`. It may
        fail only on the named-case expectations and on unreviewed cases, which the owner
        settles. If a v3 rule (counts, dev share, split) fails, import a further draw with
        `--seed 23` once; after that, escalate (`decision`).
      - Commit the set unreviewed: `data: build extraction set v4 candidates`. A refused
        `claude -p` or Compose read → escalate (`permission`).

      Automatic verification:
      `cd backend && uv run pytest -q tests/extraction/evaluation/test_eval_set_v4.py -k "schema or every_v3 or named or wrong_tag or fixtures or judge_named"`
      green. The failures of `test_every_case_reviewed`,
      `test_new_and_changed_cases_reviewed_by_the_owner` and `test_composition_v4` are only on
      unreviewed cases or named-case expectations, and they are recorded in `## Deviations`.
      Then Verify with
      `--deselect tests/extraction/evaluation/test_eval_set_v4.py::test_every_case_reviewed --deselect tests/extraction/evaluation/test_eval_set_v4.py::test_new_and_changed_cases_reviewed_by_the_owner --deselect tests/extraction/evaluation/test_eval_set_v4.py::test_composition_v4`.

- [ ] 6. **The owner's review of set v4 (AC6): the implementer stops here.**
      - End the run with `RESULT: ESCALATE` (`KIND: decision`). The message gives:
        - the counts: new cases per split, the one changed case, and the candidates judged;
        - the command
          `cd /home/czarny/Projects/worktrees/022-extraction-rests-and-blind-match-check/backend && uv run python -m app.extraction.evaluation review --set evals/extraction/v4`;
        - the reminders. Under v7, a rest, rotation, squad omission or suspension that concerns
          only another match gives no event. Kroupi.Jr in 2115825835525488887 is `benched`, and
          2116448660980732008 (a tips post) gives no event. An `other_match_absence` case
          expects no `out`/`doubt`, and a `cup_injury` case keeps its own. 2115797596119740910
          lost its `other_match_absence` tag and keeps its two `available` events.
      - On resume: run `test_eval_set_v4.py` in full. If a composition rule fails after the
        review, report it and escalate again; do not relabel the owner's cases. Set
        `DEFAULT_SET_DIR = EVALS_DIR / "v4"` in `app/extraction/evaluation/dataset.py`, then
        run `tests/extraction/test_linking.py` (it reads the default set's `players.json`). Commit
        `data: review extraction set v4` if the owner has not committed the review, and the
        default switch as `chore: point the extraction evaluation at set v4`.

      Automatic verification:
      `cd backend && uv run pytest -q tests/extraction/evaluation/test_eval_set_v4.py tests/extraction/test_linking.py`,
      then Verify.

- [ ] 7. **Dev iterations of prompts v7 and `match_check` v3 with the default model (AC2, AC7
      dev).** Use the paid key from `backend/.env`.
      - Iterate
        `env -u TWEET_SOURCE uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v4 --split dev --model openai/gpt-6-luna --run-name dev-v4-luna-iN`.
      - Edit `extraction.md` and `match_check.md` for dev failures only. Do not patch single
        slips (SPEC "Out of scope": Kroupi's "not ready to start"). The rule texts of design
        items 1 and 2, and the phrases step 1 and 2 tests check, stay. Stop at five iterations,
        or when dev passes the five thresholds, both absence checks, and at most 1 raw absence.
      - Log every iteration in `specs/022-extraction-rests-and-blind-match-check/dev-iterations.md`:
        the run name, F1, `available_f1`, the false-alarm rate, the absence false alarms, the
        raw absences, the injuries dropped, the cost, and the change made. Keep one line
        `Extraction dev spend: <x> USD`, the `spend --results-dir evals/extraction/results/v4`
        total of the dev runs.
      - Escalate (`decision`) if the dev spend reaches 2 USD.
      - Freeze: commit the prompts and the log as
        `feat: freeze extraction prompt v7 and match check v3`. The versions stay 7 and 3.

      Automatic verification: `cd backend && uv run pytest -q tests/content tests/extraction -n auto`,
      and `uv run python -m app.extraction.evaluation spend --results-dir evals/extraction/results/v4`
      printing at most 2.00 USD. Then Verify.

- [ ] 8. **Three test runs (AC7, AC8, AC9).** Tests first, in
      `tests/extraction/evaluation/test_results_files.py` (a v4 block after the v3 block; the v1,
      v2 and v3 tests stay as they are):
      - `test_v4_three_test_runs`. `results/v4/*.json` holds `test-v4-openai-gpt-6-luna-1`,
        `-2` and `-3`, each with at most one `-r2`. Every header has `set_version "v4"`, the
        prompt version `extraction@7+link_disambiguation@1+match_check@3` and `split "test"`;
      - `test_v4_mean_passes`: `threshold_flags` on `mean_of_runs` of the three final runs
        (each run's `-r2` if it exists) is all true;
      - `test_v4_each_run_passes_the_absence_checks`: `absence_checks` holds for each final run
        (at most 1 `other_match_absence` false alarm, at most 1 injury dropped);
      - `test_v4_mean_raw_absences_at_most_one`;
      - `test_v4_spend_within_cap`: every v4 test run's `total_cost_usd` (including discarded
        `-r2` originals), plus the `Extraction dev spend` and `Discarded test spend` lines of
        `dev-iterations.md`, is at most 5.00.

      Commit these tests first (red until the runs exist:
      `test: add the v4 test-run checks`), so the runs start from a clean tree and their headers
      carry `git_dirty: false`. Then run each, on that clean committed tree:
      `env -u TWEET_SOURCE uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v4 --split test --model openai/gpt-6-luna --run-name test-v4-openai-gpt-6-luna-<n>`
      for n = 1, 2, 3. An errored case → one repeat as `-<n>-r2`. Commit the result files
      (`data: add the three extraction test runs on set v4`).

      If the mean fails a threshold, or a run fails an absence check or the raw-absence mean,
      write the report (step 9) saying so, commit it, and escalate (`decision`). Never switch
      the model (AC10).

      Automatic verification:
      `cd backend && uv run pytest -q tests/extraction/evaluation/test_results_files.py`, then
      Verify.

- [ ] 9. **The report `docs/reports/extraction-eval-v4.md` (AC8, AC9, AC10).** It has these
      sections:
      - "## Summary": the verdict on the mean and on each run;
      - "## Set composition": per split, the tags, the new GW9 cases, the tag fix, and the
        `compare-labels --set evals/extraction/v4 --baseline evals/extraction/v3/cases.jsonl`
        output;
      - "## Review": the counts per `review_source`, and the cases the owner changed;
      - "## Dev iterations": from `dev-iterations.md`;
      - "## Test results": a table with one row per run and a mean row. The columns are F1,
        linking, false alarms, PLN a month, errored cases, `other_match_absence` false alarms,
        raw absences and injuries dropped. Under the table, the five thresholds on the mean
        (`threshold_flags(mean_of_runs(...))`);
      - "## The FFScout rest post": 2116079251476611126 in each run, raw (`raw_predicted` types
        and `for_next_pl_match`) and after the check (`predicted`);
      - "## Total spend": the `spend --results-dir evals/extraction/results/v4` output and the
        total against the 5 USD cap, with the judge named as the Claude Code session;
      - "## Threats to validity": the judge pre-labels, the GW9 cases written from the posts of
        this spec, the one answer per post and club, and the variance (BACKLOG #60);
      - "## Re-extraction": what the owner's AC12 step is expected to change.

      Tests first:
      - `tests/test_docs.py::test_extraction_report_v4_has_every_section` (the nine headings
        and the needles `review_source`, `2116079251476611126`, `Total spend`,
        `openai/gpt-6-luna`);
      - `tests/extraction/evaluation/test_results_files.py::test_report_v4_names_the_test_runs`
        (each final run name and its F1 to three decimals, plus the mean F1 to three decimals);
      - `test_report_v4_gives_the_rest_post_per_run` (the section after "## The FFScout rest
        post" names each final run).

      Automatic verification:
      `cd backend && uv run pytest -q tests/test_docs.py tests/extraction/evaluation/test_results_files.py`,
      then Verify.

- [ ] 10. **The documents (AC11) and the roadmap.** Files:
      - `docs/DECISIONS.md`, appended rows dated the day of the step:
        - "Extraction prompt v7 and the blind match check v3 (spec 022)". A rest, a rotation, a
          squad omission or a suspension that concerns only a cup, European or national-team
          match gives no event in extraction, and an injury or illness keeps its `out`/`doubt`.
          `match_check` v3 reads the post, the time, the club, the fixtures and the player
          names, with no event type. The check still runs for `out`, `doubt` and line-ups,
          never for `available`; it is kept on a null answer, and a false answer is dropped at
          read time. The row supersedes in part the 2026-10-30 match-check row (the part where
          a rest is "extracted as the post says it" and the check alone drops it) and the
          `Events:` input of the same row. Rejected alternatives: the check alone (v6), the
          types in the input (v2), one answer per event. Rationale: two independent barriers;
          the type steered the answer;
        - "Extraction set v4 (spec 022, `backend/evals/extraction/v4/`)": set v3 plus the GW9
          cases and the tag fix, the owner's review, three test runs and their mean against
          ADR 0010's thresholds.

        The 2026-10-30 match-check row gets the prefix "(superseded in part by <date>, spec
        022)" in its Decision cell.
      - `docs/EVALUATION.md`, the extraction procedure: the commands name
        `evals/extraction/v4`. One paragraph adds set v4 (spec 022), the three test runs and
        their mean, and the raw-absence line of `evaluate`. The `--availability-keywords` and
        `other_match_absence` text stays.
      - `docs/DEVELOPMENT.md`: the extraction evaluation examples name `evals/extraction/v4`.
        No command or option changes.
      - `docs/ROADMAP.md`: a ticked Stage 2 item after the 020 item: "Rests give no event and a
        blind match check: extraction prompt v7, `match_check` v3, extraction set v4 and three
        test runs ([report](reports/extraction-eval-v4.md)) (spec:
        [022](../specs/022-extraction-rests-and-blind-match-check/SPEC.md))".

      Tests first, in `tests/test_docs.py`:
      - `test_decisions_cover_rests_and_blind_match_check`. One row holds "match_check` v3",
        "no event type" and "supersede"; one row holds "Extraction set v4"; and the 2026-10-30
        match-check row begins its Decision cell with "(superseded in part by";
      - `test_evaluation_md_names_set_v4` replaces `test_evaluation_md_names_set_v3`. The
        procedure holds `evals/extraction/v4`, `--availability-keywords`,
        `other_match_absence` and "three test runs";
      - `test_roadmap_022_ticked`.

      Keep the existing single-row lookups green (see "Risks and traps").

      Automatic verification: `cd backend && uv run pytest -q tests/test_docs.py`, then Verify.

## Risks and traps

- **Docs tests that unpack exactly one row**
  (`tests/test_docs.py::test_decisions_cover_match_check_and_set_v2`). No new DECISIONS row may
  hold both `for_next_pl_match` and `adr/0010-`, both "fail-open" and "match check", or
  "Extraction evaluation set v2". Write "kept on a null answer", not "fail-open".
  `test_decisions_cover_available_and_absence_check` reads only the `| 2026-10-30` rows, and
  the prefix keeps "match check" and `` `doubt` `` in the row.
- **Tests that encode the old behaviour:** `test_match_check_input_from_fixture_data` (`Events:`
  lines) and `test_match_check_prompt_v2_rules` in step 1; `test_extraction_prompt_v6_rules`
  in step 2; `test_cli.py`'s `match_check@2` in step 1; `test_evaluation_md_names_set_v3` in
  step 10. Each is replaced in its step, never deleted without a replacement. The v3 tests in
  `test_results_files.py` and `test_eval_set_v3.py` read committed v3 data and stay as they
  are.
- **The prompt phrase tests constrain the dev iterations.** A dev edit may add to or reword
  other rules, but not the phrases the tests of steps 1 and 2 check. If a dev failure needs one
  of those phrases changed, that is a deviation that changes the SPEC's rule: escalate.
- **A blind check loses the type's hint.** A post whose only news is an injury picked up in a
  cup tie, for a club whose cup XI it also lists, now gets one answer without the `out` label.
  "Injuries dropped by the check" (at most 1 per run) measures this. A failure is an AC10 stop,
  not a reason to put the types back.
- **`DEFAULT_SET_DIR` and `test_linking.py`.** The switch to v4 waits for the reviewed set
  (020's deviations in steps 13 and 14).
- **Compose is production.** Only `snapshot-players`, `snapshot-fixtures` and `export-corpus`
  read it, and all three are read-only. No migration, and no `reextract` by the agent (AC12 is
  the owner's). The CLIs run as `env -u TWEET_SOURCE`.
- **Judge token budget.** About 15–30k tokens per input (`session_judge.sh`). The 1.5M cap in
  step 5 keeps the judge pass well under the session limit that 120 judges hit before.
- **The parallel lane 021** touches `docs/DECISIONS.md`, `docs/ROADMAP.md` and alerts. DECISIONS
  is append-only, so rows merge trivially. A conflict on `git merge origin/main` is an
  escalation trigger.
- **Run variance (#60).** That is why there are three runs and their mean. The report says each
  run's margin.
- **Order of the paid runs.** Dev iterations and the freeze come before the test runs. Test runs
  only on a committed tree (`git_dirty: false` in the header).

## End-to-end verification

### Automatic (performed by /pipeline:implement)

- Verify fully green:
  `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`.
- The three test runs of step 8: each prints `passes thresholds` and both absence-check lines,
  and `mean_of_runs` of the three final runs passes `threshold_flags`, with the raw-absence mean
  at most 1. The report records the results.
- `uv run python -m app.extraction.evaluation spend --results-dir evals/extraction/results/v4`:
  the total plus the `dev-iterations.md` spend lines is at most 5.00 USD, recorded in the report.
- `uv run pytest -q tests/extraction/test_flow.py -k match_check` on the test container (no
  Compose): the blind input and the call counts.
- Record the commands and their results in the Definition of Done line below.

### Manual (performed by the owner)

- After the merge, re-extract the current alert window on Compose and preview the next alert.
  Run `cd backend && uv run python -m app.extraction reextract --since <the last deadline> --until <now>`,
  then `uv run python -m app.alerts preview --at <now, Europe/Warsaw> --kind digest`.
  Pass when: the FFScout post 2116079251476611126 has no `out`/`doubt` event (in the preview,
  Evanilson, N.Jackson and Manzambi are not under "🔴 Nie zagra" because of it, and Manzambi has
  no "comeback" built on it). Their `available` news from GW9, if it still stands, shows as
  before.

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto` fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, `docs/EVALUATION.md`, `docs/DEVELOPMENT.md`
      updated
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

- 2026-11-01 — implement — `decision` — AC5 names 2116172084082602140 (johntownley11) and 2116226240323465435 (fplsebv), whose authors are not on the watched list and which sit in the corpus only as quoted sources; add them as hand-built cases, replace them, or drop them? Also: the owner `review` of set v4 (AC6, 41 unreviewed cases) at step 6, and the missing `backend/.env` (OpenRouter key) for step 7 — Replace them with the watched quoting posts (FPL__Raptor 2116174660693836212, FPL_Salah 2116188146660409531, FPLMattW 2116265028458315935) and update AC5's named cases accordingly. The owner cannot review set v4 now: do everything that does not depend on the review (the step 6 parts and any later work that can run on unreviewed labels without spending the paid test runs), then stop with ESCALATE for the review before the paid runs whose results depend on the reviewed labels. The owner copies `backend/.env` into the worktree themselves.

## Review log

Review of 2026-11-01 (plan-review, fresh context).

Findings:

- `major` — Design item 2 claimed every other v6 rule could stay word for word, but the v6
  relevance-rule bullet "A player who misses a national-team, cup or European match, or leaves
  the national-team camp injured, is `doubt` …" (`extraction.md`, lines 48–51) reads without a
  reason, so it tells the model that a cup rest is `doubt` and contradicts AC1's new rule (it may
  be what produced the FFScout `out`). Changed: design item 2 scopes that bullet to "through an
  injury or illness" (rest of the bullet unchanged), and step 2's test checks the scoped phrase.
- `major` — Design item 1's match-check rule "a post that names the next Premier League
  gameweek only to say the player was rested for it … false" is ambiguous: read literally it
  answers false for a player rested from the next PL match itself, a real absence the check
  would then drop. Changed: the rule now says "rested in the other match to be fresh for it
  ('rested for GW9' after a cup tie)" is false, and a player rested from or left out of the next
  PL match itself is true.
- `minor` — Design item 5 said `--x-id` is given for "the five named posts"; step 5's command
  (correctly) passes four, as 2116079251476611126 is a v3 case left out by `--exclude-cases`.
  Changed the design text to four.
- `minor` — Step 8 wrote its tests first but required the runs "on a clean committed tree"
  without saying the tests are committed before the runs. Changed: the step commits the red
  tests (`test: add the v4 test-run checks`) and then runs on that tree.

Checked and found correct:

- Coverage: AC1–AC11 each have steps and a proving test, and the matrix matches the steps; AC12
  is the owner's manual item with a `Pass when:` line. AC2's flow test is close to the existing
  `test_no_events_is_empty_result` but adds the fixtures path (`CHECK_INDEX`, `FIXTURES`), so it
  is not a duplicate.
- `_render_match_check` (`app/extraction/flow.py`) is the only place the type enters the check
  input; `match_check_node`'s selection is unchanged, so the existing call-count tests in
  `tests/extraction/test_flow.py` (lines 312–460) still prove AC4. Jones and Haaland are one
  club in `CHECK_INDEX` (2 calls in `test_out_and_starter_of_one_club_make_one_check`).
- Tests encoding the old versions are all named: `test_match_check_prompt_v2_rules`,
  `test_extraction_prompt_v6_rules`, `test_match_check_input_from_fixture_data`,
  `test_cli.py:1023` (`match_check@2`), `test_evaluation_md_names_set_v3`;
  `test_results_files.py:146` reads committed v3 data and stays.
- Metrics: `CaseResult.raw_predicted` holds every extracted event with `for_next_pl_match`
  (`runner.py`), so the raw-absence count is computable; `threshold_flags` takes the five values
  `mean_of_runs` gives; `default_output_dir` sends dev runs to the gitignored `results/v4/dev/`,
  so the spend log line is the right source for the cap test, and 020's whole spend was about
  0.18 USD (a v3 test run 0.058 USD), well under 5 USD.
- Set v4 tooling: `export-judge-inputs` has `--availability-keywords`, `--exclude-cases`,
  `--x-id` (required, bypassing the window), `--seed`; `import-judge` has `--select`, `--seed`,
  `--judge-model` and records `extraction@<current>` in `prelabels`; `review` sets
  `review_source: "owner"`. The v3 split rule (`x_id % 10 < 3` → dev) puts
  2116079251476611126 in test; removing the tag from dev case 2115797596119740910 leaves 13
  `other_match_absence` cases (minimum 8) and 12 in test (minimum 5). All five named posts were
  posted 2026-10-29 15:19Z to 2026-10-31 08:34Z, before the earliest GW9 kickoff
  (2026-10-31 12:30Z), so the corpus window holds them.
- DECISIONS (own search: "match check", "relevance rule", "set v3", "fail-open"): the plan
  breaks no row; the risk list covers the single-row unpacking in
  `test_decisions_cover_match_check_and_set_v2`, and the new rows are dated after 2026-10-30,
  so `test_decisions_cover_available_and_absence_check` is unaffected.
- Conventions: prompts stay in `app/content/prompts/` with a version bump and a test each; LLM
  steps tested against a fake model; paid runs outside pytest; CLIs run with
  `env -u TWEET_SOURCE`; Compose only read (snapshots, `export-corpus`).
- Feasibility and order: prompts (steps 1–2) precede the export (step 5, so `prelabels` names
  `extraction@7`); composition code (step 4) precedes the set; `DEFAULT_SET_DIR` switches only
  after the review (step 6); dev runs and the freeze precede the test runs.
- The owner's review in step 6 is a human judgment AC6 requires (`review_source: owner`), not a
  command the implementer could run, so it rightly stays an escalation point inside `## Steps`.
- Owner summary: no new dependency, no migration, consistent with SPEC → "Owner decisions";
  the manual scenario matches AC12. Language: the plan is in English (`language: en`).

The plan is ready: every AC is covered by steps with executable verification, the four findings
are fixed in place, and it adds no dependency or migration beyond what the owner accepted.

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

- `minor` — step 2: no `test_a_post_with_no_event_is_stored_with_no_events` added; `tests/extraction/test_service.py::test_success_first_attempt` already stores an `extracted` row for `ExtractionOutput(events=[])` and asserts `outcome.events == 0` (the plan allowed naming it instead of a duplicate).
- `minor` — step 5: `read_corpus` in `app/extraction/evaluation/judging.py` split the corpus file with `splitlines()`, which also splits on U+2029 inside a post text, so `export-judge-inputs` crashed on the GW9 corpus. It now splits on `"\n"` (test `test_read_corpus_keeps_a_paragraph_separator_inside_a_text`). A product defect fixed within the step.
- `minor` — step 5: no Compose `.env` copy was possible in the worktree (the auto-mode classifier refused copying credentials), so the read-only Compose commands ran with `DATABASE_URL` from the public `backend/.env.example` (local Compose credentials). The paid key for step 7 is still missing in the worktree (see the step 6 escalation).
- `minor` — step 5: the judge exported 98 inputs (about 598k tokens estimate, 548k used), none failed; 40 were imported (25 test, 15 dev). Unreviewed after the import, as the plan expects: `test_every_case_reviewed`, `test_new_and_changed_cases_reviewed_by_the_owner` and `test_composition_v4` (the judge pre-labels give `out`/`doubt` to four `other_match_absence` cases, a `cup_injury` case with no absence event, and Kroupi `available`; the owner settles these in `review`). `test_named_gw9_cases` also fails: 2116172084082602140 (johntownley11) and 2116226240323465435 (fplsebv) are in the corpus only as quoted sources by authors outside the watched list, so `export-judge-inputs` skipped them and the v3 composition rule (author on the watched list) would reject them. Escalated at step 6.

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_
