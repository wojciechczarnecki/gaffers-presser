---
status: plan-draft
stage_history:
  - "spec-draft — 2026-10-28"
  - "spec-ready — 2026-10-28"
  - "plan-draft — 2026-10-28"
metrics:
  started_at: 2026-10-28T10:42
  plan_steps: 15
  escalations: 0
  escalations_permission: 0
  escalations_tooling: 0
---

# SPEC 019 — Corroboration evaluation v2: a reviewed judge set, contradictions and a pass threshold

## Goal

The corroboration judge decides which retrieved posts count as independent supporting or
contradicting accounts, and those counts set the grade friends see in an alert. Today nobody
knows how good it is. Its only set (v1) has no reviewed case, no `contradicts` case and no
pass threshold, and the only run scored the judge against another model's labels.

It works when:

- a set v2 is in the repository, reviewed case by case by the owner. It holds set v1 and new
  GW6/GW7 cases drawn by the production retrieval, real contradictions among them;
- the judge has a pass threshold, set before the run;
- the six candidate models are compared on v2's test split and the judge's default model is
  the one the rule picks;
- a report in `docs/reports/` gives the results.

## Context

- **The judge** (`backend/app/corroboration/judge.py`) is a one-node LangGraph step. The
  prompt is `app/content/prompts/corroboration_judge.md`, version 1, recorded as
  `corroboration_judge@1`.
  - It labels a retrieved post `supports`, `contradicts`, `related` or `unrelated` against the
    anchor, the player's newest current event in the window.
  - It sees only retrieved posts that carry no current event for the player, at most 10 per
    corroboration (`MAX_JUDGED`).
  - Posts with an extracted event are labelled by a fixed table (`label_claim` in `rules.py`).
  - The judge runs on `LLM_MODEL`, the same setting as extraction (default `openai/gpt-6-luna`,
    `app/llm/chat.py`).
- **How the labels reach an alert** (`rules.py`):
  - each `supports` adds an independent account, and 1 or 2 accounts lift the grade to medium
    or high;
  - a new or newer `contradicts` lowers the grade one step.

  A false `supports` therefore inflates a grade, and a missed `contradicts` leaves a grade too
  high.
- **The evaluation** (`app/corroboration/evaluation/`, entry point
  `python -m app.corroboration.evaluation`) has three commands:
  - `build-cases`: builds candidates from the live `tweet` table through `retrieval_candidates`
    plus up to 3 SQL-context claims per anchor, pre-labels them with a paid model, selects
    50–70 cases with a cap on `unrelated` and assigns a seeded dev/test split per label;
  - `review`: accept or change each label;
  - `evaluate`: accuracy, per-label precision and recall, and the false-support rate. That is
    the share of cases expected `unrelated`, `related` or `contradicts` that the judge called
    `supports`.

  Two rules in `composition_problems` matter here:
  - it forbids pre-labels by the judge's own model;
  - it exempts `contradicts` through `LABELS_NOT_YET_REQUIRED`.
- **Set v1** (`backend/evals/corroboration/v1/cases.jsonl`):
  - 51 cases from posts of 2026-09-25 to 2026-09-28;
  - split 15 dev / 36 test;
  - 24 `unrelated`, 16 `supports`, 11 `related` and no `contradicts`;
  - anchors: 42 `doubt`, 8 `confirmed_starter`, 1 `benched`;
  - 7 cases are SQL-context pairs (`has_player_event: true`), which in production go to the
    fixed table, not to the judge;
  - every case is pre-labelled by `anthropic/claude-haiku-4.5` and none is reviewed.
- **The only result so far** is `results/test-openai-gpt-6-luna.json` (2026-09-30), run with
  `include_unreviewed: true` on the old retrieval: accuracy 0.78 and false-support 12% (3/25).
- **The retrieval chosen in spec 017** (DECISIONS 2026-10-22, ADR 0009):
  `openai/text-embedding-3-large`, `SEARCH_MODE = "hybrid"`, `QUERY_VARIANT = "names"`,
  `SEARCH_LIMIT = 20`, `MAX_JUDGED = 10`. Set v1 was built with the earlier configuration.
