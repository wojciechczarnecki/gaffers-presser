# PLAN 020 — Positive availability and the match check for absences

## Owner summary

- **Approach:** Code first, then the evaluation data, then the paid runs. Extraction gets a
  fifth event type `available` (prompt v6). The existing match-check node now runs, still once
  per post and club, for `out`, `doubt` and line-up events (prompt `match_check` v2), and
  `kept_at_read` drops a false answer for all four types. `available` is never checked.
  Corroboration labels `available` by the table you approved. A small anchor rule makes an
  `available` claim the anchor only when it reverses older `out`/`doubt` news. Alerts get the
  group "✅ Wraca do gry", and breaking alerts skip an `available` post that reverses nothing.
  Set v3 is set v2 relabelled under prompt v6 by the session judge, plus new GW8–GW9 cases from
  a read-only Compose export. You review the changed and new cases, and then the default model
  alone runs dev and test. The corroboration judge prompt v2 gets three runs against three
  runs of v1 on set v2.
- **Main risks:**
  - The implementation stops once for your review of set v3 (step 15). It may stop again if a
    `claude -p` call or a read-only Compose read is refused, if the default model fails a
    threshold (AC19) or if the judge regression fails (AC12).
  - The match check gives one answer per post and club. A post that holds both a cup line-up
    and an injury for one club gets one answer. Prompt v2 starts with "an injury makes it
    true", and the "injuries dropped by the check" metric measures what this costs.
  - More match-check calls raise the cost a month. ADR 0010's 5 PLN threshold still applies.
- **New dependency:** no.
- **Data migration:** no. `event_type` is a plain string column, and `for_next_pl_match`
  already exists on every event (migration 0014). Both are confirmed in SPEC → "Owner
  decisions".
- **Manual scenarios for the owner:** 1. Re-extract the current alert window on Compose after
  the merge. Then check the next digest or preview for the three GW9 cases (AC21).

## Approach

### What the plan rests on

- `specs/020-availability-and-match-check/SPEC.md`: read in full, including
  `## Owner decisions`.
- `docs/CONVENTIONS.md`: read in full. The points this plan uses:
  - Polish texts go in `app/content/`.
  - LLM steps are tested against fakes, and evaluations run outside `pytest`.
  - Database tests run on a container.
  - Agents run CLIs near `backend/.env` as `env -u TWEET_SOURCE`.
  - Each green step gets a commit with only that step's files.
- `docs/DECISIONS.md`: searched for "event type", "match check", "for_next_pl_match",
  "anchor", "should be available" and "grouped". This found 2026-09-28 (four types; the
  relevance rule), 2026-09-29 ("should be available" gives no event), 2026-09-30 (the
  newest-claim anchor, the label table, and the rule that the anchor's own account never
  counts), 2026-10-02 (`anchor_x_id` and "included" posts), 2026-10-06 (groups by anchor type)
  and 2026-10-26 (the match check and fail-open). It also showed the docs tests that read
  these rows (see the traps).
- `docs/ROADMAP.md`: searched for "Stage 2", "018-" and "019-". The Stage 2 items for specs 018
  and 019 are the pattern for this spec's ticked item.
- `docs/BACKLOG.md`: searched for rows 24, 55, 58, 59 and 60, and for the highest number (60).
  The new P3 retrieval row is #61.
- `docs/PROJECT.md`: searched for "FR-1.3" (line 46: four types).
- `docs/EVALUATION.md`: read "The extraction procedure" in full. Searched "The retrieval
  procedure" for `export-corpus`, a read-only Compose export.
- `docs/reports/corroboration-eval-v2.md`: searched for "spend", "test split" and "cases".
  The test split has 75 cases, and the default judge is `google/gemini-3.1-flash-lite`.
