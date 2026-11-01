# PLAN 021 — Alert categories and simpler corroboration rules

## Owner summary

- **Approach:** All the work is in code, texts and docs. No evaluation is run. Corroboration
  chooses the anchor first, from SQL claims and FPL flags only, with one shared function. It
  then runs retrieval and the judge once, against the final anchor. One function finds the
  reversal: the newest older SQL claim, from any account, if it contradicts the anchor; for an
  `available` anchor, a non-`a` FPL flag at the post's time also counts. An `available` that
  qualifies by neither falls back only to an older `benched` or starter claim, never to
  `out`/`doubt`; otherwise the player gets no card (owner decision B1). The judge counts only
  when it says `contradicts`. ●●● needs two accounts. The label table gets `benched`/`out`.
  Alerts get one name per category, a lime accent for "🟢 Gotowy do gry" and a
  "⚠️ Wcześniej:" line. "New" now also needs the post's first successful extraction to finish
  after the previous alert. Breaking alerts use the same first-extraction rule and the same
  anchor decision.
- **Main risks:**
  - AC16 rests on GW9 data the plan cannot see, because agents do not read the Compose
    database. Manzambi drops only if the post just before his newest `available` post is not
    the `out` slip. Two such posts in a row give that. If the slip comes right before it,
    his card stays until SPEC 022 fixes the extraction (see the traps).
  - Many existing tests encode the old rules: the two judge passes, judged `supports`,
    confirmed = ●●●, the old category names and blue, and the own-account rule. They are
    rewritten to the new rules, step by step.
  - The test helper `add_claim` now lets a post's extraction finish no earlier than the post
    itself. Before, every extraction finished at the test's fixed `NOW`. Tests that relied on
    that unrealistic order get explicit times.
- **New dependency:** no.
- **Data migration:** no. `player_flag_change` and `extraction` already hold the data. This
  is confirmed in SPEC → "Owner decisions".
- **Manual scenarios for the owner:** 1. After the merge, preview the GW9 news alert and the
  digest at 2026-10-31 11:00 on the Compose database (read-only) and check the five GW9 cards
  (AC16).

## Approach

### What the plan rests on

- `specs/021-alert-categories-and-corroboration-rules/SPEC.md`: read in full, including
  `## Owner decisions`.
- `docs/CONVENTIONS.md`: read in full. The points used here: Polish texts in `app/content/`;
  tests check the fields of a template, not whole texts; database tests on a container; a
  commit after every green step with only that step's files.
- `docs/DECISIONS.md`: searched for "anchor", "reversal", "label table", "grade", "included",
  "group", "available", "FPL flag" and the dates 2026-09-26, 2026-09-30, 2026-10-02,
  2026-10-06 and 2026-10-30. The search found the rows the SPEC names: line 17 (alerts from X,
  never from FPL flags), 30 (the flag change log), 56 and 61 (corroboration; the own-account
  rule), 65 and 66 (alerts; included posts), 67 (groups and the card), 123 and 124 (the
  `available` anchor rule and "✅ Wraca do gry"). Earlier rows carry prefixes such as
  "(extended by 2026-10-30, spec 020)". This is the pattern for marking rows (step 10).
- `docs/ROADMAP.md`: searched for "Stage 2", "020" and "alert". The ticked item for spec 020
  (lines 94–98) is the pattern for this spec's item.
- `docs/PROJECT.md`: searched for "FR-2.2" (line 54: "flag a reversal of earlier news").
- `docs/BACKLOG.md`: searched for "#58" and "#59" (rows at lines 46 and 47).
- `docs/DEVELOPMENT.md`, `docs/EVALUATION.md`, `docs/adr/`: not opened. The SPEC's read
  context says they do not apply: no CLI change, no evaluation, no change to the judge model.
- `specs/020-availability-and-match-check/PLAN.md`: the top 60 lines, for the house style.
- Code, read in full: `app/corroboration/{rules,service,sources,schemas,runtime}.py`,
  `app/alerts/{service,breaking,store,render,players,schemas,loop}.py`,
  `app/content/alert_email.toml`; `app/extraction/store.py` (`current_extractions`,
  `_latest_per_post`); `app/fpl/models/reference.py` (`PlayerFlagChange`) and
  `app/fpl/reference.py` (`_latest_flags`).
- Code, searched for consumers: `app/corroboration/cli.py` (prints `result.reversal`),
  `app/corroboration/evaluation/building.py` (uses `label_claim`) and
  `app/corroboration/evaluation/cases.py` (the judge's own guideline table, which is not
  changed: the judge prompt is out of scope).
- Tests, read: `tests/corroboration/{helpers,test_rules,test_service}.py` (the label table and
  the `available` tests), `tests/alerts/{helpers,test_breaking,test_slots,test_end_to_end}.py`
  (fixtures and the `available` cases), `tests/content/test_alert_email.py` (the vocabulary
  check and the every-key test), `tests/test_docs.py` (the docs-row test pattern) and
  `tests/test_module_boundaries.py` (corroboration may import `fpl` and `extraction`; alerts
  may import `extraction`).

### Design

**1. One anchor decision, before retrieval (AC3, AC5, AC13, AC14).** New pure functions go
in `app/corroboration/rules.py`. They replace `reverses` and `reversal`:

