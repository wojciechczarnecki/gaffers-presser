---
status: plan-approved
stage_history:
  - "spec-draft — 2026-10-25"
  - "spec-ready — 2026-10-25"
  - "plan-draft — 2026-10-25"
  - "plan-approved — 2026-10-25"
metrics:
  started_at: 2026-10-25T01:04
  plan_steps: 17
  plan_changes: 10
  escalations: 0
  escalations_permission: 0
  escalations_tooling: 0
  plan_review_blockers: 0
  plan_review_majors: 3
  implement_steps: 10
  implement_iterations: 12
  deviations_minor: 8
  deviations_major: 0
---

# SPEC 018 — Extraction evaluation v2: GW6 line-up leaks, prompt v5 and a match check

## Goal

Alerts should carry real pre-deadline line-up leaks and drop line-ups from other matches
(Champions League, cups, internationals), whatever the author's certainty. Today extraction is
measured on a set from before the first deadline it served, which has no real line-up leak.
Prompt v4 sometimes turns a European line-up into a `benched` or `confirmed_starter` event,
and the 60 h filter catches only the `confirmed` ones. It works when:

- a second, reviewed evaluation set with real GW6/GW7 leaks and other-match line-ups is in the
  repository;
- a match check after linking drops line-ups from other matches in production;
- the six models of ADR 0006 are re-compared on the new set with prompt v5;
- the default model is the one the rule picks, recorded in an ADR, with the report in
  `docs/reports/`.

## Context

- **The extraction flow** (`backend/app/extraction/flow.py`) is a LangGraph graph with two
  nodes:
  - extract: one LLM call returns, per mentioned player, the mention, the team, the event
    type and the certainty;
  - link: deterministic resolution to an FPL player through `PlayerIndex` and the aliases;
    a second LLM call (`link_disambiguation`) runs only when the name has several candidates.
  The prompt is `app/content/prompts/extraction.md`, version 4; the run records
  `extraction@4+link_disambiguation@1`. Events are stored in `extraction_event`
  (`app/extraction/models.py`).
- **Where v1 loses points.** On v1 (`docs/reports/extraction-eval-v1.md`) linking accuracy was
  1.00 for the default `openai/gpt-6-luna`. The F1 loss and the false alarms come from the
  decision whether a post holds an event and which one. Luna's latency was 1.2 s p50 and 2.5 s
  p95 per post, with about 1,040 input tokens.
- **The 60 h filter** (DECISIONS 2026-10-24, `LINEUP_MAX_LEAD` in `app/extraction/store.py`):
  `current_extractions(fresh_lineups_only=True)` drops a `confirmed` line-up event published
  more than 60 h before the club's next Premier League kickoff, at read time. The stored events
  are not changed. Alerts, breaking alerts, corroboration's SQL claims and the retrieval and
  corroboration evaluation builders read through it.
- **Evaluation set v1** (`backend/evals/extraction/v1/`):
  - 136 cases (107 real, 29 synthetic), all reviewed, posted 2026-09-25 to 2026-09-28;
  - split 91 test / 45 dev;
  - test events: 31 `doubt`, 23 `confirmed_starter`, 10 `out`, 9 `benched`;
  - pre-labelled by `anthropic/claude-haiku-4.5` and reviewed by the owner;
  - a players snapshot `players-2026-27.json`, with no fixtures.
  The six test runs of spec 005 cost about $0.29 in total.
- **The extraction CLI** (`python -m app.extraction`, `app/extraction/cli.py`, 637 lines) holds
  `reextract` beside the evaluation commands `snapshot-players`, `prelabel`, `evaluate`,
  `spend`, `compare-labels` and `review`. The run header, the atomic write and the overwrite
  guard already come from `app/core/eval_results.py` (spec 016).
- **The session judge** (`scripts/session_judge.sh`, `docs/EVALUATION.md`) knows the `presser`
  and `retrieval` schemas.
- **Retrieval set v2** (`backend/evals/retrieval/v2/corpus.jsonl`) holds 2,557 public posts of
  the watched list from 2026-09-18 to 2026-10-17: the GW6 and GW7 windows, and the three posts
  of BACKLOG #23.
- **The #54 posts of 2026-10-20** (City v AEK) are outside that corpus and only in the Compose
  database.