- **Spec 018** delivered extraction prompt v5, the match check (migration 0014) and extraction
  set v2.
  - Its 228 cases show at least six players with a line-up event against an availability event
    inside one GW6/GW7 window (FPL IDs 328, 420, 428, 429, 431, 208). So real contradictions
    exist in that period.
  - Retrieval set v2's corpus (`backend/evals/retrieval/v2/corpus.jsonl`, 2,557 public posts
    of the watched List, 2026-09-18 to 2026-10-17) covers the GW6 and GW7 alert windows.
- **The session judge** (`scripts/session_judge.sh`, `docs/EVALUATION.md`) knows the
  `presser`, `retrieval` and `extraction` schemas. Extraction (spec 018) added the export,
  estimate and import pattern that the corroboration pre-labels follow here.

## Read context

- `docs/ROADMAP.md` — read in full. The Stage 2 item "Corroboration evaluation v2" names
  BACKLOG #12 (the corroboration half) and #47, and is spec 019. It follows spec 018 (done) and
  comes before the README rewrite, which waits for this report's results.
- `docs/PROJECT.md` — searched for "corrobor", "FR-2", "budget" and "evaluation":
  - FR-2.2 corroboration and FR-2.4 evaluation sets;
  - every LLM step that produces data has an evaluation set before its stage is done;
  - the budget is ≤ 20 PLN a month;
  - line 87: `LLM_MODEL` serves extraction and the corroboration judge. A separate judge
    setting changes this line.
- `docs/DECISIONS.md` — searched for "corrobor", "judge set", "session judge", "threshold" and
  "pass". What binds this spec:
  - 2026-09-30: the judge's narrow role, the set format (JSONL, dev/test, a pre-label by
    another model than the judge, the owner's review) and the anchor's own account never
    counting;
  - 2026-09-28: the extraction thresholds and "the cheapest passing model is the default" as
    the pattern;
  - 2026-10-15: the shared `eval_results` module and the evaluation's own entry point;
  - 2026-10-22: the production retrieval configuration;
  - 2026-10-24: `fresh_lineups_only` in the builders;
  - 2026-10-26: extraction set v2 pre-labelled by the session judge.
- `docs/BACKLOG.md` — read in full. #12 and #47 are delivered here and their rows removed. Two
  rows stay untouched:
  - #19 (aggregators) and #29 (the quoted post's text), each with its trigger;
  - #33 (parallel judge calls) and #49 (corroboration as a graph).
- `docs/adr/` — read in full: ADR 0006 (the candidates, the rule and the pair rule). Read in
  part:
  - ADR 0009, its decision and rule;
  - ADR 0010, the default after spec 018.
- `docs/CONVENTIONS.md` — searched for "eval" and "threshold". Evaluation sets run through
  their own command, never inside `pytest`, and prompts live in `app/content/`.
- `docs/EVALUATION.md` — searched for headings and for "corroboration" and "judge". The session
  judge rules (a fresh context per item, a validated schema, the judge named, an estimate
  first) and the extraction procedure are the pattern. "Other evaluations" mentions
  corroboration.
- `docs/DEPLOYMENT.md` — not read. The spec changes no image, `railway.json` or deployment
  step. A new judge-model variable, if the rule picks a model other than luna, would be
  documented there by the plan.
- `docs/reports/` — searched `code-audit-2026-10.md` for A5. Read the summaries of
  `extraction-eval-v2.md` and `retrieval-eval-v2.md` (the corpus and the GW6/GW7 windows).

## Scope

- **Set v2** in `backend/evals/corroboration/v2/`:
  - every v1 case, with v1's split kept;
  - about 50 new cases from the GW6 and GW7 alert windows. They are built the way corroboration
    runs in production: the anchors are current events under `fresh_lineups_only=True`, and the
    candidates come from `retrieval_candidates` with the 2026-10-22 configuration;
  - SQL-context pairs, kept as in v1 and marked `has_player_event`.

  The selection favours `contradicts` and known misses.