```python
def flag_at(flags: Sequence[FlagState], moment: datetime) -> FlagState | None
    # the latest row observed at or before `moment`
def reversed_claim(anchor: Claim, claims: Sequence[Claim]) -> Claim | None
    # the newest claim older than the anchor by (created_at, x_id), from any account,
    # returned only when label_claim(anchor.event_type, it.event_type) == "contradicts"
def choose_anchor(
    claims: Sequence[Claim], flags: Sequence[FlagState], anchor_x_id: int | None = None
) -> AnchorChoice | None

@dataclass(frozen=True)
class AnchorChoice:
    anchor: Claim
    reversed_claim: Claim | None = None
    reversed_flag: FlagState | None = None
```

`choose_anchor` works as follows:

- The candidate is the given `anchor_x_id` claim, or else the newest claim.
- A candidate that is not `available` is the anchor, with `reversed_claim(candidate, claims)`.
  A given anchor that is not the newest stays the anchor, as today for breaking.
- An `available` candidate is the anchor only when it is the newest claim and one of two
  things holds:
  - (a) `reversed_claim` finds a claim; or
  - (b) `flag_at(flags, candidate.post.created_at)` has a status other than `a`. The choice
    then carries that flag, and the flag is kept only when (a) found nothing (AC4). No flag
    row at or before the post (`flag_at` returns `None`) does not qualify: (b) needs a
    recorded non-`a` status.
- Otherwise (the narrower fallback, owner decision B1 of 2026-11-01) take the newest claim
  that is not `available`. It is the anchor, with its `reversed_claim`, only when the
  `available` candidate does not contradict it, i.e.
  `label_claim("available", it.event_type) != "contradicts"` (`benched` or
  `confirmed_starter`). When it is `out` or `doubt`, or there is no such claim, the result is
  `None`: no card. The fallback never skips past a newer `out`/`doubt` to an older claim.

"The newest older claim" is literal (AC5). An `available` that follows another `available`
reverses nothing: the older one supports it. It then qualifies only through the flag. So
`out` → `available` → `available` with the flag already `a` gives no card, never a
"🔴 Nie zagra" card that the two newest posts contradict.

`FlagState(status, news, observed_at)` goes into `app/corroboration/schemas.py`. The new
`flag_history(session, player, as_of)` in `app/corroboration/sources.py` reads the player's
`player_flag_change` rows with `observed_at <= as_of`, oldest first. It reads every row, not
only those in the window, because the baseline row is often older than the window.

Variant considered: a stateful "anchor resolver" object shared by `corroborate` and
breaking. It was rejected. Breaking already calls `corroborate(…, anchor_x_id=…)`, so one
pure function inside `corroborate` gives both callers one decision. Breaking then only checks
that the trigger post became the anchor.

**2. `corroborate` (AC7, AC9, AC13).** The new order in `app/corroboration/service.py`:

1. Load claims, claimed ids and the flag history in one session.
2. Call `choose_anchor`. With `None`, return no anchor, with
   `RetrievalReport("skipped", NO_REVERSAL_REASON)` and no retrieval.
3. Label the SQL claims against the anchor.
4. When the judge is ready, call `_judge_candidates` once against the anchor. Keep only the
   items labelled `contradicts`.
5. Call `count_accounts` (unchanged, AC7), then `newer_contradiction` and `grade`.

The double-pass block goes away. `RetrievalReport.judged` still counts every judged post, so
the trace and the CLI show the judge's work.

`Corroboration` loses the `reversal: bool` field and gains `reversed_post: Citation | None`
and `reversed_flag: FlagState | None`. A `reversal` property returns whether either is set,
so `app/corroboration/cli.py` and the trace's `"reversal"` key keep working. The citation is
built with `_citation` from a `LabelledPost(origin="sql", label="contradicts")` of the
reversed claim. When the anchor's own account wrote the reversed post, it is not in
`contradicting` (AC7). It is still the card's reversal.

**3. Labels and grade (AC8, AC10).** `label_claim` gets `{"benched", "out"}` →
`contradicts`. `GradeRules` gets `confirmed_high_min_supporting: float = 1`. The levels:

- `high`: the weight is at least `high_min_supporting` (2), or the certainty is `confirmed`
  and the weight is at least `confirmed_high_min_supporting` (1).
- `medium`: the certainty is `confirmed` or `likely`, or the weight is at least
  `medium_min_supporting` (1).
- `low`: anything else.

Reasons stay English sentences, one per rule that fired. The lowering is unchanged.

**4. Categories (AC1, AC2).** `alert_email.toml` swaps `[group]`, `[event]` and the
category keys of `[summary]` for one table, `[category]`, that holds the five names with their
circles. `[summary]` keeps only `separator`. `render.py` uses `category[event]` for the group
heading and the card label. The counter uses `category[event].lower()`, so the name exists
only once. `[palette.available]` changes to lime: accent `#84cc16`, background `#f7fee7`,
heading `#3f6212`.

Variant considered: keep three tables with the same words, and add a test that they match.
It was rejected because one table cannot drift.

**5. The "⚠️ Wcześniej:" line (AC4, AC6).** New template keys:

- `[note]`: `earlier = "⚠️ Wcześniej:"`, `reversed_post = "{earlier} @{account}, {time}: {url}"`,
  `reversed_flag = "{earlier} flaga FPL — {news}"` and
  `reversed_flag_no_news = "{earlier} flaga FPL"`, for a flag with empty `news`.
- `[html]`: `reversed_post`, a note `<div>` whose account is a link, in the style of
  `source_link`, followed by the time.

`[note] reversal` is removed. `Card` gets `reversed: Source | None` and
`reversed_flag: str | None`. The reversal line comes first, before the other notes, in the
plain text, the WhatsApp text and the HTML. The time is `POST_TIME` in Warsaw. The account is
`original_author`. The reversed post is filtered out of the "Zaprzecza" list by its `x_id`.
The "N przeciw" count and the order key still count every contradicting account. A card
whose only contradicting post is the reversed one shows "1 przeciw" and the line, but no
"Zaprzecza" label.