- **The corroboration part of the roadmap item** (BACKLOG #47 and the `contradicts` half of
  #12) goes to spec 019. Corroboration still reads extraction's events through
  `current_extractions`, so the match check reaches it without changes there.

## Read context

- `docs/ROADMAP.md` — read in full. The Stage 2 item "Extraction and corroboration evaluation
  v2 on the GW6 window" names BACKLOG #12, #23, #46 (the extraction entry point), #47 and #54
  and is planned as 018. This spec delivers its extraction half; the corroboration half becomes
  spec 019. The item comes after spec 017 (done) and before the README rewrite.
- `docs/PROJECT.md` — read in full. FR-1.2 and FR-1.3 (typed extraction, four event types)
  stay. FR-2.4 asks for an evaluation set for extraction, and the non-functional rule asks for
  evaluation before a stage is done. The 20 PLN a month budget bounds the extra call. Latency:
  the 60 s target covers detection only, and extraction comes on top.
- `docs/DECISIONS.md` — searched for "extraction", "threshold", "evaluation set", "line-up",
  "LINEUP_MAX_LEAD", "judge", "session judge", "ADR 0006", "reviewed" and "2026-10-2". What
  binds this spec:
  - 2026-09-28: the set format, the dev/test split, the thresholds and "the cheapest passing
    model is the default";
  - 2026-09-29: the event type describes the next Premier League match; OpenRouter only; the
    luna default with the gemini fallback and the pair rule;
  - 2026-09-26: a fake LLM in tests, and evaluations outside `pytest`;
  - 2026-10-15: one tracing path through `GenerationTracer`; the shared `eval_results` module;
    the evaluation's own entry point;
  - 2026-10-21: the retrieval relevance rule for other matches' line-ups;
  - 2026-10-24: the 60 h filter at read time, with the stored events unchanged.
- `docs/BACKLOG.md` — read in full. #12 (extraction half), #23, #46 and #54 are delivered here.
  #47 and the `contradicts` half of #12 go to spec 019. #55 (other competitions' fixtures)
  stays the fallback with its trigger. #24 (a positive availability event) and #29 (quoted
  posts) are not touched.
- `docs/adr/` — read in full: 0006 (default extraction model, the rule, the pair rule, the
  thin margin on false alarms) and 0008 (orchestration in plain Python, LLM steps as typed
  LangGraph nodes). Searched 0009 for "ADR format".
- `docs/CONVENTIONS.md` — searched for "migration", "alembic", "eval" and "prompt". Prompts
  live in `app/content/`, and evaluation sets run through their own command, never inside
  `pytest`.
- `docs/EVALUATION.md` — read in full. The session judge rules (versioned prompt, a fresh
  context per call, a validated schema, the judge named, an estimate before the run), the
  retrieval procedure as the pattern, and "Other evaluations", which says extraction has no
  export or import yet.
- `docs/DEPLOYMENT.md` — searched for "migration", "alembic" and "LLM_MODEL". The new migration
  follows the existing numbered-migration notes. `LLM_MODEL` keeps its meaning. Railway is
  postponed, so the owner migrates Compose.
- `docs/reports/` — read in full: `extraction-eval-v1.md` (thresholds, composition, latency,
  noise on 91 cases). From `code-audit-2026-10.md`, read A4/D2/D3 (the extraction entry point
  is planned for spec 017, now 018) and A5 (corroboration, for spec 019). From
  `retrieval-eval-v2.md`, searched for "corpus" and "window".

## Scope