- **The source of the new cases.** The new cases come from a test container, never from the
  Compose database:
  1. the retrieval v2 corpus is loaded into the `tweet` table;
  2. the posts are embedded with `text-embedding-3-large`;
  3. the posts are extracted with prompt v5 and the match check (the production default
     model), so the anchors and the claimed posts are what production would have had.

  The paid spend of this build is reported.
- **Real contradictions.** The target is at least 8 `contradicts` cases in the set and 5 in
  test. If the windows give fewer from retrieved posts, real SQL-context pairs fill the gap.
  Those pairs are marked `has_player_event`. No synthetic case is added.
  `LABELS_NOT_YET_REQUIRED` goes, so the composition check requires `contradicts`.
- **Pre-labels by the Claude Code session judge.**
  - The new cases and a blind relabel of every v1 case go through `scripts/session_judge.sh`
    with a new `corroboration` schema: an export with neutral IDs, an estimate of calls and
    tokens before any call, and an import that validates every verdict.
  - Each case keeps its pre-label and who gave it. A v1 case also keeps haiku's label, so the
    two can be compared.
  - No paid pre-labelling model runs.
- **The owner's full review.** The owner reviews every case of v2.
  - `review` shows first the cases where the two pre-labels disagree.
  - `evaluate` refuses a split with an unreviewed case unless `--include-unreviewed` is passed,
    as today.
- **A pass threshold, set here before any run.** On v2's test split, in a run with no errored
  case, the judge passes with:
  - accuracy ≥ 0.80;
  - a false-support rate ≤ 10%;
  - `contradicts` recall ≥ 0.70.

  A run with an errored case gets one repeat.
- **Metrics.** `evaluate` reports the existing metrics on the whole split and again on the
  production slice, the cases with `has_player_event: false`. The threshold applies to the
  whole split. The slice is reported only, because it is too small for its own threshold.
- **The comparison.** On v2's test split, each of the six models of ADR 0006 runs as the judge:
  `openai/gpt-6-luna`, `qwen/qwen3.8-flash`, `z-ai/glm-5.3-flash`,
  `google/gemini-3.1-flash-lite`, `deepseek/deepseek-v4-flash`, `anthropic/claude-haiku-4.5`.
  Each model gets one run, plus the repeat above. Each result file has the run header from
  `eval_results`.
- **The judge's default model.** The cheapest model passing every threshold wins. If it is not
  the extraction default, the judge gets its own model setting with that default, and
  `LLM_MODEL` keeps serving extraction. If no model passes, the rule in the Decisions table
  picks the winner.
- **Prompt work.** The judge prompt may change only on the dev split, and is frozen before any
  test run. The report lists every dev iteration.
- **Documents:**
  - a report `docs/reports/corroboration-eval-v2.md`;
  - DECISIONS rows;
  - an ADR when the judge's default model or setting changes;
  - the README's corroboration section and results;
  - `docs/EVALUATION.md` with the corroboration procedure;
  - the ROADMAP item ticked;
  - BACKLOG #12 and #47 removed.

## Out of scope

- Changing the grade rules (`GradeRules`, the lowering rule) or the fixed label table. The
  report may recommend a change, which would go to the backlog.