**6. What is new (AC11).** Two new pieces:

- `first_extractions(session, x_ids) -> dict[int, datetime]` in `app/extraction/store.py`:
  `min(finished_at)` of the `status = 'extracted'` rows per post. It sits beside
  `current_extractions`, extraction's public read API.
- In `app/alerts/service.py`, `_is_new(origins, included)` becomes a `Newness` value object:

```python
@dataclass(frozen=True)
class Newness:
    included: set[int]
    since: datetime | None            # the previous alert's as_of (last_alert_as_of)
    origins: dict[int, set[int]]
    first_extracted: dict[int, datetime]
    def is_new(self, x_id: int) -> bool
```

A post is new when its origins are not included, and either `since` is `None` or its first
successful extraction finished strictly after `since`. A post with no successful extraction
falls back to the old rule ("not included" alone). Only a judged post can be in that state,
and such a post cannot be a re-extraction. `build_slot_alert` builds the object.
`corroborate_player` fills in the origins and first extractions of shown posts that are
missing, the same way it fills in origins today. `run_slot` already passes `previous`.

**7. Breaking (AC12, AC14).** In `app/alerts/breaking.py`:

- `_candidates` checks `first_extractions(...)[x_id] >= since_extracted` instead of the
  current row's `finished_at`. This keeps today's `>=` boundary at the loop's mark.
- `Candidate.available_only` is removed. Any named player whose corroboration (with
  `anchor_x_id`) does not end with the trigger post as its anchor gets no section.
- `shown_x_ids` (in `service.py`) adds `reversed_post.x_id`, so the post the card names is
  recorded as context. The gate `_extracted_since` is unchanged: it only lets a pass skip
  work, and a re-extraction that opens the gate now finds no candidate.

### Patterns reused

- The test fixtures `tests/corroboration/helpers.py` (`seed_reference`, `add_claim`,
  `add_extraction`) and `tests/alerts/helpers.py` (`corroboration`, `citation`, `report`,
  `scripted_corroboration`, `alerts_runtime`).
- `world()` and `add_late()` in `tests/alerts/test_breaking.py`, and `seed()` and `setup()`
  in `tests/alerts/test_slots.py`.
- `FakeChatModel.received_messages` counts judge calls (`tests/corroboration/test_service.py`).
- `post_origin_sets`, and the way it fills in missing ids, in `app/alerts/store.py` and
  `corroborate_player`.
