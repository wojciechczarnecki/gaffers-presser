---
status: plan-approved
stage_history:
  - "spec-draft — 2026-11-01"
  - "spec-ready — 2026-11-01"
  - "plan-draft — 2026-11-01"
  - "plan-approved — 2026-11-01"
metrics:
  started_at: 2026-11-01T04:49
  plan_steps: 10
  plan_changes: 5
  escalations: 1
  escalations_permission: 0
  escalations_tooling: 0
  plan_review_blockers: 0
  plan_review_majors: 2
  implement_steps: 5
  implement_iterations: 2
  deviations_minor: 5
  deviations_major: 0
---

# SPEC 022 — Rests give no event, and a match check blind to the event types

## Goal

An absence that concerns only another match (a rest, a rotation, a squad left out for a cup,
European or national-team match, with no injury given) must not reach an alert as "Nie zagra".
Today that rests on one unstable step: extraction is told to extract such absences and the match
check, primed by the event type it is given, has to drop them. Success means two independent
barriers, extraction and the match check, measured on extraction set v4 over three test runs,
and the GW9 FFScout post giving no absence after the owner's re-extraction.

## Context

Evidence: the GW9 news alert of 2026-10-31, 11:00, read-only from the Compose database, and the
SPEC 020 test run (`backend/evals/extraction/results/v3/test-v3-openai-gpt-6-luna.json`):

- **FFScout "💆‍♂️ Evanilson, Jackson + Manzambi rested for GW9!"** (2116079251476611126, posted
  after the EFL Cup tie, with its score). Prompt v5 gave no event. Production re-extraction
  under `extraction@6+match_check@2` gave `out/confirmed` with `for_next_pl_match=true` for all
  three, so Evanilson showed under "🔴 Nie zagra ●●●" and Manzambi's training posts became a
  false "comeback". In the SPEC 020 test run the same post gave `benched/confirmed` and the check
  answered false, so the case passed although both extractions were wrong.
- **Why the check passed it.** Prompt v6 (`app/content/prompts/extraction.md`) says "a squad
  rest, a rotation or a suspension served in a cup or European tie is extracted as the post says
  it (`out` or `doubt`): the match check decides later". The check
  (`app/content/prompts/match_check.md` v2, `_render_match_check` in `app/extraction/flow.py`)
  gets the events as `- <player>: <event type>` and applies different rules by type: with
  `benched` the line-up timing rule answered false, with `out` "an absence with no other match
  in sight" answered true. The post also names "GW9", the next Premier League gameweek.
