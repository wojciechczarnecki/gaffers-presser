---
status: spec-ready
stage_history:
  - "spec-draft — 2026-10-30"
  - "spec-ready — 2026-10-30"
metrics:
  started_at: 2026-10-30T22:06
---

# SPEC 020 — Positive availability and the match check for absences

## Goal

The pre-deadline alert must say what will happen in the club's next Premier League match. Two
things break that today. First, an absence that applies only to another match (a cup squad, a
rest, a suspension served midweek) is shown as "will not play". Second, newer news that a player
is fit cannot replace older bad news, so the newest post does not decide the card. Success means
the GW9 digest cases below come out right on the evaluation sets and, after the owner's
re-extraction, in a digest built from the live data.

## Context

Evidence: the GW9 digest of 2026-10-30, 20:00 (alert 13, read-only from the Compose database):

- **Evanilson, N.Jackson — "🔴 Wypada ze składu ●●● potwierdzone".** The anchors are posts from
  Wednesday evening ("Evanilson not in the squad — potentially just a full rest"). They describe
  the EFL Cup tie Bournemouth v Aston Villa, but extraction recorded them as `out/confirmed`.
  FFScout's "Evanilson, Jackson + Manzambi rested for GW9!" gave no event.
- **Palmer — "🔴 Wypada ze składu".** The anchor is a pre-Carabao-Cup press-conference post about a
  suspension served midweek. "Palmer is NOT suspended" and "won't be banned for GW9" give no
  event, so they can only appear as judged contradictions.
- **N.Williams — "🔴 Wypada ze składu".** "He is OK… he has trained the entire week" was extracted
  as `out/confirmed`, an extraction slip. The newer "trained all week" posts from FFScout and
  FPLFocal give no event, so they cannot become the anchor.

The causes in the code:

- The match check (spec 018, `app/extraction/flow.py` `match_check_node`,
  `app/content/prompts/match_check.md` v1) runs only for the line-up events `confirmed_starter`
  and `benched` (`LINEUP_EVENTS` in `app/extraction/lineups.py`). `kept_at_read` keeps every
  `out` and `doubt` event whatever match it is about.
- Extraction knows four event types (`EventType` in `app/extraction/schemas.py`, prompt
  `extraction.md` v5). The prompt says outright that "should be available" and "a player
  returning from injury" give no event.
- The corroboration anchor is the player's newest extracted claim (`corroborate` in
  `app/corroboration/service.py`). A newer judged contradiction only lowers the grade one step
  (`grade`, `newer_contradiction` in `app/corroboration/rules.py`) and never changes the card's
  event.
- Alert cards are grouped by the anchor's event type (`GROUP_ORDER` in `app/alerts/render.py`,
  texts in `app/content/alert_email.toml`). Breaking alerts fire for every new extracted post
  about a listed player (`_candidates` in `app/alerts/breaking.py`).
- `extraction_event.event_type` is a plain string column, and `for_next_pl_match` is nullable on
  every event (migration 0014), so no migration is needed.
- Retrieval builds the corroboration query from a per-event-type template
  (`app/content/retrieval_query_templates.toml`). The corroboration judge's prompt
  (`corroboration_judge.md` v1) and the label table (`label_claim`) know the four types.

This closes BACKLOG #24. It follows spec 018 (extraction set v2, the match check) and spec 019
(corroboration set v2).

## Read context

- `docs/ROADMAP.md` — searched for "extraction", "match check", "alert", "#24". The Stage 2
  items are done apart from the README and the per-player alert summary. This spec is a fix to
  delivered Stage 2 alerts. Its delivery adds a ticked Stage 2 item linking this spec, the same way
  the fast-path fixes are recorded.
- `docs/PROJECT.md` — searched for "FR-1", "event type". FR-1.3 fixes four event types, so this
  spec changes FR-1.3 to five.
- `docs/DECISIONS.md` — searched for "event type", "match check", "for_next_pl_match",
  "anchor", "available". It touches 2026-09-28 (four types; the relevance rule), 2026-09-29
  (an absence from another match is `doubt`; "should be available" gives no event), 2026-09-30
  (the anchor is the newest claim; the label table; the anchor's own account counts on neither
  side), 2026-10-06 (groups by the anchor's event type), 2026-10-24 (the 60 h filter for
  line-ups) and 2026-10-26 (the match check for line-ups, fail-open). Each is extended or
  superseded by this spec's decisions, recorded in the same PR.