- The docs-row tests of `tests/test_docs.py` (`test_roadmap_019_ticked`).

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 6 | `tests/alerts/test_render.py::test_one_category_name_in_header_counter_and_card` (plain text, HTML, WhatsApp); `tests/content/test_alert_email.py::test_template_file_is_valid_toml_with_every_vocabulary` |
| AC2 | 6 | `tests/alerts/test_render.py::test_gotowy_and_zagra_accents_differ_and_are_not_blue` |
| AC3 | 3, 4 | `tests/corroboration/test_rules.py::test_choose_anchor_*`; `tests/corroboration/test_service.py::test_available_anchor_by_reversed_news`, `…_by_a_non_a_flag_without_news`, `test_available_with_an_a_flag_and_no_news_has_no_card`, `test_a_flag_turned_a_before_the_post_gives_no_card`, `test_an_available_chain_after_out_with_an_a_flag_has_no_card` |
| AC4 | 4, 7 | `tests/corroboration/test_service.py::test_available_anchor_by_a_non_a_flag_without_news` (`reversed_flag`); `tests/alerts/test_render.py::test_flag_reversal_line_shows_the_flag_news` |
| AC5 | 3, 4 | `tests/corroboration/test_rules.py::test_reversed_claim_*`; `tests/corroboration/test_service.py::test_reversal_own_account_changes_story`, `…_another_accounts_older_contradiction`, `…_newer_supporting_claim_ends_it`, `…_judged_older_contradiction_alone_is_none` |
| AC6 | 7 | `tests/alerts/test_render.py::test_reversal_line_names_the_post_and_it_is_not_listed_again`; `tests/alerts/test_end_to_end.py::test_palmer_shaped_reversal_is_a_gotowy_do_gry_card` |
| AC7 | 4 | `tests/corroboration/test_rules.py::test_accounts_the_anchor_account_never_contradicts` (kept); `tests/corroboration/test_service.py::test_reversal_own_account_changes_story` (`contradicting == []`) |
| AC8 | 1 | `tests/corroboration/test_rules.py::test_label_table` (all 25 pairs, table updated) |
| AC9 | 4, 8 | `tests/corroboration/test_service.py::test_a_judged_support_adds_no_account`, `test_a_judged_contradiction_still_lowers_the_grade`; `tests/alerts/test_slots.py::test_a_judged_support_is_not_an_included_post` |
| AC10 | 2 | `tests/corroboration/test_rules.py::test_grade_*` (each level, `confirmed` with 0 supporting = medium, `confirmed` + 1 = high) |
| AC11 | 8 | `tests/alerts/test_slots.py::test_a_reextracted_old_post_is_not_new`, `test_a_late_ingested_post_first_extracted_after_the_digest_is_new`, `test_a_failed_then_retried_extraction_after_the_digest_is_new`; `tests/extraction/test_store.py::test_first_extractions_*` |
| AC12 | 9 | `tests/alerts/test_breaking.py::test_a_reextraction_in_the_breaking_window_sends_nothing` |
| AC13 | 4 | `tests/corroboration/test_service.py::test_one_judge_pass_for_a_non_reversing_available` |
| AC14 | 9 | `tests/alerts/test_breaking.py::test_reversing_available_post_breaks`, `test_flagged_available_post_breaks`, `test_an_available_post_about_a_player_with_no_other_claim_sends_nothing` |
| AC15 | 10 | `tests/test_docs.py::test_roadmap_021_ticked`, `test_decisions_cover_alert_categories_and_rules`, `test_backlog_58_and_59_carry_the_021_notes` |
| AC16 | — | manual (the owner's preview on Compose, after the merge) |

## Steps

- [x] 1. The label `benched` against `out` (AC8). Files: `app/corroboration/rules.py`,
      `tests/corroboration/test_rules.py`. `iterations: 0`
      - Test first. In `LABEL_TABLE`, set `out → benched` and `benched → out` to `C`, and
        leave `doubt`/`benched` as `R`. Run it and see it fail.
      - In `label_claim`, return `contradicts` for the pair `{"benched", "out"}` before the
        `related` fall-through.
      - Also run the corroboration evaluation tests: `building.py` uses `label_claim` to pick
        contradiction candidates.
      Automatic verification: `cd backend && uv run pytest -q tests/corroboration/test_rules.py tests/corroboration/evaluation`

- [x] 2. The grade's top level needs two accounts (AC10). Files: `app/corroboration/rules.py`,
      `tests/corroboration/test_rules.py`. `iterations: 0`
      - Tests first (run them and see them fail before the product change). Rewrite `test_grade_confirmed_is_high` as
        `test_grade_confirmed_alone_is_medium`, the Evanilson shape. Add
        `test_grade_confirmed_with_one_supporting_account_is_high`. Keep the "two supporting
        → high", "likely → medium", "one supporting → medium", "rumour, none → low" and the
        lowering tests, and adjust any that start from a lone `confirmed` anchor. Update
        `test_grade_thresholds_are_named_parameters` for the new
        `confirmed_high_min_supporting` field.
      - Then change `GradeRules` and `grade` as in Design §3.
      Automatic verification: `cd backend && uv run pytest -q tests/corroboration/test_rules.py`

- [x] 3. The pure anchor and reversal rules, and the flag history (AC3, AC5). Files:
      `app/corroboration/schemas.py` (`FlagState`), `app/corroboration/rules.py` (`flag_at`,
      `reversed_claim`, `AnchorChoice`, `choose_anchor`; remove `reverses`, `reversal` and
      `REVERSED_TYPES`), `app/corroboration/sources.py` (`flag_history`),
      `tests/corroboration/helpers.py` (a new `add_flag(engine, player_fpl_id, status, news,
      observed_at)` that inserts a `PlayerFlagChange` row), `tests/corroboration/test_rules.py`,
      `tests/corroboration/test_sources.py`. `iterations: 0`
      - Tests first (run them and see them fail before the product change), in `test_rules.py`. Replace `test_reverses`,
        `test_a_line_up_claim_after_the_bad_news_leaves_nothing_to_reverse`,
        `test_reversal_*` and
        `test_accounts_the_anchor_accounts_own_reversal_sets_no_flag_and_no_lowering` with:
        - `test_reversed_claim_own_account_counts`;
        - `test_reversed_claim_another_accounts_older_contradiction`;
        - `test_reversed_claim_a_newer_supporting_claim_ends_it`;
        - `test_reversed_claim_a_newer_contradiction_is_not_a_reversal`;
        - `test_reversed_claim_benched_before_out` (the Kroupi.Jr shape);
        - `test_flag_at_takes_the_latest_row_at_or_before_the_moment`;
        - `test_choose_anchor_available_by_reversed_news`;
        - `test_choose_anchor_available_by_a_non_a_flag` (the flag is kept only without (a));
        - `test_choose_anchor_available_with_an_a_flag_falls_back_to_a_starter_or_benched`
          (`[confirmed_starter, available]` and `[benched, available]`, flag `a` → the older
          claim is the anchor);
        - `test_choose_anchor_non_qualifying_available_never_falls_back_to_out_or_doubt`
          (`[out, available, available]` and `[doubt, available, available]`, flag `a` at the
          newest post → `None`; `[confirmed_starter, out, available, available]` → `None`,
          not the starter);
        - `test_choose_anchor_only_available_and_no_qualifying_flag_is_none`;
        - `test_choose_anchor_available_with_no_flag_row_does_not_qualify`;
        - `test_choose_anchor_a_flag_turned_a_before_the_post_is_none`;
        - `test_choose_anchor_available_after_available_needs_the_flag`;
        - `test_choose_anchor_given_non_newest_available_falls_back` (by the same narrower
          fallback);
        - `test_choose_anchor_given_non_available_stays`.
      - Keep the account-counting test of the old own-account case: it shows `count_accounts`
        is unchanged (AC7).
      - In `test_sources.py`, add `test_flag_history_reads_rows_up_to_as_of_including_the_baseline`
        (a baseline row older than the window, a row after `as_of` left out, another player
        left out).
      - `service.py` still imports `reverses` and `reversal`, so this step leaves it red until
        step 4. Run only the two files below here. Steps 3 and 4 are committed together after
        step 4 is green.
      Automatic verification: `cd backend && uv run pytest -q tests/corroboration/test_rules.py tests/corroboration/test_sources.py`

- [x] 4. `corroborate` chooses the anchor first and runs the judge once, and the judge counts
      only as a contradiction (AC3, AC4, AC5, AC7, AC9, AC13). Files:
      `app/corroboration/service.py`, `app/corroboration/schemas.py` (`Corroboration`:
      `reversed_post`, `reversed_flag` and the `reversal` property), `tests/corroboration/test_service.py`,
      `tests/alerts/helpers.py` (`corroboration(...)` takes `reversed_post: Citation | None` and
      `reversed_flag: FlagState | None` instead of `reversal: bool`; `full_report` passes a
      reversed post). `iterations: 2`
      - Tests first (run them and see them fail before the product change), in `test_service.py`:
        - Rewrite `test_available_anchor_when_a_judged_older_post_contradicts` →
          `test_reversal_judged_older_contradiction_alone_is_none`. With no flag the
          `available` gets no card and the judge is never called. With an `out` anchor and an
          older judged contradiction, `reversal` is False and the post is in `contradicting`.
        - Rewrite `test_non_reversing_available_falls_back_to_an_older_starter` →
          `test_one_judge_pass_for_a_non_reversing_available`. Assert exactly one judge call,
          with "Event type: confirmed_starter".
        - Rewrite `test_a_judged_out_newer_than_the_available_claim_gives_no_anchor`: no
          anchor and no judge call without a flag; with a `d` flag, the `available` anchor
          and `newer_contradiction`.
        - Remove `test_a_failed_first_pass_with_only_available_claims_is_reported`: there is
          no first pass any more. Replace it with
          `test_no_anchor_runs_no_retrieval` (the monkeypatched `retrieval_candidates` is
          never called).
        - Change `test_same_account_out_then_fit_reverses` to assert `reversal is True`,
          `reversed_post.x_id == 1` and `contradicting == []`. Rename it
          `test_reversal_own_account_changes_story`.
        - Add `test_available_anchor_by_reversed_news`,
          `test_available_anchor_by_a_non_a_flag_without_news` (Kadıoğlu: `reversed_flag.news`
          set, `reversed_post` None), `test_available_with_an_a_flag_and_no_news_has_no_card`
          (Manzambi without the slip), `test_a_flag_turned_a_before_the_post_gives_no_card`,
          `test_an_available_chain_after_out_with_an_a_flag_has_no_card` (B1: `out` →
          `available` → `available`, flag `a`; no anchor, no judge call),
          `test_reversal_another_accounts_older_contradiction` and
          `test_reversal_newer_supporting_claim_ends_it`.
        - Add `test_a_judged_support_adds_no_account`, the SalomonYaniv shape: the judge says
          `supports`, `supporting == []`, `related` holds no judged post, and the grade stays
          as for the SQL claims alone. Add `test_a_judged_contradiction_still_lowers_the_grade`
          (a judged `contradicts` newer than the anchor lowers it one step).
        - Re-check `test_judged_posts_labelled_and_unrelated_dropped` and
          `test_trace_holds_every_step`. Judged `supports` and `related` now leave the
          citations, but they stay in `retrieval.judged`.
      - Then rewrite `corroborate` as in Design §2.
      - Run the alert tests as well: `Corroboration`'s fields changed, and the alerts render
        `reversal` through the property.
      - Commit steps 3 and 4 together.
      Automatic verification: `cd backend && uv run pytest -q tests/corroboration tests/alerts tests/content/test_alert_email.py`

- [x] 5. A test helper: an extraction finishes no earlier than its post. Files:
      `tests/corroboration/helpers.py`. `iterations: 0`
      - `add_claim(…, finished: int | None = None)`. With `None`, the extraction finishes at
        `max(NOW, created_at)`. An explicit `finished` keeps today's meaning (seconds after
        `NOW`).
      - `add_extraction` gets an optional `finished_at: datetime | None` that wins over
        `finished`.
      - No product change. The suite must stay green before step 8 relies on the realistic
        order.
      Automatic verification: `cd backend && uv run pytest -q -n auto tests/alerts tests/corroboration tests/worker`