- **Its own entry point** (BACKLOG #46). `python -m app.extraction.evaluation` takes every
  evaluation command (`snapshot-players`, `prelabel`, `evaluate`, `spend`, `compare-labels`,
  `review`, and the new ones below). `python -m app.extraction` keeps `reextract`. The README
  and `docs/EVALUATION.md` show the new commands.
- **The match check** (BACKLOG #54, architecture B). A third LangGraph node runs after linking:
  1. It runs only for posts with at least one linked `benched` or `confirmed_starter` event.
  2. It makes one LLM call per post and club. The call gets:
     - the post's text and time;
     - the club from the FPL data;
     - the club's previous and next Premier League fixture around the post's time (opponent,
       home or away, kickoff).
  3. It answers per club whether the line-up is for that next Premier League match.
  4. The answer is stored per event in a new nullable column of `extraction_event` (a
     migration). It is `null` for:
     - events other than line-ups;
     - events whose player is not linked;
     - a club with no known fixture;
     - a failed call (fail-open: the event stays and the failure is traced as a generation);
     - rows extracted before this spec.
  5. `current_extractions(fresh_lineups_only=True)` also drops a line-up event whose stored
     answer is false, in the same place as the 60 h filter, which stays.
  6. The step has its own versioned prompt in `app/content/prompts/` and its version in the run
     header and in `extraction.prompt_version`.
- **Extraction prompt v5** (BACKLOG #23). These posts give no event:
  - an FPL manager writing about his own team or a hypothetical (@FPL_Salah on João Pedro, x_id
    2104646876389650860);
  - national-team "should be ready to go and very likely to start" (@BenDinnery on Guéhi, x_id
    2106010889262436608).

  A post listing players "injured or yellow flagged" restates the official FPL flags and is
  never `confirmed` (@FPLGOAT7, x_id 2104158837540622748). The prompt is changed on the dev
  split only.
- **Evaluation set v2** in `backend/evals/extraction/v2/`. It holds:
  - set v1's cases, relabelled under the v5 rules;
  - about 80 new real cases from the GW6 and GW7 windows of the retrieval v2 corpus, weighted
    towards pre-deadline line-up leaks and other matches' line-ups;
  - the three #23 posts and the #54 posts. The 2026-10-20 ones come from one read-only read of
    the Compose database, which the owner approved;
  - a players snapshot;
  - a fixtures snapshot holding the Premier League fixtures the match check needs.

  The split follows v1's rule (`x_id % 10 < 3` → dev), the backlog's posts included. A new tag marks other-match line-ups,
  which expect no event.
- **The session judge for extraction.** Extraction cases can be pre-labelled by the Claude Code
  session judge instead of a paid model:
  - the extraction evaluation exports one input per post;
  - `scripts/session_judge.sh` takes an `extraction` schema;
  - an import validates the verdicts into candidate cases;
  - an estimate of calls and tokens comes before any call (`docs/EVALUATION.md`, lesson 1).

  The relabelling of v1 uses the same path. The owner reviews only the v1 cases whose label
  changed, and every new case.
- **Metrics.** `evaluate` runs the whole flow (extract → link → match check) and scores the
  events the read-time filter would keep. Besides the existing metrics it reports two numbers:
  - the other-match line-up false alarms: events surviving in cases with the new tag;
  - the real line-ups the match check dropped.
- **The re-comparison.** On v2's test split with the frozen prompt v5, each of the six models
  of ADR 0006 runs the whole flow:
  - `openai/gpt-6-luna`, `qwen/qwen3.8-flash`, `z-ai/glm-5.3-flash`,
    `google/gemini-3.1-flash-lite`, `deepseek/deepseek-v4-flash`, `anthropic/claude-haiku-4.5`;
  - every step of the flow uses the model under test;
  - each model gets one test run, and a run with an errored case gets one repeat.
- **The default revisited.** The cheapest model passing every threshold wins. If none passes,
  the winner is the model passing the most thresholds, then the higher F1, among the models at
  or under 5 PLN a month. The fallback must be pair-compatible with the winner (ADR 0006).
  `LLM_MODEL`'s default and the fallback follow the result.
- **Documents.**
  - a report `docs/reports/extraction-eval-v2.md`;
  - an ADR for the match check and the default model, superseding ADR 0006's default where it
    changes;
  - DECISIONS rows;
  - the README's extraction section and results;
  - the roadmap item split into its 018 and 019 halves and ticked for 018;
  - BACKLOG #12 narrowed to its corroboration half, and #23, #46 and #54 removed.

## Out of scope

- The corroboration judge: the owner's review of set v1, a pass threshold, `contradicts`
  cases and the re-run on the new retrieval (BACKLOG #47 and the corroboration half of #12).
  These go to spec 019, which follows this one.
- Re-extracting the stored production posts with prompt v5 and the match check. That is the
  owner's operation (`reextract` on the Compose database). Old rows keep `null` and the 60 h
  filter.
- The fixtures of other competitions (BACKLOG #55). It stays P3 with its trigger.
- Splitting extraction into separate "who" and "what" calls (architecture C), and the fixtures
  table in the main prompt (architecture A). Both are rejected below.
- A positive availability event type (BACKLOG #24) and the quoted post's text (BACKLOG #29).
- Changing the thresholds of DECISIONS 2026-09-28.
- New candidate models beyond the six.

## Requirements and acceptance criteria

### Entry point

- [ ] AC1: `uv run python -m app.extraction.evaluation --help` lists `snapshot-players`,
  `prelabel`, `evaluate`, `spend`, `compare-labels`, `review` and the new export, import and
  estimate commands. `uv run python -m app.extraction --help` lists `reextract`, and none of
  the evaluation commands.
- [ ] AC2: The README and `docs/EVALUATION.md` show only the new module path for the
  extraction evaluation commands. A grep for `python -m app.extraction evaluate|review|prelabel`
  in the repository's Markdown finds nothing.

### Match check

- [ ] AC3: Given a post with linked line-up events of one club (fake LLM in a test), the flow
  makes exactly one match-check call for that club. A post with no line-up event makes none.
  A post with line-up events of two clubs makes two.
- [ ] AC4: The match-check input holds the post's text and time, the club's name and its
  previous and next Premier League fixtures relative to the post's time (opponent, venue,
  kickoff). It takes them from the fixture data, not from the model's output (a test asserts
  the rendered input).
- [ ] AC5: The answer is stored on each of that club's line-up events of the post. Other event
  types, unlinked players and clubs with no fixture after the post get `null` and no call.
- [ ] AC6: A match-check call that raises or returns no valid answer leaves the events stored
  with `null`, and the extraction stays `extracted`. The failure is traced as a generation
  with its error class.
- [ ] AC7: `current_extractions(fresh_lineups_only=True)` drops a line-up event whose stored
  answer is false, whatever its certainty. It keeps one with `null` or true, subject to the
  60 h filter as today. With `fresh_lineups_only=False` every event is returned.
- [ ] AC8: The migration adds the nullable column, and leaves existing rows `null`. Upgrade
  and downgrade run on the test container. The migration is not run on Compose by the agent.
- [ ] AC9: `extraction.prompt_version` and the evaluation run header name all three prompt
  versions (extraction 5, link disambiguation, match check 1).
- [ ] AC10: The match-check prompt lives in `app/content/prompts/` with a version line, and no
  prompt text is a string literal in code.

### Prompt v5 and set v2

- [ ] AC11: `extraction.md` has version 5 with the three #23 rules. The three #23 posts are
  cases in set v2 with the expected labels above:
  - no event for 2104646876389650860 and 2106010889262436608;
  - no `confirmed` certainty for 2104158837540622748.
- [ ] AC12: Set v2 has every v1 case. Each relabelled case's change is listed by
  `compare-labels` against v1, and every case is `reviewed`. `evaluate` on v2 refuses to run
  while any case of the split is unreviewed, as today.
- [ ] AC13: Set v2 meets its composition check, run as a command or a test:
  - every v1 rule;
  - at least 70 new real cases from 2026-10-01 to 2026-10-17;
  - at least 15 `benched` or `confirmed_starter` events in the test split from the GW6 and
    GW7 windows;
  - at least 8 cases with the new other-match tag, at least 5 of them in test, none with an
    expected event;
  - the #54 posts named in BACKLOG #54 among the tagged cases where they are line-ups.
- [ ] AC14: Set v2 has a fixtures snapshot. `evaluate` needs no database: it reads cases,
  players and fixtures from the set directory.
- [ ] AC15: The prompt work is done on the dev split only. The report lists every dev iteration
  with its F1 and false-alarm rate, and the test runs use one frozen prompt version.

### Session judge for extraction

- [ ] AC16: The export writes one input per post: the judge prompt and the post with its
  fixtures context. Inputs are named by neutral IDs, with the mapping kept outside the inputs
  directory. An estimate command prints the calls and the tokens before any call.
- [ ] AC17: `scripts/session_judge.sh DIR N MODEL extraction` writes a verdict per input, and
  moves an invalid one to `raw/<id>.invalid.json` as for the other schemas. The import
  validates every verdict against the extraction output schema and refuses an invalid file.
- [ ] AC18: Imported cases are `reviewed: false` with the judge named in the set's metadata.
  `review` shows them for the owner as it shows today's cases.

### Metrics and the comparison

- [ ] AC19: `evaluate` scores the events that `fresh_lineups_only=True` would keep, with the
  same predicate as production (one function, used by both). It also reports
  `other_match_false_alarms` (count and rate over the tagged cases) and
  `lineups_dropped_by_check` (expected line-up events the check answered false).
- [ ] AC20: The six models each have a test result file on v2 with a run header (set `v2`,
  model, the three prompt versions, reasoning effort, commit), and each step of the flow ran with the model under test. Total paid spend of the spec's
  runs (dev and test, by `spend`) stays at or under $5.
- [ ] AC21: The default model and fallback are the ones the rule in Scope gives. They are
  recorded in the ADR with the full table. `LLM_MODEL`'s default and `LLM_FALLBACK_MODEL`'s
  default in code match the ADR, and a test pins them.
- [ ] AC22: `docs/reports/extraction-eval-v2.md` gives:
  - the set's composition;
  - the dev iterations;
  - the test table per model: F1, linking, false alarms, the two new metrics, cost a month,
    latency p50/p95 with the match check included;
  - how many tagged cases the 60 h filter alone would have caught;
  - the threats to validity, including the Claude pre-labels against `claude-haiku-4.5` as a
    candidate.

### Documents and the iron rules

- [ ] AC23: DECISIONS has rows for:
  - the match check and its storage;
  - the fail-open rule;
  - set v2 and the session pre-labels;
  - the default model and the "no model passes" rule.

  An ADR records the match-check architecture against alternatives A, C and D.
- [ ] AC24: The ROADMAP item is split into an 018 item (ticked, linked to this SPEC) and a 019
  item (open). In BACKLOG, #23, #46 and #54 are removed and #12 keeps only its corroboration
  half.
- [ ] AC25: The full verification command passes. No real manager or league data is in a
  case, a test or a log line (the cases hold only public posts of the watched accounts).

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Spec 018 covers extraction (#12 extraction half, #23, #46, #54); the corroboration judge (#47, `contradicts`) is spec 019 | one spec for the whole roadmap item; three specs (tooling / run / judge) | the two evaluations share no set, prompt or metric; the judge needs a long review by the owner; one spec would exceed 016 and 017 combined |
| Architecture B: a match-check node after linking, one call per post and club with the club's previous and next PL fixture | A: a 20-club fixtures table in the main prompt; C: separate "who" and "what" calls for every post; D: the LLM extracts the named match, code decides | B gives the model the club from FPL data instead of its memory, costs a call only for line-up posts (one per club, not per player), and is measurable on its own; A burdens every call and leaves the club to the model; C fixes linking, which already scores 1.00, and doubles the calls and latency of every post; D fails when the post does not name the match, the typical case ("Haaland on the bench" during City v AEK) |
| The answer is stored in a nullable column and filtered at read time beside the 60 h filter (a migration) | dropping events before storing (no migration) | the stored events stay unchanged and filtered at read (DECISIONS 2026-10-24); a trace in `review` and in SQL; the rule can be switched off without re-extraction; the owner prefers the better design over the simpler one |
| The 60 h filter stays | removing it once the check works | rows extracted before v5 have `null` and depend on it; it is a cheap net under the check |
| A failed match check is fail-open (`null`, the event kept) | the whole extraction failed and retried | the same as a failed disambiguation today; a real leak is never lost to a provider error, and `confirmed` line-ups still meet the 60 h filter |
| New cases come from the retrieval v2 corpus already in the repository, plus one approved read-only read for the posts after 2026-10-17 | the Compose database as the source; only the backlog's posts | no reading of production for the bulk, and the same corpus the retrieval evaluation used |
| Pre-labels by the Claude Code session judge; v1 relabelled the same way, and the owner reviews only the changed labels and the new cases | a paid model as in v1 (`claude-haiku-4.5`); keeping v1's labels; a full re-review of v1 | no paid-API spend; v2 stays consistent with the v5 rules without a 136-case re-review; the vendor overlap with the haiku candidate is reported, as in v1 |
| About 80 new real cases | about 40; about 150 | v1's test has only 9 `benched` events; 80 gives at least 15 line-ups in test at a review the owner can do in one sitting |
| The same six candidates | the six plus newer models; default and fallback only | directly comparable with ADR 0006 at about $0.3 a run set |
| With no passing model, the one passing most thresholds then higher F1 wins, and the cost (≤ 5 PLN a month) is a hard limit | keeping luna regardless; escalating to the owner; cost as just one threshold | the owner's choice; the same rule as ADR 0006's fallback; 5 PLN is a quarter of the monthly budget |

## Owner decisions

- Scope split: 018 is extraction, and 019 is the corroboration judge (2026-10-25).
- Migration accepted up front: a nullable column on `extraction_event` for the match-check
  answer. The owner runs `alembic upgrade head` on Compose (2026-10-25).
- A read-only read of the Compose database is approved for the #54 posts after 2026-10-17 (and
  the snapshots the set needs) (2026-10-25).
- No new dependency is expected. If one turns out to be needed, it is an escalation.
- Paid API spend for the spec's model runs: at most $5 (standing rule). The session judge runs
  on the Claude subscription, after an estimate.

## Open questions (non-blocking)

- The Claude model and effort for the session pre-labels (Opus, or Sonnet after a small
  pilot as in spec 017). The plan decides, from the estimate.
- Whether the owner re-extracts the stored GW8+ posts with v5 after the merge. This is outside
  the spec; the report can state the expected effect.