- `docs/BACKLOG.md` — read in full. #24 is delivered here. #58 (the judge passes no threshold)
  stays open, and this spec only guards against a regression. #60 (repeated runs) shapes the
  regression check.
- `docs/adr/` — read ADR 0010 in full (match check B, the five thresholds, default and fallback),
  searched ADR 0011 for "model". The match check stays architecture B, and the default
  extraction model is not re-chosen.
- `docs/EVALUATION.md` — read "The extraction procedure" and "The corroboration procedure" in
  full. Set v3 follows the extraction procedure (session judge, the owner's review of changed and
  new cases, paid runs only for the model under test).
- `docs/CONVENTIONS.md` — searched for "User-facing text", "Tests". The Polish texts go to
  `app/content/`, and every behaviour gets a test.
- `docs/DEPLOYMENT.md` — searched for "extraction", "LLM_MODEL". No new variable or image change,
  so it does not apply.
- `docs/reports/` — read `extraction-eval-v2.md` (set composition, thresholds, dev iterations)
  and `corroboration-eval-v2.md` (the judge's accuracy on set v2) in part; searched
  `code-audit-2026-10.md` for "extraction". There are no open audit items in this area.

## Scope

- A fifth event type `available`: the player is fit or available for the next Premier League
  match (fit, back in full training, trained all week, not suspended, the ban is served
  elsewhere). It makes no claim about starting. The extraction prompt becomes v6, the schema and
  every place that lists the event types follow, and FR-1.3 and the 2026-09-28/29 decisions are
  updated.
- The match check for availability: one call per post and club for every linked event except
  `available`, under the question "does this event concern the club's next Premier League
  match?". The prompt becomes `match_check` v2. A false answer is dropped at read time, as it is
  for line-ups today.
- Corroboration with `available`: the label table, the judge prompt v2 (the anchor may be
  `available`), the retrieval query template, and the rule that decides when an `available`
  claim may be the anchor.
- Alerts: a new group "✅ Wraca do gry" with the event "Gotowy do gry" and a header counter, and
  breaking alerts for an `available` post that reverses earlier bad news.
- Extraction evaluation set v3 with metrics for the new type and for absences from other
  matches, and a test run with the default model only.
- A corroboration regression check of the judge prompt v2 on set v2.
- A manual step for the owner: re-extraction of the current alert window on the Compose
  database after the merge.

## Out of scope

- Re-choosing the default extraction model or re-running the fallback model
  (`google/gemini-3.1-flash-lite`). A full comparison goes to the next model check (owner
  decision).
- A new corroboration set with `available` anchors. It returns with BACKLOG #58 or #59, the next
  judge change.
- Fixing the corroboration judge's weak `related` label and its false contradictions (the
  Palmer scorer-odds post, the Mendy question post): BACKLOG #58 (P2) stays open.
- Fixtures of other competitions from an outside source: BACKLOG #55 stays.
- A retrieval evaluation re-run for the new query template. The next retrieval evaluation covers
  it (BACKLOG, P3, added by this spec).
- Patching the extraction prompt for single slips (N.Williams, Evanilson). They become
  evaluation cases.

## Requirements and acceptance criteria

### Extraction: the `available` type

- [ ] AC1: Extraction returns `available` for a player said to be fit or available for the next
  Premier League match: "trained all week", "is fine", "back in full training", "is NOT
  suspended", "won't be banned for GW9", "should be available". Certainty follows the existing
  scale (a quote from the manager or club is `confirmed`). Tested with a stubbed model through
  the flow and the store, and measured on set v3 (AC17).
- [ ] AC2: The extraction prompt is v6. The rules that today give no event for "should be
  available" and for "a player returning from injury who is ready for some minutes" give
  `available` instead. Fatigue remarks, fake leaks after the fact, fantasy managers' own teams
  and national-team fitness still give no event. A test checks the prompt version and the five
  types in the prompt.
- [ ] AC3: Every place that lists the event types accepts `available`: the extraction schema and
  the evaluation judge schema, the corroboration evaluation's type texts, the retrieval query
  templates ("{player} fit" in English and "{player} gotowy do gry" in Polish) and the alert
  texts. An unknown type still fails validation.
- [ ] AC4: The 60 h line-up filter (DECISIONS 2026-10-24) applies neither to `available` nor to
  `out` and `doubt`. A test shows an `available` event 70 h before kickoff is kept.

### Extraction: the match check for absences

- [ ] AC5: The match check runs once per post and club for every linked event of type `out`,
  `doubt`, `benched` or `confirmed_starter`, never for `available`. A post with an `out` and a
  `confirmed_starter` of one club makes one call. Tested by counting calls with a stubbed model.