- **Kroupi.Jr.** FPLFocal 2115825835525488887 ("Rose says Kroupi isn't ready to start just yet,
  needs to build up his minutes first") gave `out/confirmed`; AlexSmithEcho and FFScout gave
  `benched/confirmed` for the same news. A single slip, not a rule gap.
- **Manzambi and F.Kadıoğlu.** Their `available` events are about GW9 (Emery: "normally, yes,
  [they will play vs Fulham]"; Hürzeler: "options for Saturday"; FFScout: "expected to return in
  GW9"), so a match check on `available` would not have changed either card. Case
  2115797596119740910 in set v3 carries the tag `other_match_absence` while expecting two
  `available` events; the tag is wrong.
- Results vary between runs of one model (BACKLOG #60); SPEC 020's test F1 was 0.853 against the
  0.85 threshold.

The alert-side rules of the same review (categories, the "Gotowy do gry" rule, the judge, the
grade) are SPEC 021. The two specs are independent.

## Read context

- `docs/ROADMAP.md` — searched for "extraction", "match check", "020". A fix to delivered
  Stage 2 extraction; adds a ticked Stage 2 item linking this spec at delivery.
- `docs/PROJECT.md` — searched for "FR-1.3", "FR-2.4". FR-1.3 (five event types) is unchanged;
  FR-2.4 is served by set v4.
- `docs/DECISIONS.md` — searched for "match check", "relevance rule", "rest", "other match",
  "set v3", "fail-open". Touched: 2026-09-28 / 2026-09-29 (the relevance rule, in part superseded
  by 2026-10-30); 2026-10-26 ×2 (the match check, fail-open: kept); 2026-10-30 (the match check
  for absences: extended, its "extracted as the post says it" part superseded); 2026-10-30 (set
  v3). Recorded in the same PR.
- `docs/BACKLOG.md` — searched for "#55", "#60", "match check". #60 shapes the three test runs.
  #55 (fixtures of other competitions) stays.
- `docs/adr/` — read ADR 0010's thresholds section (five thresholds, the default and fallback
  models). The architecture B of the check stays; the default model is not re-chosen.
- `docs/CONVENTIONS.md` — searched for "Tests", "prompt". Prompts live in `app/content/prompts/`
  with a version; each version change gets a test.
- `docs/DEVELOPMENT.md` — searched for "extraction.evaluation", "reextract". The existing
  `evaluate`, `review`, `compare-labels` and `reextract` commands are used; no CLI change.
- `docs/EVALUATION.md` — searched for "The extraction procedure". Set v4 follows it: session
  judge, the owner's review of new and changed cases, paid runs only for the model under test.
- `docs/DEPLOYMENT.md` — searched for "LLM_MODEL". No variable or image change; does not apply.
- `docs/reports/` — read `extraction-eval-v3.md` (set composition, the absence checks, the
  one-answer-per-club limitation).

## Scope

- Extraction prompt v7: a rest, rotation or omission from another match's squad with no injury
  given gives no event.
- `match_check` v3: the check reads the post, the club and the fixtures without the extracted
  event types.
- Extraction set v4: set v3 plus the GW9 cases, with the wrong tag fixed.
- Three test runs of the default model and a report.
- A manual re-extraction of the current window by the owner after the merge.

## Out of scope

- Alert and corroboration rules: SPEC 021.
- A prompt rule for "not ready to start" (Kroupi): a single slip, kept as an evaluation case.
- A match check for `available` events: no GW9 case needs it.
- One answer per event instead of per post and club (report v3, "One answer per post and club"):
  stays; revisit when an injury is dropped beside a cup rest in production.
- Re-choosing the default or fallback model: the next model check.

## Requirements and acceptance criteria

### Extraction

- [ ] AC1: The extraction prompt is v7. A player rested, rotated or left out of a squad for a cup,
  European or national-team match with no injury or illness given gives no event, and so does a
  suspension served in such a match (v6 extracted it for the check). An injury or illness from any match keeps its `doubt` or
  `out`, and every other rule of v6 stays. A test checks the version and the rule's presence.
- [ ] AC2: Tested with a stubbed model through the flow and the store as today; the behaviour is
  measured on set v4 (AC7).

### Match check

- [ ] AC3: `match_check` is v3. Its input holds the post, the time, the club, the previous and next
  Premier League fixtures and the names of the club's players with checked events, with no event
  type. It answers the same question ("does the post's news about these players concern the club's
  next Premier League match?"), and its rules no longer depend on the event type. A test checks the
  rendered input holds no event type.
- [ ] AC4: The check still runs once per post and club for linked `out`, `doubt`, `benched` and
  `confirmed_starter` events, never for `available`; a false answer is dropped at read time, a
  null keeps the event (DECISIONS 2026-10-26). Tested by counting calls with a stubbed model.

### Evaluation set v4

- [ ] AC5: Set v4 (`backend/evals/extraction/v4/`) is set v3 plus new real cases from the GW9
  window, among them 2115825835525488887 (Kroupi.Jr, expected `benched`), 2116174660693836212 (FPL__Raptor),
  2116188146660409531 (FPL_Salah) and 2116265028458315935 (FPLMattW), the watched accounts' posts
  that quote the Manzambi and N.Jackson news (they replace 2116172084082602140 and
  2116226240323465435, whose authors are not on the watched list; owner decision 2026-11-01), and
  2116448660980732008 (a tips post, no event). Case 2115797596119740910 loses the `other_match_absence` tag. 2116079251476611126 stays
  in the test split. New cases are split like v3, about two thirds to test, and the composition
  rules of v3 still hold.
- [ ] AC6: The owner reviews every new and changed case with `review` before any test run
  (`review_source: owner`); the session judge pre-labels them.

### Measurement

- [ ] AC7: Dev iterations on the dev split only. The prompts `extraction@7`,
  `link_disambiguation@1`, `match_check@3` with `openai/gpt-6-luna` run three times on v4's test
  split. The mean of the three runs passes ADR 0010's five thresholds, and each run has at most 1
  `other_match_absence` false alarm and at most 1 injury dropped by the check.
- [ ] AC8: The report also gives, per run and as a mean, the `other_match_absence` test cases with
  an `out` or `doubt` event before the check (raw extraction), which must average at most 1,
  and the outcome of 2116079251476611126 in each run (raw and after the check).
- [ ] AC9: Paid API spend for this spec's runs stays at or under 5 USD; the judge is the Claude Code
  session; the spend is printed by `spend` and recorded in the report.
- [ ] AC10: `docs/reports/extraction-eval-v4.md` holds the set composition, the review counts, the
  dev iterations, the three test runs and their mean, and the spend. If the default model fails,
  the report says so and the spec stops for the owner's decision; it never switches the model.

### Documents and the owner's step

- [ ] AC11: `docs/DECISIONS.md` records prompt v7's rule and the blind match check v3, superseding
  the "extracted as the post says it" part of the 2026-10-30 match-check row, and set v4 with its
  three-run measurement. `docs/EVALUATION.md` names set v4. `docs/ROADMAP.md` gets a ticked Stage 2
  item linking this spec.
- [ ] AC12: After the merge the owner re-extracts the current alert window on the Compose database
  (`uv run python -m app.extraction reextract --since <the last deadline> --until <now>`) and checks
  with `app.alerts preview` that the FFScout rest post gives no absence. A manual procedure; the
  agent does not run it.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| A rest for another match gives no event in extraction (v7) and the check stays as a second barrier | the check alone (v6); prompt cases only, no prompt change | two independent barriers; v5 handled the FFScout post; the check no longer carries every rest alone |
| The match check reads no event types (v3) | the types in the input (v2); one answer per event | the type steered the answer (benched → false, out → true on the same post); the question is about the match, not the type |
| Three test runs and their mean | one run | one model's runs vary (BACKLOG #60) and v3 passed by 0.003 |
| Kroupi's "not ready to start" stays an evaluation case | a prompt rule | a single slip; two other accounts' posts were extracted right |

## Owner decisions

- 2026-11-01: a separate spec from 021, independent of it.
- Extraction v7 (rests give no event) together with the blind match check v3.
- Evaluation: set v4 (v3 plus the GW9 cases), three test runs of the default model, at most 5 USD,
  the session as judge.
- A manual re-extraction of the current window by the owner after the merge.
- No new dependency and no migration.

## Open questions (non-blocking)

- none