- `specs/018-extraction-evaluation-v2/PLAN.md`: steps 10–15 read. They are the pattern for
  steps 14–18 here (build the set, the owner's review as an escalation, dev, test, report).
- The code read in full: `app/extraction/{schemas,lineups,flow,store,models}.py`,
  `app/extraction/evaluation/{cases,metrics,runner,dataset,judging,selection,cli}.py`,
  `app/corroboration/{rules,service,sources,schemas,judge}.py`,
  `app/alerts/{breaking,render,service}.py`, `app/content/alert_email.toml`,
  `app/content/retrieval_query_templates.toml`, `app/content/__init__.py`,
  `app/content/prompts/{extraction,match_check,corroboration_judge,extraction_prelabel}.md`,
  `scripts/session_judge.sh`.
- The code read in part: `app/alerts/players.py` (`_claims`), `app/alerts/store.py`
  (`included_origins`), `app/corroboration/evaluation/cases.py` (`EVENT_WORDS`,
  `LABEL_TABLE`, `render_case`), `app/corroboration/evaluation/cli.py` (`evaluate`),
  `app/corroboration/evaluation/building.py` (`_tier_of_retrieved`),
  `app/retrieval/evaluation/{cli,queries}.py` (`export-corpus`, `load_templates`),
  `app/extraction/fixtures.py`, `app/extraction/cli.py` (`reextract` options),
  `app/alerts/cli.py` (`preview` options).
- The tests that break or need extending, read in full or in the named part:
  `tests/extraction/test_lineups.py`, `tests/extraction/test_flow.py` (lines 300–440),
  `tests/content/{test_prompts,test_corroboration_prompt,test_alert_email}.py`,
  `tests/retrieval/evaluation/test_templates.py`,
  `tests/extraction/evaluation/{test_eval_set,test_eval_set_v2,test_results_files}.py`,
  `tests/corroboration/evaluation/test_results_files.py`, `tests/test_docs.py`
  (lines 260–330), and `tests/corroboration/test_rules.py` (lines 1–60). The test names come
  from a grep of `tests/alerts/`, `tests/corroboration/test_service.py` and
  `tests/extraction/test_current_extractions.py`.

### Design

1. **The type.** `EventType` in `app/extraction/schemas.py` gets `"available"`. Every place
   that derives the types from it follows: `EVENT_TYPES` in the evaluation, the retrieval
   query-template test, and the judge reply format. The places that list the types by hand
   are changed by hand: the alert TOML, `GROUP_ORDER`, the corroboration evaluation's
   `EVENT_WORDS`/`LABEL_TABLE`, `session_judge.sh`, and the `REPLY_FORMAT` in `judging.py`.
   The v1 and v2 composition rules keep the four types. `composition_problems` gets an
   `event_types` parameter, which defaults to a new `LEGACY_EVENT_TYPES` (the four), so the
   frozen sets stay valid.
2. **Read-time rule.** `app/extraction/lineups.py` gets `MATCH_CHECKED_EVENTS = ("out",
   "doubt", "benched", "confirmed_starter")`. `kept_at_read` drops an event of those types
   when `for_next_pl_match is False`. It keeps `available` whatever the answer. The 60 h filter
   (`is_stale_lineup`) stays line-ups only, and that already satisfies AC4. The parameter name
   `fresh_lineups_only` of `current_extractions` stays the same, because renaming it touches
   five readers for no behaviour change. It is a backlog proposal instead.
3. **The match check.** In `match_check_node` the filter becomes `MATCH_CHECKED_EVENTS`, so
   there is still one call per club, now over `out`/`doubt`/line-up events. The human message
   gets one more block, after the fixtures: `Events:` with one line `- <mention>: <type>` per
   event of that club. This tells the model what the question is about. `match_check.md` v2
   asks "does this news concern the club's next Premier League match?".
   - false: a rest or rotation for a cup, European or national-team match; left out of such
     a squad with no injury given; a suspension served in another competition.
   - true: an injury or illness from any match; an absence with no other match in sight.
   - The v1 line-up rules stay.
   - When a post's events for the club mix an injury with an other-match line-up, it answers
     true. Losing an injury costs more than keeping a cup line-up, and the 60 h filter still
     catches a confirmed cup line-up.
   - Considered: one call per event. Rejected: the owner decided one call per post and club.
4. **Prompt v6.** `extraction.md`:
   - adds `available` to "Event types": fit or available for the next PL match, with no claim
     about starting;
   - replaces the two "no event" rules ("should be available" and "returning from injury …
     there is no positive availability type") with `available` rules, and adds "is NOT
     suspended" / "the ban is served elsewhere" → `available`;
   - keeps the fatigue, fake-leak, own-team and national-team rules;
   - adds a sentence that a squad rest for a cup or European tie is still extracted as the
     post says it (`out`/`doubt`), because the match check judges the match. This keeps
     extraction and the match check separate (ADR 0010 B).

   `extraction_prelabel.md` v2 adds the two new flags (item 9).
5. **Label table.** `label_claim`: the same type → `supports`; `available` with `out`/`doubt`
   → `contradicts`; `available` with `benched`/`confirmed_starter` → `related`; the old pairs
   are unchanged. This is done with an explicit check before the existing branch.
6. **Anchor rule (AC10).** A new pure function in `app/corroboration/rules.py`:
   `reverses(candidate: Claim, claims: Sequence[Claim], judged: Sequence[LabelledPost]) -> bool`.
   It is true when an older (by `(created_at, x_id)`) claim of type `out`/`doubt` exists, or an
   older judged post labelled `contradicts`. Every account counts, so `count_accounts` is not
   used here. `corroborate` in `app/corroboration/service.py`:
   1. The candidate is the forced claim (`anchor_x_id`), or else the newest claim, as today.
   2. If its type is not `available`, nothing changes.
   3. If it is `available` and a SQL claim reverses, it is the anchor.
   4. Otherwise, with the judge configured, the judge runs with the candidate as anchor. If a
      judged post reverses, the candidate is the anchor and the judged list is reused.
   5. Otherwise the anchor is the newest claim of another type. The judge runs again against
      it, and the `available` claim is labelled against it by the table.
   6. With no claim of another type, the result has `anchor=None` and
      `RetrievalReport("skipped", NO_REVERSAL_REASON)`, so there is no card.

   Considered: judging only the SQL side. Rejected: AC10 counts judged contradictions. The
   second judge pass happens only for players whose newest claim is a non-reversing
   `available`.
7. **Alerts.**
   - `GROUP_ORDER = ("out", "doubt", "benched", "available", "confirmed_starter")`.
   - In `alert_email.toml`: `group.available = "✅ Wraca do gry"`,
     `summary.available = "✅ wraca"`, `event.available = "Gotowy do gry"`, and
     `[palette.available]` (accent `#2e90fa`, background `#eff8ff`, heading `#175cd3`).
   - The header counter is the existing `"{label} {count}"`, so it reads "✅ wraca N".
   - `breaking.py`: `_candidates` also returns, per named player, the trigger row's event type.
     After `corroborate(..., anchor_x_id=x_id)`, a player whose trigger event is `available`
     and whose `result.anchor` is not the trigger post is left out. If no player is left, the
     post is skipped. That means no send, no `record_alert`, and no addition to `included`, so
     a later digest can still cite it.
8. **Evaluation metrics (AC17).** `CaseResult` gets `other_match_absence: bool` (from the tag).
   `Metrics` gets:
   - `available_f1`: F1 over the `available` events only, from the same `_match_pairs`;
   - `other_match_absence_cases` and `other_match_absence_false_alarms`: tagged cases, no
     error, with a kept `out`/`doubt` event predicted;
   - `injuries_dropped_by_check`: expected `out`/`doubt` events whose raw match has
     `for_next_pl_match is False`.

   The ADR 0010 thresholds (`threshold_flags`) stay as they are, so `choose_models` over the
   v2 files is unchanged. The two AC17 limits are separate constants,
   `MAX_OTHER_MATCH_ABSENCE_FALSE_ALARMS = 1` and `MAX_INJURIES_DROPPED = 1`, checked by
   `absence_checks(metrics) -> dict[str, bool]` and printed by `evaluate`.
9. **Set v3 tooling (AC16).**
   - `Tag` gets `other_match_absence` and `cup_injury`.
   - `PrelabelVerdict` gets two booleans with default `False` (`other_match_absence`,
     `cup_injury`), and `import_verdicts` maps the three flags to tags. "Unchanged" now
     compares all three flag tags.
   - `session_judge.sh` (schema `extraction`) asks for and validates the four keys and the
     five types.
   - `export-judge-inputs` gets `--availability-keywords`, which also keeps posts matching an
     availability regex (`fit|fitness|trained|training|available|suspen|ban(ned)?|rest(ed)?|rotat|squad|injur|knock|miss(es)?`).
     The sampling of the rest is unchanged.
   - `_tier`/`DEFAULT_QUOTAS` get the tiers `absence` (either new flag) and `available` (an
     `available` event), ranked after `other`.
   - `composition_problems_v3(cases, snapshot, manifest, v2_ids)`:
     - the base rules with the five types (so at least 5 `available` events in test);
     - every v2 case is present;
     - `other_match_absence`: at least 8 cases, at least 5 in test, and none with an expected
       `out`/`doubt` event;
     - `cup_injury`: at least 3 cases, each with an expected `out`/`doubt` event;
     - `other_match_lineup` cases have no expected events;
     - `manifest.backlog_cases["24"]` is non-empty and every id in it is in the set;
     - a new real case follows the split rule, and its author is on the watched list.
   - `DEFAULT_SET_DIR` becomes `v3`, so `default_output_dir` gives `results/v3`.

   Considered: tagging by hand during the owner's review. Rejected: a composition failure after
   the review would then need a second review pass (the same reasoning as spec 018).
10. **Judge prompt v2.** `corroboration_judge.md` adds `available` to "Event types of the
    anchor" and one rule per label for an `available` anchor:
    - supports: fit or available;
    - contradicts: out or doubtful;
    - related: a line-up post (starts or benched).

    The text for the four existing anchors stays word for word, so the regression on set v2
    measures only the addition.
11. **Paid runs.** The corroboration v1 runs happen before the prompt edit, on a clean
    committed tree, because `PROMPT_VERSION` is read from the file. The extraction runs use
    the default model `openai/gpt-6-luna` only. Budget gates: extraction dev spend reaching
    3 USD → escalate; the total over everything reaching 4.5 USD before the last run →
    escalate.

### Patterns reused

- The fake chat model and the `_checked_flow` helper in `tests/extraction/test_flow.py`.
- `tests/corroboration/helpers.py` (`seed_reference`, `add_claim`, `add_extraction`) and the
  test `test_a_stale_lineup_is_neither_a_claim_nor_judged` in
  `tests/corroboration/test_service.py`.
- `tests/alerts/helpers.py` (`report`, `corroboration`, `listed`, `alerts_runtime`) and
  `tests/alerts/test_breaking.py::test_breaking_anchor_is_the_post_sql_only`.
- The set-building and review flow of spec 018 (`export-judge-inputs`, `session_judge.sh`,
  `import-judge`, `compare-labels`, `review`, `evaluate`, `spend`).

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 1, 3, 17 | `tests/extraction/test_service.py::test_an_available_event_is_stored_and_read_back`; `tests/extraction/test_flow.py::test_available_event_passes_through_without_a_match_check`; set v3 `available_f1` in the test run (step 17) |
| AC2 | 3 | `tests/content/test_prompts.py::test_extraction_prompt_v6_rules`, `::test_extraction_prompt_states_relevance_rule` (with `available`) |
| AC3 | 1, 4, 9, 13 | `tests/extraction/test_schemas.py::test_unknown_event_type_fails_validation`, `::test_available_is_a_valid_event_type`; `tests/retrieval/evaluation/test_templates.py::test_every_event_type_has_an_en_and_a_pl_template_with_the_player`, `::test_available_templates`; `tests/corroboration/evaluation/test_cases.py::test_event_words_and_label_table_cover_every_type`; `tests/content/test_alert_email.py::test_template_file_is_valid_toml_with_every_vocabulary`; `tests/extraction/evaluation/test_judging.py::test_reply_format_lists_five_types` |
| AC4 | 1 | `tests/extraction/test_lineups.py::test_an_available_event_70_hours_before_kickoff_is_kept` |
| AC5 | 2 | `tests/extraction/test_flow.py::test_out_and_starter_of_one_club_make_one_check`, `::test_no_match_check_for_available_only`, `::test_match_check_answer_on_that_clubs_checked_events_only` |
| AC6 | 2, 16, 17 | `tests/content/test_prompts.py::test_match_check_prompt_v2_rules`; the test run's `other_match_absence_false_alarms` (step 17, `tests/extraction/evaluation/test_results_files.py::test_v3_test_run_passes`) |
| AC7 | 1, 2 | `tests/extraction/test_current_extractions.py::test_fresh_lineups_only_drops_an_absence_the_check_answered_false`; `tests/corroboration/test_service.py::test_an_absence_for_another_match_is_neither_a_claim_nor_judged`; `tests/extraction/evaluation/test_runner.py::test_an_out_event_answered_false_is_not_predicted`; `tests/alerts/test_breaking.py::test_an_absence_for_another_match_never_breaks` |
| AC8 | 1 | `tests/extraction/test_lineups.py::test_kept_at_read_by_type_and_answer` (5 types × null/true/false) |
| AC9 | 4 | `tests/corroboration/test_rules.py::test_label_table` (25 pairs) |
| AC10 | 5 | `tests/corroboration/test_service.py::test_available_anchor_when_it_reverses_an_older_out`, `::test_available_anchor_when_a_judged_older_post_contradicts`, `::test_non_reversing_available_falls_back_to_an_older_starter`, `::test_only_available_claims_give_no_anchor`, `::test_same_account_out_then_fit_reverses`; `tests/corroboration/test_rules.py::test_reverses` |
| AC11 | 7 | `tests/content/test_corroboration_prompt.py::test_judge_prompt_v2_has_five_anchor_types` |
| AC12 | 6, 8 | `tests/corroboration/evaluation/test_results_files.py::test_judge_v2_regression_within_tolerance` |
| AC13 | 9 | `tests/alerts/test_render.py::test_available_group_comes_before_zagra_with_its_counter`, `::test_available_card_html_has_its_palette`; `tests/content/test_alert_email.py::test_template_has_every_key_and_renders` |
| AC14 | 10 | `tests/alerts/test_end_to_end.py::test_palmer_shaped_reversal_is_a_wraca_do_gry_card` |
| AC15 | 11 | `tests/alerts/test_breaking.py::test_reversing_available_post_breaks`, `::test_non_reversing_available_post_sends_nothing_and_stays_new` |
| AC16 | 13, 14, 15 | `tests/extraction/evaluation/test_cases.py::test_composition_v3_*`; `tests/extraction/evaluation/test_eval_set_v3.py` (whole file) |
| AC17 | 12, 17 | `tests/extraction/evaluation/test_metrics.py::test_available_f1`, `::test_other_match_absence_false_alarms`, `::test_injuries_dropped_by_check`; `tests/extraction/evaluation/test_results_files.py::test_v3_test_run_passes` |
| AC18 | 16, 17, 18 | `tests/extraction/evaluation/test_results_files.py::test_v3_spend_within_cap` |
| AC19 | 18 | `tests/test_docs.py::test_extraction_report_v3_has_every_section`; `tests/extraction/evaluation/test_results_files.py::test_report_v3_names_the_test_run` |
| AC20 | 19 | `tests/test_docs.py::test_project_fr_1_3_lists_five_types`, `::test_decisions_cover_available_and_absence_check`, `::test_evaluation_md_names_set_v3`, `::test_backlog_24_removed_and_61_added`, `::test_roadmap_020_ticked` |
| AC21 | — | manual (the owner, after the merge; see "Manual") |

## Steps

Verify = `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`.
Commands run from `backend/` unless shown otherwise. Each step writes its tests first and
sees them fail, then makes the change. `$S` = a `020/` directory in the implementer's
session scratchpad (never in the repository).

- [x] 1. **The `available` type and the read-time rule (AC3 schema and templates, AC4, AC7, `iterations: 1`
      AC8).** Files:
      - `app/extraction/schemas.py` (`EventType` + `"available"`);
      - `app/extraction/lineups.py` (`MATCH_CHECKED_EVENTS`, `kept_at_read`);
      - `app/extraction/evaluation/cases.py`: `LEGACY_EVENT_TYPES`, and `composition_problems`
        gets `event_types: Sequence[str] = LEGACY_EVENT_TYPES`. `EVENT_TYPES` stays
        `get_args(EventType)`;
      - `app/content/retrieval_query_templates.toml`: `available = "{player} fit"` in `en`,
        `available = "{player} gotowy do gry"` in `pl`.

      Tests first:
      - `tests/extraction/test_lineups.py`:
        - `test_kept_at_read_by_type_and_answer`, parametrised over the five types × `None`,
          `True` and `False`, with a 10 h lead. The four checked types are dropped only on
          `False`, and `available` is always kept. It replaces the out/doubt rows of the
          existing parametrisation, which now contradict the rule
          (`("out", "confirmed", 10, False, True)` becomes `False`);
        - `test_an_available_event_70_hours_before_kickoff_is_kept` (`confirmed`, all three
          answers).
      - `tests/extraction/test_schemas.py` (new): `test_available_is_a_valid_event_type`,
        `test_unknown_event_type_fails_validation` (`ExtractedEvent(event_type="injured")`
        raises `ValidationError`).
      - `tests/extraction/test_current_extractions.py::test_fresh_lineups_only_drops_an_absence_the_check_answered_false`:
        `out` false is dropped; `doubt` null and `out` true are kept, and the unfiltered read
        returns all three.
      - `tests/extraction/evaluation/test_runner.py::test_an_out_event_answered_false_is_not_predicted`
        (raw keeps it with `kept=False`).
      - `tests/retrieval/evaluation/test_templates.py::test_available_templates` (the two
        exact strings). The existing set-equality test then covers five types.
      - `tests/corroboration/test_service.py::test_an_absence_for_another_match_is_neither_a_claim_nor_judged`
        (shaped like `test_a_stale_lineup_is_neither_a_claim_nor_judged`: an `out` stored with
        `for_next_pl_match=False` is not a claim, and its post is in `claimed_post_ids`) and
        `tests/alerts/test_breaking.py::test_an_absence_for_another_match_never_breaks`. Both
        prove this step's `kept_at_read` change, so they are written and seen red here.

      Automatic verification:
      `uv run pytest -q tests/extraction tests/retrieval/evaluation/test_templates.py tests/corroboration/test_service.py tests/alerts/test_breaking.py -n auto`
      (the v1 and v2 composition tests stay green through `LEGACY_EVENT_TYPES`), then Verify.

- [x] 2. **The match check for absences, prompt `match_check` v2 (AC5, AC6 text).** Files: `iterations: 0`
      - `app/extraction/flow.py`: the filter becomes `MATCH_CHECKED_EVENTS`;
        `_render_match_check` gets an `Events:` block with one `- <mention>: <event_type>`
        line per event of that club;
      - `app/content/prompts/match_check.md` (version 2, design item 3).

      Tests first, in `tests/extraction/test_flow.py`:
      - `test_out_and_starter_of_one_club_make_one_check`: Haaland `out` and Haaland's
        teammate `confirmed_starter` give 2 LLM calls in total, and both get the answer;
      - `test_no_match_check_for_available_only` replaces
        `test_no_match_check_without_lineup_events`: an `available` event gets no call and
        `for_next_pl_match` stays `None`. A second case with `out` events now makes a call;
      - `test_match_check_answer_on_that_clubs_checked_events_only` replaces
        `test_match_check_answer_on_that_clubs_lineup_events_only`. Jones `out` now gets its
        club's answer, and an added `available` event stays `None`;
      - `test_match_check_input_from_fixture_data` also asserts `"Events:"` and
        `"- Haaland: benched"`.

      `tests/content/test_prompts.py::test_match_check_prompt_v2_rules` checks version 2 and
      the phrases "rest", "rotat", "suspension", "injury", "illness", "cup", "european" and
      "national". Update `tests/extraction/evaluation/test_cli.py` line 1000 to
      `"match_check@2"`. (The AC7 reader tests moved to step 1, which makes the read-time
      change.)

      Automatic verification:
      `uv run pytest -q tests/extraction/test_flow.py tests/content tests/corroboration/test_service.py tests/alerts/test_breaking.py tests/extraction/evaluation/test_cli.py`,
      then Verify.

- [x] 3. **Extraction prompt v6 (AC1, AC2).** Files: `app/content/prompts/extraction.md` `iterations: 0`
      (version 6, design item 4).

      Tests first:
      - `tests/content/test_prompts.py::test_extraction_prompt_v6_rules` replaces
        `test_extraction_prompt_v5_rules`. It checks version 6, the phrases kept from v5
        ("own team", "hypothetical", "national team", "flag", "never `confirmed`"), and
        "`available`", "trained", "not suspended", "fatigue" and "fake". It also checks that
        "there is no positive availability type" is gone;
      - `"available"` is added to the parametrised keywords of
        `test_extraction_prompt_states_relevance_rule`;
      - `tests/extraction/test_service.py::test_an_available_event_is_stored_and_read_back`
        (db): a fake model returns `available`/`confirmed` for a linked player. The service
        stores it, and `current_extractions(..., fresh_lineups_only=True)` returns it with
        `for_next_pl_match` `None`;
      - `tests/extraction/test_flow.py::test_available_event_passes_through_without_a_match_check`.

      Automatic verification:
      `uv run pytest -q tests/content tests/extraction/test_service.py tests/extraction/test_flow.py`,
      then Verify.

- [x] 4. **The label table and the evaluation's type texts (AC9, AC3).** Files: `iterations: 0`
      - `app/corroboration/rules.py` (`label_claim`);
      - `app/corroboration/evaluation/cases.py`: `EVENT_WORDS["available"] = "FIT or
        AVAILABLE (no claim about starting)"`, and the `LABEL_TABLE["available"]` rows
        `("fit / available", "supports")`, `("out / doubt", "contradicts")` and
        `("starts / benched", "related")`.

      Tests first:
      - `tests/corroboration/test_rules.py`: `TYPES` gets `available`, and
        `test_label_table` asserts the explicit 25-pair expectation;
      - `tests/corroboration/evaluation/test_cases.py::test_event_words_and_label_table_cover_every_type`
        (keys equal `get_args(EventType)`).

      Automatic verification: `uv run pytest -q tests/corroboration`, then Verify.

- [x] 5. **The anchor rule (AC10).** Files: `app/corroboration/rules.py` (`reverses`, `iterations: 1`
      `REVERSED_TYPES = ("out", "doubt")`) and `app/corroboration/service.py` (design item 6,
      `NO_REVERSAL_REASON`). Factor the existing labelling and judging block of `corroborate`
      into a helper that takes the anchor, so it can run twice.

      Tests first:
      - `tests/corroboration/test_rules.py::test_reverses`: an older `out` → true; an older
        `doubt` by the same account → true; an older judged `contradicts` → true; only a newer
        `out` → false; an older `confirmed_starter` → false;
      - `tests/corroboration/test_service.py` (db, judge faked with `tests/corroboration/fakes.py`):
        - `test_available_anchor_when_it_reverses_an_older_out`;
        - `test_available_anchor_when_a_judged_older_post_contradicts`: the judge is called
          with the `available` claim as anchor;
        - `test_non_reversing_available_falls_back_to_an_older_starter`: the anchor is the
          `confirmed_starter` claim, the `available` post is in `related`, and the judge's
          second pass is against the starter;
        - `test_only_available_claims_give_no_anchor` (`anchor is None`, the reason, no
          grade);
        - `test_same_account_out_then_fit_reverses` (one reporter's `out` then `available`:
          the anchor is the `available` post);
        - `test_given_available_anchor_that_does_not_reverse_is_replaced` (`anchor_x_id`
          path).

      Automatic verification: `uv run pytest -q tests/corroboration tests/alerts -n auto`,
      then Verify.

- [x] 6. **The corroboration regression baseline: three runs of judge prompt v1 (AC12 `iterations: 0`
      part).** Paid, run before step 7. The tree must be clean and committed, because the
      header records `git_dirty`. Run three times, `N` = 1, 2, 3:
      `env -u TWEET_SOURCE uv run python -m app.corroboration.evaluation evaluate --cases evals/corroboration/v2/cases.jsonl --split test --model google/gemini-3.1-flash-lite --output evals/corroboration/results/regression/test-judge-v1-rN.json`.
      An errored case in a run → repeat that run once with `--force`. Record each run's
      accuracy, false-support rate and `cost_usd` in
      `specs/020-availability-and-match-check/dev-iterations.md` (new file, section
      "Corroboration regression"). Keep one line `Corroboration regression spend: <x> USD`
      there, which holds the sum of the runs so far (step 17's spend test reads it). Commit the three files and the log
      (`data: add corroboration judge v1 regression runs`).

      Automatic verification:
      `python -c "import json,glob; fs=sorted(glob.glob('evals/corroboration/results/regression/test-judge-v1-r*.json')); assert len(fs)==3 and all(json.load(open(f))['prompt_version']=='corroboration_judge@1' for f in fs)"`,
      then Verify.

- [x] 7. **Judge prompt `corroboration_judge` v2 (AC11).** Files: `iterations: 0`
      `app/content/prompts/corroboration_judge.md` (version 2, design item 10).

      Tests first:
      - `tests/content/test_corroboration_prompt.py::test_judge_prompt_v2_has_five_anchor_types`
        (version 2, the five types in backticks, and an `available` anchor line naming
        `contradicts` and `related`);
      - `tests/corroboration/evaluation/test_results_files.py::test_v2_test_runs_share_one_prompt`
        now compares with the literal `{"corroboration_judge@1"}`, because the committed v2
        runs were made with v1 and `PROMPT_VERSION` moves to `@2`.

      Automatic verification:
      `uv run pytest -q tests/content tests/corroboration -n auto`, then Verify.

- [x] 8. **The regression: three runs of v2 and the check (AC12).** Paid, on a clean committed `iterations: 0`
      tree. Run the step 6 command with output `test-judge-v2-rN.json`, `N` = 1, 2, 3.

      Test first:
      `tests/corroboration/evaluation/test_results_files.py::test_judge_v2_regression_within_tolerance`.
      It reads three `test-judge-v1-r*.json` (prompt `@1`) and three `test-judge-v2-r*.json`
      (prompt `@2`), all on set v2's test split with one model. It asserts
      `mean(v2 accuracy) >= mean(v1 accuracy) - 0.05` and
      `mean(v2 false_support.rate) <= mean(v1 false_support.rate) + 0.05`.

      Record the runs in `dev-iterations.md` and update its `Corroboration regression spend`
      line. If the test fails, do not edit the prompt for a
      second try without a decision: escalate (`decision`) with both means and the cases that
      changed label. Commit (`data: add corroboration judge v2 regression runs`).

      Automatic verification:
      `uv run pytest -q tests/corroboration/evaluation/test_results_files.py`, then Verify.

- [x] 9. **The alert group "✅ Wraca do gry" (AC13, AC3 alert texts).** Files: `iterations: 0`
      `app/alerts/render.py` (`GROUP_ORDER`) and `app/content/alert_email.toml` (design
      item 7; update the "four groups" comment).

      Tests first:
      - `tests/content/test_alert_email.py`: `EVENT_TYPES` gets `available`, so the vocabulary
        and every-key tests cover it;
      - `tests/alerts/test_render.py::test_available_group_comes_before_zagra_with_its_counter`:
        the plain text has the headings in the order out, doubt, benched, "✅ Wraca do gry",
        "🟢 Zagra", the summary line has "✅ wraca 1", and the card line ends "— Gotowy do
        gry";
      - `::test_available_card_html_has_its_palette` (the accent `#2e90fa` and the heading
        colour in the HTML part);
      - extend `test_groups_follow_the_event_order_with_a_summary` with an `available`
        report.

      Automatic verification: `uv run pytest -q tests/alerts tests/content`, then Verify.

- [x] 10. **A Palmer-shaped reversal card (AC14).** Test only; no product change is expected. `iterations: 0`
      If one is needed, it is a deviation.

      `tests/alerts/test_end_to_end.py::test_palmer_shaped_reversal_is_a_wraca_do_gry_card`
      (db, SQL-only runtime). The setup is a listed player with two older `out` claims from two
      accounts and a newer `available`/`confirmed` claim from a third account. A digest built
      through `build_slot_alert` puts him under "✅ Wraca do gry". "Zaprzecza" lists the two
      `out` accounts, the reversal note is present, and the grade is what `grade` gives for
      these inputs with no special case. That is `medium`: `confirmed` gives high, and it is
      lowered one step because the contradicting accounts are new since the window start. The
      test computes the expected level by calling `grade` itself.

      Automatic verification:
      `uv run pytest -q tests/alerts/test_end_to_end.py`, then Verify.

- [x] 11. **Breaking alerts for a reversing `available` post only (AC15).** Files: `iterations: 0`
      `app/alerts/breaking.py` (design item 7).

      Tests first, in `tests/alerts/test_breaking.py`:
      - `test_reversing_available_post_breaks`: an older `out` and then a new `available`
        post send one breaking e-mail with the player under "✅ Wraca do gry";
      - `test_non_reversing_available_post_sends_nothing_and_stays_new`: an older
        `confirmed_starter` and then a new `available` post. `run_breaking` returns 0, no
        alert row exists for its key, and `included_origins` does not hold it. A following
        digest (`build_slot_alert`, kind `digest`) shows the post as a `related` source of
        the starter card;
      - `test_mixed_post_drops_only_the_non_reversing_player`: one post with Y `out` and X
        `available` (nothing to reverse) breaks for Y only.

      Automatic verification: `uv run pytest -q tests/alerts -n auto`, then Verify.

- [x] 12. **Evaluation metrics for the new type and the absences (AC17 code).** Files: `iterations: 0`
      - `app/extraction/evaluation/cases.py`: `Tag` gets `other_match_absence` and
        `cup_injury` here (moved from step 13 by the plan review: `Tag` is a `Literal`, so
        this step's tests cannot build a case with the new tag without it);
      - `app/extraction/evaluation/metrics.py` (design item 8);
      - `app/extraction/evaluation/runner.py`: `other_match_absence` comes from the tag, on
        both the error and success paths;
      - `app/extraction/evaluation/cli.py` (`evaluate` prints `available F1`,
        `other match absence false alarms: k of n`, `injuries dropped by the check`, and one
        yes/no line per absence check).

      Tests first:
      - `tests/extraction/evaluation/test_metrics.py`:
        - `test_available_f1` (one hit, one miss, one spurious → 0.5);
        - `test_other_match_absence_false_alarms`: a kept `doubt` counts, a dropped one does
          not, and an errored case is not counted;
        - `test_injuries_dropped_by_check`;
        - `test_absence_checks_limits` (1 passes, 2 fails);
      - `tests/extraction/evaluation/test_runner.py::test_other_match_absence_tag_reaches_the_result`;
      - `tests/extraction/evaluation/test_cli.py::test_evaluate_prints_the_absence_metrics`.

      Automatic verification:
      `uv run pytest -q tests/extraction/evaluation -n auto`, then Verify.

- [x] 13. **Set v3 tooling (AC16 code).** Files: `iterations: 0`
      - `app/extraction/evaluation/cases.py` (`composition_problems_v3`, the constants
        `MIN_OTHER_MATCH_ABSENCE = 8`, `MIN_OTHER_MATCH_ABSENCE_IN_TEST = 5`,
        `MIN_CUP_INJURY = 3`, `BACKLOG_24 = "24"`);
      - `app/extraction/evaluation/judging.py` (the verdict fields, the tag mapping,
        `REPLY_FORMAT`, `AVAILABILITY_KEYWORDS`, `_tier`, `DEFAULT_QUOTAS` with `absence=15`
        and `available=15`, `parse_quotas` naming the new tiers);
      - `app/extraction/evaluation/cli.py` (`--availability-keywords`);
      - `app/extraction/evaluation/dataset.py` (`DEFAULT_SET_DIR = EVALS_DIR / "v3"`);
      - `app/content/prompts/extraction_prelabel.md` (version 2: the two flags, defined as
        in design item 9 and AC16);
      - `scripts/session_judge.sh` (the `extraction` schema's format and validation).

      Tests first:
      - `tests/extraction/evaluation/test_cases.py`: `test_composition_v3_accepts_a_valid_set`,
        `test_composition_v3_needs_available_events_in_test`,
        `test_composition_v3_other_match_absence_counts_and_no_absence_event`,
        `test_composition_v3_cup_injury_needs_an_absence_event`,
        `test_composition_v3_backlog_24_cases_present`,
        `test_composition_v3_every_v2_case_present`,
        `test_legacy_composition_keeps_four_types`;
      - `tests/extraction/evaluation/test_judging.py`: `test_reply_format_lists_five_types`,
        `test_flags_become_tags`, `test_unchanged_case_needs_all_three_flag_tags_equal`,
        `test_old_verdict_without_new_flags_imports`,
        `test_availability_keywords_keep_fitness_posts`, `test_absence_and_available_tiers`;
      - `tests/extraction/evaluation/test_cli.py::test_export_with_availability_keywords`.

      Check the script with
      `bash -n ../scripts/session_judge.sh && grep -c '"available"' ../scripts/session_judge.sh`
      (at least 2: the format and the validation).

      Automatic verification:
      `uv run pytest -q tests/extraction/evaluation -n auto`, the script check above, then
      Verify.

- [x] 14. **Build set v3 with session pre-labels (AC16 data).** AC16 asks for GW8–GW9 posts, `iterations: 0`
      and the only source is Compose. The owner-approved SPEC (AC16, the "Context" posts) is
      the request for these reads; they are the only Compose reads of this plan: `export-corpus`,
      `snapshot-players` and `snapshot-fixtures`, read-only, and no command here writes to
      Compose. Prefix each command with `env -u TWEET_SOURCE`.
      - Write `tests/extraction/evaluation/test_eval_set_v3.py` first:
        - `test_schema` (version `v3`, a manifest with `prelabels`);
        - `test_every_v2_case_in_v3`;
        - `test_every_case_reviewed`;
        - `test_composition_v3`;
        - `test_backlog_24_cases_listed` (non-empty, every id present);
        - `test_fixtures_snapshot_covers_every_case`.

        It is red until step 15.
      - Snapshots: `uv run python -m app.extraction.evaluation snapshot-players --set evals/extraction/v3`
        and `... snapshot-fixtures --set evals/extraction/v3`.
      - The GW8–GW9 corpus: `uv run python -m app.retrieval.evaluation export-corpus --set $S/compose --since 2026-10-17T12:00 --until <now, Europe/Warsaw> --version v3-scratch`.
      - The named posts: find the "Context" posts in `$S/compose/corpus.jsonl` by a text
        search: the Wednesday "Evanilson not in the squad" posts, FFScout's "rested for GW9",
        the Palmer press-conference post, "Palmer is NOT suspended", "won't be banned for
        GW9", N.Williams "He is OK… trained the entire week", and the FFScout/FPLFocal
        "trained all week" posts. Write `evals/extraction/v3/set.json` with `version: "v3"`,
        `list_handles` from `evals/extraction/v2/set.json`, and `backlog_cases: {"24": [ids]}`.
      - Relabel v2:
        `uv run python -m app.extraction.evaluation export-judge-inputs --set evals/extraction/v3 --cases evals/extraction/v2/cases.jsonl --out $S/judge-v2`.
      - New candidates:
        `uv run python -m app.extraction.evaluation export-judge-inputs --set evals/extraction/v3 --corpus $S/compose/corpus.jsonl --keywords --availability-keywords --rest-sample 30 --seed 20 --exclude-cases evals/extraction/v2/cases.jsonl --x-id <each named id> --out $S/judge-new`.
      - Run `estimate --export` on both exports. If the sum exceeds 3M tokens, escalate
        (`decision`) before judging. Judge with
        `../scripts/session_judge.sh $S/judge-v2 6 sonnet extraction medium` (and the same for
        `judge-new`). Retry failures with `opus`, named in the manifest.
      - Import: `import-judge --export $S/judge-v2 --set evals/extraction/v3 --judge-model "claude-sonnet-5-5 (Claude Code session)"`,
        then `import-judge --export $S/judge-new ... --select 70 --seed 20`.
      - Run `compare-labels --set evals/extraction/v3 --baseline evals/extraction/v2/cases.jsonl`
        and the composition rules except `reviewed`. If a rule fails on the candidates, draw
        once more with `--seed 21` (or a larger `--rest-sample`), judge it and import it.
        After two extra draws, escalate (`decision`).
      - Commit the set unreviewed (`data: build extraction set v3 candidates`). A refused
        `claude -p` or Compose read → escalate (`permission`).

      Automatic verification:
      `uv run pytest -q tests/extraction/evaluation/test_eval_set_v3.py -k "schema or every_v2 or backlog or fixtures"`
      green, and `-k composition` failing only on unreviewed cases, recorded in
      `## Deviations`. Then Verify with
      `--deselect tests/extraction/evaluation/test_eval_set_v3.py::test_every_case_reviewed --deselect tests/extraction/evaluation/test_eval_set_v3.py::test_composition_v3`.

- [x] 15. **The owner's review of set v3 (AC16): the implementer stops here.** `iterations: 0`
      (Done per the two 2026-10-31 owner decisions: an agent review of the essential cases, no stop.)
      - End the run with `RESULT: ESCALATE` (`KIND: decision`). The message gives:
        - the counts of changed v2 cases and new cases per split, and the `compare-labels`
          output;
        - the command
          `cd backend && uv run python -m app.extraction.evaluation review --set evals/extraction/v3`;
        - the reminders: `other_match_absence` cases expect no `out`/`doubt` event,
          `cup_injury` cases keep theirs, and the N.Williams and Evanilson slips are labelled
          as the posts say (`available` and no event, respectively).
      - On resume, run `test_eval_set_v3.py` in full. If a composition rule fails after the
        review, draw more candidates as in step 14 and escalate again. Commit
        `data: review extraction set v3` if the owner has not committed it.

      Automatic verification:
      `uv run pytest -q tests/extraction/evaluation/test_eval_set_v3.py`, then Verify.

- [x] 16. **Dev iterations of prompts v6 and `match_check` v2 with the default model (AC6,
      AC17 dev, AC18).** `iterations: 4` Use the paid key from `backend/.env`.
      - Iterate
        `env -u TWEET_SOURCE uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v3 --split dev --model openai/gpt-6-luna --run-name dev-v3-luna-iN`.
      - Edit `extraction.md` and `match_check.md` on dev failures only. Do not patch single
        slips (SPEC "Out of scope"). Stop at five iterations, or when dev passes the five
        thresholds and both absence checks.
      - Log every iteration in `specs/020-availability-and-match-check/dev-iterations.md`
        (section "Extraction dev"): the run name, F1, `available_f1`, the false-alarm rate,
        the absence false alarms, the injuries dropped, the cost, and the change made. Keep
        one line `Extraction dev spend: <x> USD` (the `spend` total of the dev runs).
        Discarded test runs, if any, go on a line `Discarded test spend: <x> USD`, which
        step 17's spend test also adds.
      - Escalate (`decision`) if `spend --results-dir evals/extraction/results/v3` reaches
        3 USD.
      - Freeze: commit the prompts and the log (`feat: freeze extraction prompt v6 and match check v2`).
        The versions stay 6 and 2.

      Automatic verification: `uv run pytest -q tests/content tests/extraction -n auto`, and
      `uv run python -m app.extraction.evaluation spend --results-dir evals/extraction/results/v3`
      printing at most 3.00 USD. Then Verify.

- [x] 17. **The test run (AC17, AC18).** `iterations: 0` Tests first, in
      `tests/extraction/evaluation/test_results_files.py`:
      - `test_v3_one_test_run`: `results/v3/*.json` holds `test-v3-openai-gpt-6-luna.json`,
        plus at most one `-r2`. The header has `set_version "v3"`, and the prompt version is
        `extraction@6+link_disambiguation@1+match_check@2`;
      - `test_v3_test_run_passes`: the final run passes `threshold_flags` and both
        `absence_checks`;
      - `test_v3_spend_within_cap`: the v3 test runs' `total_cost_usd`, plus the "Extraction
        dev spend" and "Corroboration regression spend" lines of `dev-iterations.md`, sum to
        at most 5.00.

      The existing v2 tests stay as they are.

      Run, on a clean tree:
      `env -u TWEET_SOURCE uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v3 --split test --model openai/gpt-6-luna --run-name test-v3-openai-gpt-6-luna`.
      An errored case → one repeat as `-r2`. Commit the result files.

      If a threshold or an absence check fails, write the report (step 18) saying so, commit
      it, and escalate (`decision`). Never switch the model (AC19).

      Automatic verification:
      `uv run pytest -q tests/extraction/evaluation/test_results_files.py`, then Verify.

- [x] 18. **The report `docs/reports/extraction-eval-v3.md` (AC18, AC19).** `iterations: 0` It has these
      sections:
      - "Set composition": per split; the tags; `available` per split; the `compare-labels`
        output against v2; the #24 posts;
      - "Dev iterations": from `dev-iterations.md`;
      - "Test results": the five thresholds, `available_f1`, the absence false alarms, the
        injuries dropped, line-ups dropped, PLN a month, and p50/p95;
      - "Corroboration regression": the six runs and the two means against the tolerance;
      - "Total spend": the `spend` output plus the corroboration costs;
      - "Threats to validity": the judge pre-labels; the GW9 cases written from the same
        posts; the one-answer-per-club rule; the variance (#60);
      - "Re-extraction": what the owner's AC21 step is expected to change.

      Tests first:
      - `tests/test_docs.py::test_extraction_report_v3_has_every_section`;
      - `tests/extraction/evaluation/test_results_files.py::test_report_v3_names_the_test_run`
        (the run name and its F1 to three decimals).

      Automatic verification:
      `uv run pytest -q tests/test_docs.py tests/extraction/evaluation/test_results_files.py`,
      then Verify.

- [x] 19. **The documents (AC20) and the roadmap.** `iterations: 0` Files:
      - `docs/PROJECT.md`: FR-1.3 gets five types, adding **available** (fit or available,
        no claim about starting).
      - `docs/DECISIONS.md`, rows dated 2026-10-30:
        - the type `available` (supersedes "FR-1.3 stays four event types" in 2026-09-28,
          and "should be available gives no event" in 2026-09-29);
        - the match check for `out`/`doubt` and line-ups (extends 2026-10-26; supersedes in
          part the 2026-09-28 relevance rule that availability events count whatever the
          competition: a rest, squad choice or suspension for another match is now dropped,
          an injury still carries over; extends 2026-10-24, whose read-time filter now also
          drops an absence answered false);
        - the AC10 anchor rule (extends 2026-09-30);
        - the group "✅ Wraca do gry" and breaking only on a reversal (extends 2026-10-06 and
          2026-10-02);
        - extraction set v3.

        The earlier rows get "(superseded in part by 2026-10-30, spec 020)".
      - `docs/EVALUATION.md`: the extraction procedure names `evals/extraction/v3` and the
        new flags and tags.
      - `docs/BACKLOG.md`: remove #24, and add #61 (P3: re-run the retrieval evaluation for
        the `available` query template; trigger: the next retrieval evaluation).
      - `docs/ROADMAP.md`: a ticked Stage 2 item after the 019 item, linking
        `../specs/020-availability-and-match-check/SPEC.md` and the report, and naming
        BACKLOG #24.

      Tests first, in `tests/test_docs.py`:
      - `test_project_fr_1_3_lists_five_types`;
      - `test_decisions_cover_available_and_absence_check` (one row with `available`,
        `supersede` and `FR-1.3`; one row with "match check" and `doubt`);
      - `test_evaluation_md_names_set_v3`;
      - `test_backlog_24_removed_and_61_added`;
      - `test_roadmap_020_ticked`.

      Keep the existing single-row lookups green (see "Risks and traps").

      Automatic verification: `uv run pytest -q tests/test_docs.py`, then Verify.

## Risks and traps

- **Docs tests that unpack exactly one row** (`tests/test_docs.py::test_decisions_cover_match_check_and_set_v2`):
  - no new DECISIONS row may hold both `for_next_pl_match` and `adr/0010-`;
  - no new row may hold both "fail-open" and "match check";
  - no new row may hold "Extraction evaluation set v2".

  Write the set v3 row as "Extraction set v3", and refer to fail-open as "kept on a null
  answer", or extend the tests.
- **Tests that encode the old rules:**
  - `test_kept_at_read` rows for out/doubt false (step 1);
  - `test_no_match_check_without_lineup_events` and
    `test_match_check_answer_on_that_clubs_lineup_events_only` (step 2);
  - `test_extraction_prompt_v5_rules` (step 3);
  - `test_cli.py` `match_check@1` (step 2);
  - the corroboration `test_v2_test_runs_share_one_prompt` (step 7).

  Each is replaced in its step, never deleted without a replacement.
- **The frozen v1/v2 composition** breaks if `EVENT_TYPES` reaches it. That is why
  `LEGACY_EVENT_TYPES` is there (step 1).
- **Order of the paid runs.** The v1 judge runs must precede step 7. Both sets of runs must be
  on a committed tree (`git_dirty: false`).
- **One answer per post and club.** A mixed post (a cup XI with an injured player) gets one
  answer. Prompt v2 favours keeping the injury. The test run's "injuries dropped" shows the
  cost.
- **Cost a month.** `out`/`doubt` posts now also make a match-check call. If the projected
  cost passes 5 PLN, that is a failed threshold (AC19 → escalate), not a reason to drop the
  check on the agent's own decision.
- **The judge text and the SQL table differ for `benched` against "fit".** v1's judge text
  (kept word for word) labels a fit post `contradicts` against a `benched` anchor, while the
  table gives `related` for an extracted `available` claim. This is kept so the regression
  measures only the addition. It is a backlog proposal, not a step.
- **A mixed breaking post.** When a post breaks for another player, its x_id becomes
  "included" for the deadline (`included_origins` is per post). The non-reversing player's
  news then counts as already shown in later news e-mails. This is accepted as an edge case.
- **The second judge pass (AC10)** adds up to 10 judge calls for a player whose newest claim
  is a non-reversing `available`. Alerts run 6 players in parallel, so the build gets slower
  but not serial.
- **Compose is production.** Only `export-corpus`, `snapshot-players` and
  `snapshot-fixtures` read it (read-only). No migration and no `reextract` by the agent. The
  CLIs run as `env -u TWEET_SOURCE`.
- **Run variance** (#60): one test run of one model is noisy. The report says so, and the
  regression uses means of three.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

- Verify fully green.
- `uv run python -m app.extraction.evaluation evaluate --set evals/extraction/v3 --split test ...`
  (step 17): the output shows "passes thresholds: yes" and both absence checks "yes".
- `uv run python -m app.extraction.evaluation spend --results-dir evals/extraction/results/v3`
  plus the six corroboration `cost_usd` values: a total of at most 5.00 USD, recorded in the
  report.
- `uv run pytest -q tests/alerts/test_end_to_end.py tests/alerts/test_breaking.py` on the test
  container (no Compose): the Palmer-shaped digest and the two breaking cases render as
  AC13–AC15 say.
- Record the commands and their results in the Definition of Done line below.

### Manual (performed by the owner)

- After the merge, re-extract the current alert window on Compose and read the next digest.
  Run `cd backend && uv run python -m app.extraction reextract --since <the last deadline> --until <now>`,
  then `uv run python -m app.alerts preview --at <now, Europe/Warsaw> --kind digest`.
  Pass when: in the preview, Evanilson and N.Jackson are not under "🔴 Nie zagra" for the
  EFL Cup squad; an `available` reversal (Palmer, if his news still stands) shows under
  "✅ Wraca do gry"; and N.Williams's card follows his newest post (not "Wypada ze składu"
  from "He is OK… trained the entire week").

## Definition of Done

- [x] all steps ticked
- [x] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto` fully green (2552 passed)
- [x] end-to-end verification (automatic) performed: Verify green; the step 17 test run prints "passes thresholds: yes" with both absence checks "yes" (F1 0.853, 0 of 12 absence false alarms, 1 injury dropped); `spend` 0.1793 USD plus the corroboration costs 0.1089 USD, a total of 0.2882 USD (cap 5.00); `pytest tests/alerts/test_end_to_end.py tests/alerts/test_breaking.py` 18 passed on the test container
- [x] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, `docs/PROJECT.md`, `docs/EVALUATION.md`,
      `docs/BACKLOG.md` updated
- [x] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

- 2026-10-30 — implement — `decision` — Step 14: the planned set v3 candidate pool needs about 5.3M judge tokens against the 3M limit; how should candidates be drawn? — Option B: keep the whole GW8 and GW9 window (`--since 2026-10-17`) but drop `--keywords` and use only `--availability-keywords` (about 3.9M with the 228 set v2 relabels); the owner accepts exceeding the 3M limit for this run.
- 2026-10-31 — implement — `decision` — Step 15: the owner review of set v3 has 127 unreviewed cases; must the owner review all of them (AC16)? — No: the owner reviews only the 30 essential cases (the 11 named GW9 posts plus the FFScout "rested for GW9" post 2116079251476611126, the `other_match_absence` cases that still carry `out`/`doubt`, and the v2 cases whose change is more than an added `available`); the other 97 keep the session pre-labels, are marked as pre-label-only (not owner-reviewed) in the set, and the report states that.
- 2026-10-31 — implement — `decision` — Step 15: may the review of the 30 essential set v3 cases be done without the owner? — Hybrid, replacing the previous entry's owner review: a fresh-context agent reviews the 30 essential cases (ids from the previous entry's rules) against the SPEC rules and applies the rule-checkable corrections itself (FFScout 2116079251476611126 gives no event; an `other_match_absence` case gives no `out`/`doubt` unless tagged `cup_injury`; the named GW9 posts follow the outcomes the SPEC states). Only the cases where the agent disagrees with the pre-label beyond those rules, or is unsure, go to the owner as a short list (escalate with their ids; skip the escalation when the list is empty). The set records each case's review source (owner / agent / pre-label only) and the report states the counts.

## Review log

2026-10-30 — plan review (fresh eye, `/pipeline:ship`).

Findings:

- `major` — Step 12 had a forward dependency on step 13: its runner and metrics tests build a
  case tagged `other_match_absence`, but `Tag` (a `Literal` in
  `app/extraction/evaluation/cases.py`) only got the new tags in step 13, so step 12 could not
  go green. Moved the `Tag` change to step 12's files.
- `minor` — Step 2 wrote the AC7 reader tests (`test_an_absence_for_another_match_is_neither_a_claim_nor_judged`,
  `test_an_absence_for_another_match_never_breaks`), but the behaviour they prove is step 1's
  `kept_at_read` change, so they would never be seen red. Moved both to step 1 and added their
  files to step 1's verification command.
- `minor` — Step 19 did not name the 2026-09-28 relevance rule ("availability events count
  whatever the competition") or the 2026-10-24 read filter among the entries the match check
  for absences supersedes or extends, which AC20 requires to be marked. Added both.
- `minor` — Step 14 read Compose (production) without stating the basis; CLAUDE.md allows
  agent reads only when asked. Stated that AC16 of the approved SPEC is the request and listed
  the three read-only commands as the only Compose reads.

Checked and found correct:

- Coverage: every AC1–AC21 has steps and a named proving test; the matrix matches the steps
  (AC21 is manual by the SPEC).
- `kept_at_read` is the single read-time rule: every reader (`alerts/players.py`,
  `alerts/breaking.py`, `corroboration/sources.py`, corroboration and retrieval evaluation,
  the extraction evaluation runner and CLI) goes through it with `fresh_lineups_only=True`,
  and `claimed_post_ids` reads unfiltered, so AC7's "still counts as claimed" holds.
- `label_claim` today gives `related` for `available`×`out`, so the explicit check before the
  existing branch is needed; `available`×`confirmed_starter` already falls to `related`.
- `LEGACY_EVENT_TYPES` is needed: `composition_problems` loops over `EVENT_TYPES`, and
  `composition_problems_v2` calls it, so five types would break the frozen v1/v2 sets.
- Every CLI option the plan uses exists (`export-judge-inputs --cases/--corpus/--keywords/
  --rest-sample/--x-id/--exclude-cases`, `import-judge --select`, `estimate --export`,
  `compare-labels --baseline`, `evaluate --run-name`, `spend --results-dir`, corroboration
  `evaluate --cases/--split/--model/--output/--force`, `export-corpus --since/--until/
  --version`, `alerts preview --at --kind`, `extraction reextract --since --until`).
- Dev results under `results/v3/dev/` are gitignored, so the spend test reading the
  `dev-iterations.md` lines is the right source; `spend` uses `rglob`, so step 16's 3 USD gate
  includes dev runs.
- The corroboration regression files under `results/regression/` do not collide with the
  non-recursive `v2/test-*.json` globs of the existing results tests; the result JSON has
  `prompt_version`, `metrics.accuracy` and `metrics.false_support.rate` as step 8 reads them.
- Budget: the v2 test run of `openai/gpt-6-luna` cost 0.03 USD and a judge v2 run 0.017 USD,
  so 5 USD with the 3 USD / 4.5 USD gates is realistic.
- No new dependency and no migration, consistent with SPEC → "Owner decisions" and the owner
  summary; the owner's review of set v3 and a failed threshold or regression are escalations,
  not owner steps inside `## Steps`; the manual item has a `Pass when:` line.
- The paid-run order (v1 judge runs on a committed tree before the v2 prompt edit) and the
  docs-test single-row traps are handled.
- Language: the plan is in English.

The plan is ready: the one major finding was fixable in the plan and is fixed, no blocker or
SPEC gap remains, and it adds neither a dependency nor a migration.

## Deviations

- `minor` — Step 1: the `test_an_out_event_answered_false_is_not_predicted` runner test moved to step 2, because the evaluation runner only gets a match-check answer for `out` once step 2 changes `match_check_node`; in step 1 it could not be seen red-then-green.
- `minor` — Step 1: the existing `test_fresh_lineups_only_drops_a_lineup_the_check_answered_false` expected a false-answered `out` kept; it now expects an `available` event kept instead (the plan's rule change for `kept_at_read`). `tests/corroboration/helpers.py` gained a `for_next_pl_match` argument.
- `minor` — Step 13: `DEFAULT_SET_DIR` stays `v2` until step 14 creates `evals/extraction/v3` (`tests/extraction/test_linking.py` reads the default set's `players.json`); the switch to `v3` moves to step 14.
- `minor` — Step 13: `session_judge.sh` asks the extraction judge for all four keys, but its validation requires `events` and `other_match_lineup` and accepts the two new flags as optional booleans, so the existing two-key verdict tests stay valid.
- `minor` — Step 2: `test_prelabel_leaves_out_a_lineup_the_check_answers_false` kept a false-answered `doubt`, which the new rule drops; the test now keeps an `available` event instead.

- `minor` — Step 14: the candidate pool follows the owner decision of 2026-10-30 (Option B: `--availability-keywords` without `--keywords`, 439 candidates plus 228 v2 relabels, about 3.8M tokens). Judged by sonnet (retries on opus); 57 v2 cases changed, 171 unchanged, 70 new candidates selected (298 cases in total; dev 91, test 207, 127 of them unreviewed). `backlog_cases["24"]` lists the 11 named GW8-GW9 posts. The composition and `reviewed` tests fail only on unreviewed cases, as planned (for example 12 `other_match_absence` cases still carry out/doubt events; the FFScout "rested for GW9" pre-label says `out`, which the owner corrects in the review).
- `minor` — Step 14: the switch of `DEFAULT_SET_DIR` to `v3` is postponed to step 15 (after the owner's review), because `tests/extraction/test_linking.py` asserts the default set holds the reviewed v2 count (331 >= 439 fails on v3 candidates).
- `minor` — Step 15: per the owner decision of 2026-10-31 (hybrid), the essential cases were reviewed by the implementation agent, not the owner. `EvalCase` gained `review_source` (`owner` | `agent` | `prelabel`, default none; `review` sets `owner`), and `test_review_cli.py` expects it. Result: 171 `owner` (v2 cases unchanged), 50 `agent`, 77 `prelabel` (all `reviewed: true` so `evaluate` accepts them; the report must state these counts). The agent set 41 essential cases (the 11 named GW8-GW9 posts, `other_match_absence` cases still carrying `out`/`doubt`, v2 cases changed beyond an added `available`) plus 9 cases whose tags broke the composition rules. Rules applied: v2 owner labels stand unless a SPEC rule forces a change (23 v2 cases reverted to the owner's events and tags); FFScout 2116079251476611126 gives no event; a post with a kept injury is not tagged `other_match_absence` or `other_match_lineup`; `cup_injury` only with an `out`/`doubt` event. Judgement calls for the owner's later glance, none blocking: Palmer `out` dropped from the Alonso Carabao Cup post 2115090924053147753 (suspension served in the cup; the post stays `cup_injury` for Joao Pedro, so the one-answer-per-club rule may keep Palmer as a false positive), Thomas dropped from the GOAT7 recap 2114057425049543144, Bruno and Kostoulas `available likely` in 2104175133694804334 and 2104121110841139241, Mateta kept `doubt likely` in the two Palace/Sarr posts. No case was left for the owner, so step 15 raised no escalation.
- `minor` — Step 15: `DEFAULT_SET_DIR` of the extraction evaluation now points at `v3`.
- `minor` — Step 16: five dev iterations; the dev F1 stayed at 0.81-0.85 (best 0.848, below 0.85) and the iteration limit ended the loop. The frozen prompt is the i3 text (the extraction prompt gained rules that `available` is never for team-sheet or hurt players and needs a next-match statement); `match_check.md` v2 was not changed. The test split then passed (step 17, F1 0.853, a margin of 0.003; the report says the pass is not stable).
- `minor` — Step 19: earlier DECISIONS rows (2026-09-28 x2, 2026-09-29, 2026-09-30, 2026-10-02, 2026-10-06, 2026-10-24, 2026-10-26) got a prefix "(superseded in part by / extended by 2026-10-30, spec 020)" in their Decision cell, as the plan asks.

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_