- [ ] AC6: `match_check` v2 answers `for_next_pl_match` false for an absence that concerns only
  another match: rested or rotated for a cup, European or national-team match, left out of such
  a squad with no injury given, or a suspension served in another competition. It answers true
  for an injury or illness from any match, and for an absence with no other match in sight. The
  line-up rules of v1 stay. Measured on set v3 (AC17), and the prompt text is checked by a test.
- [ ] AC7: An `out` or `doubt` event with `for_next_pl_match` false is dropped by `kept_at_read`,
  in every reader that uses it (alerts, breaking alerts, corroboration's SQL claims, the
  evaluation). Its post still counts as claimed (DECISIONS 2026-10-24), so retrieval does not
  bring it back to the judge. A null answer keeps the event (fail-open, DECISIONS 2026-10-26).
- [ ] AC8: Events stored before this spec (`for_next_pl_match` null on `out` and `doubt`) are
  read as today. A test of `kept_at_read` covers null, true and false for each of the five types.

### Corroboration

- [ ] AC9: The label table: `available` against `available` is `supports`, against `out` or
  `doubt` (either side the anchor) is `contradicts`, and against `benched` or
  `confirmed_starter` (either side the anchor) is `related`. The existing pairs are unchanged.
  `label_claim` gets a test for every pair of the five types.
- [ ] AC10: An `available` claim is the anchor only when it reverses. The window has to hold an
  older `out` or `doubt` claim about the player, or an older judged post labelled `contradicts`
  against it, from any account, the anchor's own included. Otherwise the anchor is the newest
  claim of another type, and the `available` post is labelled against it by AC9. With no claim
  of another type, the player has no anchor and no card. Tested for the reversing case, the
  non-reversing case with an older `confirmed_starter`, the only-`available` case, and the
  same-account case (a reporter's own "out" then "fit").
- [ ] AC11: The judge prompt is `corroboration_judge` v2. It describes `available` as an anchor
  type and keeps v1's labels: a post saying the player is out or doubtful `contradicts` an
  `available` anchor, and a line-up post is `related` to it. A test checks the version and the
  five types.
- [ ] AC12: The corroboration regression check: the judge prompt v2 with the default judge model
  runs three times on set v2's test split. The mean accuracy is at most 0.05 below the mean of
  three runs of v1 under the same conditions, and the false-`supports` rate is at most 0.05
  higher. Both are recorded in the report (AC19).

### Alerts

- [ ] AC13: Cards with an `available` anchor form the group "✅ Wraca do gry". It comes
  second-to-last, after "🟡 Na ławce" and before "🟢 Zagra", with the event label "Gotowy do gry"
  and the header counter "✅ wraca N". The texts live in `app/content/alert_email.toml`, and the
  group has its own palette entry in the HTML part. Tested in render tests for the plain text
  and the HTML.
- [ ] AC14: An `available` card shows the reversed news as "Zaprzecza" with the existing
  reversal note, and the grade follows the existing rules (`grade`) with no special case.
  Tested on a fixture shaped like the Palmer case: older `out` claims, a newer `available`
  post, and the card in "✅ Wraca do gry".
- [ ] AC15: A breaking alert fires for a new `available` post only when, as its anchor, it
  reverses (AC10) for at least one listed player it names. A new `available` post with nothing to
  reverse sends no breaking e-mail and is not recorded as included, so it can still appear in a
  later digest or news e-mail as a supporting or related source. Tested with both posts in
  `run_breaking`.

### Evaluation

- [ ] AC16: Extraction set v3 (`backend/evals/extraction/v3/`) is set v2 relabelled under prompt
  v6's rules by the session judge, plus new real cases from the GW8 and GW9 windows that the
  owner reviews (`review`, the changed and new cases). The composition validator requires, in the
  test split, at least 5 `available` events, and at least 8 cases (5 in test) tagged
  `other_match_absence`, which are absences that concern only another match and expect no event.
  It also requires at least 3 cases tagged `cup_injury`, an injury from a cup or European match
  that keeps its `out` or `doubt`. The GW9 posts named in "Context" are in the set, among them
  the N.Williams and Evanilson slips.
- [ ] AC17: The evaluation reports, beside the existing metrics, the F1 of `available` events,
  the other-match absence false alarms (`other_match_absence` cases with an `out` or `doubt`
  event left after the check), and the injuries dropped by the check (expected `out` or `doubt`
  events dropped by a false answer). The test run of `openai/gpt-6-luna` with prompts
  `extraction@6`, `link_disambiguation@1` and `match_check@2` passes ADR 0010's five thresholds.
  It also has at most 1 of the `other_match_absence` test cases as a false alarm and at most 1
  injury dropped by the check.
- [ ] AC18: Paid API spend for the spec's evaluation runs (dev iterations, the test run, the
  corroboration regression) stays at or under 5 USD. The judge is the Claude Code session. The
  spend is printed by `spend` and recorded in the report.