- [x] 6. Five uniform categories and the lime accent (AC1, AC2). Files:
      `app/content/alert_email.toml`, `app/alerts/render.py`, `tests/alerts/test_render.py`,
      `tests/content/test_alert_email.py`, `tests/alerts/test_end_to_end.py`,
      `tests/alerts/test_breaking.py`. `iterations: 2`
      - Tests first (run them and see them fail before the product change):
        - `test_one_category_name_in_header_counter_and_card` asserts, for one card of each
          type and for the plain text, the HTML and the WhatsApp text:
          - the group heading is `category[event]`;
          - the counter item is `category[event].lower()` with its count;
          - the card line reads `"Saka (Arsenal) — " + category[event]`;
          - no `✅` appears anywhere;
          - the five names are exactly those of AC1. The names are content, so this
            vocabulary check reads them from the template and compares them with the AC1
            list once.
        - `test_gotowy_and_zagra_accents_differ_and_are_not_blue` asserts that the two
          accents differ, that neither is `#2e90fa`, and that the `available` accent is in
          the card's HTML.
        - Update the vocabulary test in `test_alert_email.py` to the `category` table.
        - Replace every `TEMPLATE["group"]`, `["event"]` and `["summary"][event]` in the
          tests with `TEMPLATE["category"]`. Rename `test_available_card_html_has_its_palette`.
      - Then change the TOML (Design §4) and `render.py` (`_summary_items`, `_render_text`,
        `_render_card`, `_render_html`).
      Automatic verification: `cd backend && uv run pytest -q tests/alerts tests/content/test_alert_email.py`