- Aggregators and shared sources (BACKLOG #19), the quoted post's text (#29), parallel judge
  calls (#33) and corroboration as a graph (#49). Each keeps its trigger.
- Re-indexing or re-extracting the Compose database. That is the owner's operation.
- A positive availability event type (BACKLOG #24), which needs its own spec.
- An end-to-end evaluation of the grade itself (anchor → grade). This spec measures the judge's
  labels only.
- New candidate models beyond the six.

## Requirements and acceptance criteria

### Set v2

- [ ] AC1: `backend/evals/corroboration/v2/cases.jsonl` holds every v1 case by ID, with the same
  split, and at least 45 new cases. Every new case's post and anchor were created inside the
  GW6 or GW7 alert window. The windows are computed by the production window function from the
  corpus's fixtures.
- [ ] AC2: The composition check passes on v2 with `LABELS_NOT_YET_REQUIRED` removed:
  - every label, `contradicts` included, is present in the set and in the test split;
  - at least 8 `contradicts` cases in the set and at least 5 in test;
  - no pre-label by the judge's default model;
  - at least one known miss;
  - between 90 and 130 cases.

  A test covers the check with a set missing `contradicts`.
- [ ] AC3: The new cases are built in a test container from the retrieval v2 corpus, with the
  2026-10-22 retrieval configuration and anchors from prompt v5 with the match check under
  `fresh_lineups_only=True`. No command of the build reads or writes the Compose database. The
  paid spend of the build (embeddings and extraction) is recorded in the set's metadata and in
  the report, and stays at or under $1.
- [ ] AC4: Every case records:
  - its pre-label and who gave it;
  - for a v1 case, haiku's original label too;
  - `reviewed`;
  - `has_player_event`.

  The set's metadata names the session judge, its prompt version and the git commit, as
  extraction set v2 does.

### Session judge pre-labels

- [ ] AC5: An export writes one input per case: the judge prompt, and the case rendered by the
  same `render_input` production uses. Inputs are named by neutral IDs, and the mapping is kept
  outside the inputs directory. An estimate command prints the calls and tokens before any
  call.
- [ ] AC6: `scripts/session_judge.sh DIR N MODEL corroboration` writes one verdict per input. An
  invalid verdict is moved to `raw/<id>.invalid.json`, as for the other schemas. The import
  validates every verdict against the judge's output schema, refuses an invalid file and
  leaves the imported cases `reviewed: false`.

### The owner's review

- [ ] AC7: `review` on v2 shows first the cases whose two pre-labels disagree, and shows both
  labels for a v1 case. Accepting or changing a label marks the case reviewed and saves it at
  once, as today.
- [ ] AC8: Before any test run of the comparison, every case of v2 is `reviewed: true`. The
  report states how many of the owner's labels differ from each pre-labeller.

### Threshold and metrics

- [ ] AC9: `evaluate` prints whether the run passes each threshold (accuracy ≥ 0.80,
  false-support ≤ 10%, `contradicts` recall ≥ 0.70, no errored case) and overall. The verdict
  is written to the result file. A test covers a pass, a failure on each threshold and an
  errored run.
- [ ] AC10: `evaluate` reports the metrics on the whole split and on the production slice
  (`has_player_event: false`), and writes both to the result file.

### Comparison and the default

- [ ] AC11: Each of the six models has a test result file on v2 with a run header (set `v2`,
  model, prompt version, reasoning effort, commit). The total paid spend of the spec, comprising
  the build, the dev runs and the test runs, stays at or under $5.
- [ ] AC12: The judge's default model is the one the rule gives:
  - if it is the extraction default, the judge keeps `LLM_MODEL` and nothing in the
    configuration changes;
  - otherwise the judge reads its own model setting, with that default, and extraction keeps
    `LLM_MODEL`.

  A test pins the judge's default model. The setting and its documentation
  (README, `docs/DEPLOYMENT.md`, PROJECT line 87) change only in the second case.
- [ ] AC13: If the prompt changed, its version is bumped and the prompt is frozen before the test
  runs. All six test runs use one prompt version. The report lists the dev iterations with
  their metrics.

### Report and documents

- [ ] AC14: `docs/reports/corroboration-eval-v2.md` gives:
  - the set's composition (labels by split, `has_player_event`, the source window);
  - the owner's agreement with each pre-labeller;
  - the test table per model: accuracy, per-label precision and recall, false-support, the
    production slice, the threshold verdict and the cost per month at production volume;
  - the winner by the rule;
  - the threats to validity: set size, vendor overlap between the Claude session pre-labels and
    the haiku candidate, the SQL-context pairs.
- [ ] AC15: DECISIONS has rows for set v2 (source, session pre-labels, full review), the pass
  threshold and the judge's default model. An ADR is added when the judge's default model or
  setting changes.
- [ ] AC16: The ROADMAP item "Corroboration evaluation v2" is ticked and linked to this SPEC. In
  BACKLOG, #12 and #47 are removed. The README's corroboration section no longer says the set
  waits for review, and links the report. `docs/EVALUATION.md` gains a corroboration
  procedure.
- [ ] AC17: The full verification command passes. No real manager or league data is in a case,
  a test or a log line: the cases hold only public posts of the watched accounts. The Compose
  database is neither read nor written.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Set v2 = v1 (reviewed, split kept) + ~50 new GW6/GW7 cases drawn by the production retrieval | review v1 and add a few contradictions by hand; a new set from GW6/GW7 only | the judge's set must match what it sees today (the 017 retrieval); v1 keeps its pre-GW6 variety and comparability; the owner's choice |
| New cases built in a test container from the retrieval v2 corpus with embeddings and prompt v5 extraction | one read-only read of the Compose database; only the posts already in extraction set v2 | no production access, reproducible, the same corpus as specs 017 and 018; Compose holds prompt v4 events and may not be re-indexed to `3-large`; extraction set v2 is too narrow for retrieval candidates |
| Pre-labels by the Claude Code session judge, v1 relabelled blind, haiku's v1 label kept | a paid haiku pre-label as in v1 | no paid spend; two pre-labels on v1 show where the review matters; as in specs 017 and 018 |
| The owner reviews every case | disagreements plus a stratified sample (spec 017) | the set is small (~100 short pairs, ~1–1.5 h) and is the gold standard behind the threshold (audit A5) |
| Threshold: accuracy ≥ 0.80, false-support ≤ 10%, `contradicts` recall ≥ 0.70, no errored case | stricter (0.85 / 5% / 0.80); false-support alone | a false `supports` inflates the grade (the worst error) and a missed `contradicts` blocks the lowering; with ~45 at-risk test cases 5% is two errors, within noise; luna scored 12% against haiku labels |
| Too few real contradictions → real SQL-context pairs fill in | synthetic paraphrases; as many as there are | real posts only; such pairs are already in v1 and the judge labels them correctly in principle, even though production uses the table for them |
| SQL-context pairs stay under the threshold, and the production slice is reported apart | threshold on the production slice only; remove them | the production slice is too small for its own threshold; the report shows both |
| Six ADR 0006 candidates, the cheapest passing wins; a separate judge setting only if the winner is not the extraction default | luna only; others only when luna fails | a run costs about $0.003, the table serves the portfolio, the same rule as extraction |
| With no passing model, the one passing the most thresholds wins, then the lower false-support rate, among models at or under 5 PLN a month | keep luna; escalate to the owner | the same shape as spec 018's rule, ordered by the worst error |

## Owner decisions

- Set v2 shape: v1 plus about 50 new GW6/GW7 cases drawn by the production retrieval
  (2026-10-28).
- The new cases come from a test container built from the retrieval v2 corpus. The Compose
  database is not read (2026-10-28).
- Pre-labels by the session judge, with v1 relabelled blind (2026-10-28).
- A full review of every case by the owner (2026-10-28).
- Threshold: accuracy ≥ 0.80, false-support ≤ 10%, `contradicts` recall ≥ 0.70 (2026-10-28).
- Comparison: the six ADR 0006 candidates, and the cheapest passing becomes the judge's default
  (2026-10-28).
- Too few contradictions: fill in with real SQL-context pairs. Those pairs stay under the
  threshold, with the production slice reported apart (2026-10-28).
- No new dependency and no migration expected. Either one, if needed, is an escalation.
- Paid API spend: at most $5 for the spec, of which at most $1 for the build's embeddings and
  extraction (standing rule). The session judge runs on the Claude subscription, after an
  estimate.

## Open questions (non-blocking)

- The Claude model and effort for the session pre-labels (Opus, or Sonnet after a pilot on v1's
  dev split as in spec 018). The plan decides, from the estimate.
- The name of the judge's own model setting, if one is needed. The plan proposes one that
  follows the `LLM_*` naming.