- [ ] AC19: A report `docs/reports/extraction-eval-v3.md` holds the set composition, the dev
  iterations, the test table, the corroboration regression and the spend. If the default model
  fails a threshold, the report says so and the spec stops for the owner's decision. It never
  switches the model on its own.

### Documents and the owner's step

- [ ] AC20: FR-1.3 in `docs/PROJECT.md` lists five event types. `docs/DECISIONS.md` records the
  new type, the match check for absences, the anchor rule of AC10 and the new group, and marks
  which earlier entries they supersede. `docs/EVALUATION.md` names set v3 in the extraction
  procedure, and `docs/BACKLOG.md` closes #24.
- [ ] AC21: After the merge the owner re-extracts the current alert window on the Compose
  database (`uv run python -m app.extraction reextract --since <the last deadline> --until <now>`,
  the existing CLI). They then read the next digest or a `python -m app.alerts` preview and check
  three things: Evanilson and N.Jackson are not under "🔴 Nie zagra" for an EFL Cup squad, an
  `available` reversal shows under "✅ Wraca do gry", and N.Williams follows the newest post. This
  is a manual procedure. The agent does not run it.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| A fifth event type `available`, with no claim about starting | An "available" flag on `confirmed_starter`. Showing a newer judged contradiction as a warning on the old card | Only an extracted claim can become the anchor, and "fit" is not "starts". A warning keeps the stale headline and inherits the judge's false contradictions (#58) |
| An `available` claim is the anchor only when it reverses an older `out`/`doubt` (AC10) | Every `available` becomes the anchor and gets a card. Merging into "🟢 Zagra" | "X trained" for the whole squad would flood the digest and hide a starter leak behind a fitness post. The owner wants a card only when the news turns |
| The group "✅ Wraca do gry" goes second-to-last, before "🟢 Zagra" | Between doubt and benched. At the end | A benched player is bad news, close to out; a returning player is hope (the owner) |
| `available` ↔ `benched`/`confirmed_starter` = `related` | `available` supports `confirmed_starter` | Fit does not mean starting, so it must neither raise nor lower a line-up card |
| One match check call per post and club over `out`, `doubt` and line-ups, prompt v2, dropped at read time | Checking `out` only. Checking `available` too. Dropping the events before storing | `doubt` from a cup rest is as wrong as `out`. One call keeps the cost per post and club. Storing the answer keeps the events and lets the rule be switched off without re-extraction (ADR 0010) |
| A false answer means "concerns only another match"; an injury from any match is true | A competition field on every event | It keeps DECISIONS 2026-09-28: injuries carry over, rests and squad choices do not |
| Extraction set v3 relabels v2 and adds GW8–GW9 cases, with the owner's review | Adding only new cases to v2 unchanged | v2's labels say "no event" where v6 expects `available`, so the scores would lie |
| Only the default model's test run | The full six-model comparison. Default plus fallback | The prompt changes, not the model choice, and the comparison is four days old (the owner) |
| A corroboration regression on set v2, three runs each for v1 and v2 | A new corroboration set v3 with `available` anchors | Set v2 has no `available` anchors, so the check guards the existing labels. One model's runs vary by about 0.12 (#60), so it uses means |

## Owner decisions

- 2026-10-30: one spec covering the match check for absences and the positive availability type
  (BACKLOG #24).
- An `available` card only when it reverses earlier `out`/`doubt` news. The group is "✅ Wraca do
  gry", second-to-last, before "🟢 Zagra".
- The labels: `available` against `out`/`doubt` contradicts, against `benched`/`confirmed_starter`
  is related.
- The match check: `out`, `doubt` and line-ups in one call, not `available`.
- Evaluation: extraction set v3 with the owner's review of changed and new cases, the default
  model only, and a corroboration regression on set v2 with no new corroboration set.
- Breaking alerts fire for an `available` post only when it reverses.
- A manual re-extraction of the current window on the Compose database by the owner after the
  merge.
- No new dependency and no migration (`event_type` is a plain string, and `for_next_pl_match`
  exists on every event).

## Open questions (non-blocking)

- none