- [x] 7. The "⚠️ Wcześniej:" line (AC4, AC6). Files: `app/content/alert_email.toml`,
      `app/alerts/render.py`, `tests/alerts/test_render.py`,
      `tests/content/test_alert_email.py`, `tests/alerts/test_end_to_end.py`. `iterations: 0`
      - Tests first (run them and see them fail before the product change):
        - `test_reversal_line_names_the_post_and_it_is_not_listed_again`: the post is also in
          `contradicting`. The text and WhatsApp hold
          `"⚠️ Wcześniej: @doubter, <POST_TIME>: <url>"`, built from the template keys. The
          HTML holds its link. The post's URL is not under the "Zaprzecza" label, and
          "1 przeciw" stays.
        - `test_flag_reversal_line_shows_the_flag_news` checks the line with `news` and the
          `reversed_flag_no_news` form.
        - Update `test_notes_only_when_flagged`. Add a flag report and a no-news flag report
          to `test_template_has_every_key_and_renders`, so every new key is read.
        - Update the end-to-end Palmer test and rename it
          `test_palmer_shaped_reversal_is_a_gotowy_do_gry_card`. The line names a2's post,
          and only a1 is under "Zaprzecza".
      - Then change the TOML and `render.py` (`Card`, `_card`, the text and HTML renderers) as
        in Design §5.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts tests/content/test_alert_email.py`

- [ ] 8. "New" follows the first successful extraction in slot alerts (AC9's included rows,
      AC11). Files: `app/extraction/store.py` (`first_extractions`),
      `app/alerts/service.py` (`Newness`; `build_slot_alert`, `corroborate_player`; and
      `shown_x_ids` gains `reversed_post`), `tests/extraction/test_store.py`,
      `tests/alerts/test_slots.py`.
      - Tests first (run them and see them fail before the product change):
        - `tests/extraction/test_store.py::test_first_extractions_take_the_earliest_success_and_skip_failures`
          and `…_empty_ids`.
        - In `test_slots.py`, `test_a_reextracted_old_post_is_not_new`. A post first
          extracted before the digest names no player. It is re-extracted after the digest
          as `out` for Saka. The news slot is `skipped`.
        - `test_a_late_ingested_post_first_extracted_after_the_digest_is_new`: the post was
          created before the digest and first extracted after it. The news alert is sent with
          the post marked " (nowe)".
        - `test_a_failed_then_retried_extraction_after_the_digest_is_new`: a `failed` row
          before the digest, then an `extracted` row after it.
        - `test_a_judged_support_is_not_an_included_post`: a scripted judge says `supports`,
          and its post is in no `alert_post` row.
      - Then implement Design §6.
      Automatic verification: `cd backend && uv run pytest -q -n auto tests/extraction/test_store.py tests/alerts`

- [ ] 9. Breaking alerts: the first-extraction rule and one shared anchor decision (AC12,
      AC14). Files: `app/alerts/breaking.py`, `tests/alerts/test_breaking.py`.
      - Tests first (run them and see them fail before the product change):
        - `test_a_reextraction_in_the_breaking_window_sends_nothing`: a post about a listed
          player was first extracted before `since_extracted` with no event for him and
          re-extracted after it as `out`. `run_breaking` returns 0 and records nothing.
        - `test_flagged_available_post_breaks`: Isak has a `d` flag before the post and no
          other claim. A late `available` post breaks, and the line reads
          "⚠️ Wcześniej: flaga FPL — …".
        - Keep `test_reversing_available_post_breaks`, with the new category name.
        - Keep `test_an_available_post_about_a_player_with_no_other_claim_sends_nothing` (no
          flag), `test_non_reversing_available_post_sends_nothing_and_stays_new` and
          `test_mixed_post_drops_only_the_non_reversing_player`. They now prove the shared
          decision without `available_only`.
      - Then implement Design §7.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_breaking.py tests/alerts/test_loop.py`

- [ ] 10. Documents (AC15). Files: `docs/DECISIONS.md`, `docs/PROJECT.md`, `docs/ROADMAP.md`,
      `docs/BACKLOG.md`, `tests/test_docs.py`.
      - Tests first (run them and see them fail before the product change), in `tests/test_docs.py`:
        - `test_roadmap_021_ticked`: a ticked Stage 2 item that links
          `021-alert-categories-and-corroboration-rules/SPEC.md`.
        - `test_decisions_cover_alert_categories_and_rules`: `2026-11-01` rows that name
          "Gotowy do gry", `player_flag_change`, "Wcześniej", `benched`, "first successful
          extraction" and "●●●". The rows 2026-09-26 (the X-only alerts row), 2026-09-30
          (lines 56 and 61), 2026-10-02 (lines 65 and 66), 2026-10-06 (line 67) and 2026-10-30
          (lines 123 and 124) carry a "(… by 2026-11-01, spec 021)" prefix.
        - `test_backlog_58_and_59_carry_the_021_notes`: #58 names the contradicts-only judge
          option, and #59 names the SalomonYaniv case.
      - Then edit the docs:
        - DECISIONS: append 2026-11-01 rows, one per decision in AC15. Each names the rows it
          extends or supersedes. The "Gotowy do gry" row also states the narrower fallback
          (owner decision B1): a non-qualifying `available` falls back only to a `benched` or
          `confirmed_starter` claim, never to `out`/`doubt`, otherwise no card. The 2026-09-30 own-account row (line 61) is superseded in
          part, for the reversal only. Give the eight earlier rows their prefix, as spec 020
          did.
        - PROJECT FR-2.2: name the reversal definition.
        - ROADMAP: add a ticked Stage 2 item after the spec 020 item.
        - BACKLOG: amend #58 and #59.
      Automatic verification: `cd backend && uv run pytest -q tests/test_docs.py`

- [ ] 11. Full verification and the end-to-end check below. Tick the Definition of Done.
      No files beyond this PLAN.
      Automatic verification: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`

## Risks and traps

- **AC16 depends on GW9 data the plan cannot see.** Manzambi's card drops by the literal
  "newest older claim" rule only if an `available` post separates his newest `available`
  post from the `out` slip, and his flag was `a`. If the slip comes right before his newest
  `available` post, (a) holds and the card stays until SPEC 022 fixes the extraction. The
  implementer must not bend the rule to reach AC16. The owner's manual check names this
  case.
- **A chain of `available` posts after bad news (settled, owner decision B1).** With the
  literal "newest older claim" rule, `out` (A) → `available` (B) → `available` (C) gives C no
  reversal, because B supports it. When the FPL flag turned `a` before C, C does not qualify.
  The narrower fallback (Design §1) then gives no card instead of a "🔴 Nie zagra" card with
  B and C under "Zaprzecza". Do not reintroduce the old skip over older `available` claims
  (DECISIONS 2026-10-30) to reach a different result: the step 3 and 4 tests pin this case.
- **The judge's guideline table in `app/corroboration/evaluation/cases.py`** and the judge
  prompt still say "out / benched → related". The SPEC keeps the prompt (#58). The SQL table
  now says `contradicts`. Leave both as they are. Changing them would change the judge
  evaluation set's rules.
- **`add_claim`'s new default (step 5)** moves extractions of posts created after `NOW` to
  their `created_at`. Breaking tests pass `finished` explicitly and are unaffected. Run the
  whole `tests/alerts` and `tests/worker` packages in step 5, not just one file.
- **Existing news tests** add posts after the digest with the default `finished`. Before
  step 5 they finished at `NOW`, the digest's `as_of`. Under AC11 that would make them "not
  new". Step 5 fixes this, so step 8 must run after step 5.
- **Steps 3 and 4 are one commit**: step 3 removes `reverses` and `reversal`, which
  `service.py` imports. Do not commit a red tree in between.
- **`Corroboration.reversal` becomes a property.** Constructors that pass `reversal=` fail
  (`tests/alerts/helpers.py`). Step 4 updates them in the same commit.
- **The every-key template test** (`test_template_has_every_key_and_renders`) fails until every
  new key (`category`, `note.earlier`, `note.reversed_post`, `note.reversed_flag`,
  `note.reversed_flag_no_news`, `html.reversed_post`) is rendered in it.
- **Polish literals stay in the TOML.** `test_alerts_code_has_no_polish_literals` guards
  `render.py`. `.lower()` on the category name is code, not a literal.
- **Time zones.** The reversal line's time is formatted in Warsaw (`_local(..., POST_TIME)`),
  like the source lines. A flag's `observed_at` is compared in UTC with the post's
  `created_at`.
- **No Compose access.** Tests use their own container. The owner runs the AC16 preview.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

- `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`:
  everything green.
- The scenarios end to end in the test container, already in the suite:
  `uv run pytest -q tests/alerts/test_end_to_end.py tests/alerts/test_slots.py tests/alerts/test_breaking.py`.
  This covers a digest, then a news alert, then breaking, with a reversal card, a re-extracted
  post and a flagged `available` post.
- No CLI run against Compose: the preview reads the owner's database.

### Manual (performed by the owner)

- After the merge, preview the GW9 news alert:
  `cd backend && uv run python -m app.alerts preview --at 2026-10-31T11:00 --kind news`, then
  the same with `--kind digest`, on the Compose database (read-only).
  Pass when:
  - Manzambi has no "🟢 Gotowy do gry" card. If he still has one, his `out` slip comes right
    before his newest `available` post, and SPEC 022 handles it.
  - F.Kadıoğlu is under "🟢 Gotowy do gry" with a "⚠️ Wcześniej:" line.
  - Evanilson is not ●●●.
  - Kroupi.Jr shows the `benched` accounts under "Zaprzecza" or in his "⚠️ Wcześniej:" line,
    and the SalomonYaniv post is not among his sources.
  - The news alert lists Kroupi.Jr and F.Kadıoğlu only for posts first extracted after the
    previous alert.
  - The header counter reads "🔴 nie zagra · 🟠 wątpliwy · 🟡 na ławce · 🟢 gotowy do gry ·
    🟢 zagra" (for the categories present), with the same names on the group headings and
    the cards.

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto` fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, `docs/PROJECT.md` and `docs/BACKLOG.md`
      updated (step 10)
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

- 2026-11-01 — plan-review — `decision` — B1: AC5 read literally plus AC3's fallback turns out → available → available (flag already `a` at the last post) into a "🔴 Nie zagra" card; how to settle it? — Option 1, narrower fallback: AC5 stays as written; a non-qualifying `available` claim falls back only to a claim it does not contradict (`benched` or `confirmed_starter`), never to `out` or `doubt`; otherwise the player gets no card. Record it in SPEC AC3/AC5 and adjust the step 3–4 tests.

## Review log

Review of 2026-11-01 (plan-review, fresh context).

Findings:

- `blocker` — Design §1 and steps 3–4 read AC5's "newest older claim" literally, so an
  `available` anchor whose newest older claim is another `available` reverses nothing; with
  an FPL flag already `a` at its time, AC3 then falls back to the newest other type, which
  can be the older `out` the `available` posts contradict. A return confirmed by a second
  post after the flag cleared becomes a "🔴 Nie zagra" card with the return posts under
  "Zaprzecza" — a wrong card, against FR-2.2's "never outvote the newest leak". Today's rule
  (DECISIONS 2026-10-30, `reverses`) skips older `available` claims for exactly this case
  (`test_a_line_up_claim_after_the_bad_news_leaves_nothing_to_reverse`, `[out, available]`
  → True). The SPEC does not settle it: AC5 and AC3's fallback together produce it, and
  AC16's Manzambi expectation depends on it. It cannot be fixed in the plan without
  changing an AC, so it is escalated; a risk entry was added under "Risks and traps".
- `minor` — Design §1 did not say what (b) does when the player has no flag row at or
  before the post; added: `None` does not qualify, and a test
  `test_choose_anchor_available_with_no_flag_row_does_not_qualify` in step 3.
- `minor` — steps 2–4 and 6–10 said "Tests first" without running them red before the
  product change; each now says to run them and see them fail.
- `minor` — step 8 changes `shown_x_ids`, which `breaking.py` uses, but its verification
  left out `tests/alerts/test_breaking.py`; it now runs `tests/extraction/test_store.py`
  and the whole `tests/alerts` package.

Checked and found correct:

- Coverage: every AC1–AC15 has steps and a named proving test, and the matrix matches the
  steps; AC16 is the owner's manual check, as the SPEC says.
- `extraction` rows are insert-only (`save_extraction`, `_latest_per_post` picks the newest),
  so `min(finished_at)` of `status = 'extracted'` rows is the first successful extraction,
  and the index `ix_extraction_tweet_status_finished` serves it; no migration.
- `player_flag_change` holds `status`, `news` and `observed_at` per (season, player); module
  boundaries allow corroboration → `fpl` and alerts → `extraction`.
- `label_claim` for `{benched, out}` falls through to `related` today; the plan's change and
  the 25-pair table test cover AC8; `evaluation/building.py` is run in step 1.
- Grade levels in Design §3 match AC10, including the lone `confirmed` anchor → `medium`.
- The only consumers of `reverses`, `reversal`, `REVERSED_TYPES` and `available_only` are
  `service.py`, `breaking.py` and `tests/corroboration/test_rules.py`; `reversal=` is passed
  only in `service.py` and `tests/alerts/helpers.py`; the step 3+4 single commit is right.
- `✅` appears in `alert_email.toml` only in `[group]` and `[summary]`, both replaced in step
  6, so the "no ✅" render assertion can hold.
- Breaking: with `available_only` removed, a non-`available` trigger given as `anchor_x_id`
  stays the anchor, so the "trigger became the anchor" check changes nothing for it.
- Slot alerts: `included_rows` and the news filter both go through the new `Newness`; the
  preview CLI passes `previous` as `new_since`, so AC16's preview uses the same rule.
- Step order: step 5 (the `add_claim` default) precedes step 8; steps 3 and 4 share a commit;
  no forward dependency otherwise. No new dependency, no migration, matching SPEC → Owner
  decisions and the Owner summary.
- Language: the PLAN is in English.

Decision: not approved — the blocker above needs an owner decision on the SPEC's AC3/AC5.

Review of 2026-11-01, second pass (plan-review, fresh context, after the owner decision B1).

Findings:

- `none` — no new findings; the first pass's blocker is resolved by owner decision B1,
  applied as below.

Changes made for B1:

- Design §1: the fallback for a non-qualifying `available` takes the newest non-`available`
  claim only when `label_claim("available", …)` is not `contradicts` (`benched`,
  `confirmed_starter`); `out`/`doubt` or nothing → no card; it never skips past a newer
  `out`/`doubt`.
- Step 3: the fallback test now names the starter/benched cases, and a new
  `test_choose_anchor_non_qualifying_available_never_falls_back_to_out_or_doubt` pins the
  chain case; the given-non-newest test uses the same fallback.
- Step 4 and the AC3 row of the matrix: `test_an_available_chain_after_out_with_an_a_flag_has_no_card`.
- Step 10: the DECISIONS "Gotowy do gry" row states the narrower fallback.
- Risks and Owner summary: the open blocker entry is now the settled rule.
- SPEC AC3 records the decision (the owner's decision asked for it) with a matching line in
  the SPEC's `## Owner decisions`; no other SPEC change.

Checked and found correct in this pass:

- Anti-anchoring: my own approach from the SPEC alone (one pure anchor decision from SQL claims
  and flags before retrieval; `min(finished_at)` of successful extractions for "new"; one
  category table; the grade change in `GradeRules`; breaking reusing `corroborate` with
  `anchor_x_id`) matches the plan's design.
- `label_claim` today gives `available` vs `benched`/`confirmed_starter` = `related` and vs
  `out`/`doubt` = `contradicts`, so the fallback condition is expressible with the existing
  table and is unaffected by AC8's `benched`/`out` change.
- B1 keeps AC16's Manzambi expectation: a separating `available` post now gives no card
  (before B1 it would have given a "🔴 Nie zagra" card); the slip directly before the newest
  `available` still keeps the card, as the manual check says.
- Breaking (`app/alerts/breaking.py`): with `available_only` removed, the "trigger became the
  anchor" check is the only filter; a non-`available` trigger stays the anchor; the
  `if not reports: continue` path already leaves a non-qualifying post unrecorded (AC14).
- The first pass's checks (coverage, migrations, module boundaries, step order, language)
  still hold; nothing in B1 adds a dependency, a migration or a forward step dependency.

Decision: approved — the only blocker is settled by the owner's decision B1 and applied in
Design §1, steps 3, 4 and 10, and every AC has a step and a proving test that runs red first.

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_
