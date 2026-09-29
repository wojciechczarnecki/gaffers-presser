---
status: plan-draft
stage_history:
  - "spec-draft — 2026-09-29"
  - "spec-ready — 2026-09-29"
  - "plan-draft — 2026-09-29"
metrics:
  started_at: 2026-09-29T12:47
  escalations: 0
  plan_steps: 18
---

# SPEC 005 — Extraction model comparison and OpenRouter as the only LLM provider

## Goal

Choose the default extraction model from measured results rather than guesswork, and leave
extraction ready to switch on in production with one key: OpenRouter as the only LLM
provider, a default model named in ADR 0006, and a fallback model for when the default
fails. We know it works when the report on the reviewed test split names the cheapest model
that passes the thresholds (or an interim default with the reason), the configuration uses
that model, and the owner's worker run shows one Langfuse trace per new post.

## Context

- Follow-up to spec 004 (`specs/004-tweet-extraction/`): AC26/AC27 and plan step 22 were
  descoped by the owner on 2026-09-28 into BACKLOG #14. Spec 004 delivered the flow, the
  worker loop, the `reextract` / `prelabel` / `evaluate` commands, the metrics
  (`backend/app/extraction/evaluation/metrics.py`, `passes` already requires zero errored
  cases) and the unreviewed evaluation set v1.
- **Precondition: this spec starts from a reviewed evaluation set.** An interactive
  `python -m app.extraction review` command comes first, on the fast path (no spec). The
  owner then reviews all cases in `backend/evals/extraction/v1/cases.jsonl` (136 cases:
  107 real and 29 synthetic; test 91 cases with 64 empty ones and 52 events; all
  `reviewed: false` today). The pre-labelled version is kept in git at commit `dc02d98`.
  The implementing agent wrote the pre-labels itself (PLAN 004: "a model pre-labelling…
  no provider key is configured locally"), so they come from a Claude model.
- The LLM layer today (`backend/app/extraction/config.py`, `providers.py`): four providers
  (`google`, `openai`, `anthropic`, `openrouter`) chosen by `LLM_PROVIDER`, with per-provider
  keys in `ExtractionSettings` (`backend/app/core/settings.py`). OpenRouter runs through
  `ChatOpenAI` with a `base_url` and a `method: function_calling` work-around for
  structured output. `DEFAULT_MODEL_BY_PROVIDER` is empty, and `prices.toml` has no entries.
- `langchain-openrouter` (first-party LangChain package, `ChatOpenRouter`, 0.2.9 on
  2026-09-29, built on the official `openrouter` SDK) supports structured output
  (`function_calling` / `json_schema`, `strict`), a `reasoning` parameter, provider routing
  (`openrouter_provider`) and `route="fallback"`. LangChain recommends it over `ChatOpenAI`
  with a `base_url`.
- Candidate prices on OpenRouter (USD per 1M input / output tokens, 2026-09-29):
  `google/gemini-3.1-flash-lite` 0.25/1.50, `openai/gpt-6-luna` 0.10/0.50,
  `anthropic/claude-haiku-4.5` 1.00/5.00, `deepseek/deepseek-v4-flash` 0.14/0.28,
  `qwen/qwen3.8-flash` 0.15/0.47, `z-ai/glm-5.3-flash` 0.15/0.50. The prompt is about
  600 tokens and a post about 100, so one test-split run costs roughly 0.02–0.16 USD.
  Claude Haiku 4.5 projects to about 6.7 PLN a month at 1,050 posts, above the 5 PLN
  threshold, and stays in the comparison as a quality reference.
- The owner's OpenRouter balance is 1.70 USD. That is the hard ceiling on this spec's spend.
- Production does not run extraction yet (no LLM variables on Railway), so changing the
  configuration shape breaks no deployment.

## Read context

- `docs/ROADMAP.md` — read in full. Stage 1, last open item "Extraction model comparison"
  (follow-up to spec 004, AC26/AC27; BACKLOG #14) is delivered by this spec. The stage's
  definition of done requires an evaluation set with recorded results for each LLM step.
- `docs/PROJECT.md` — read in full. "The default LLM is picked in the stage 1 spec by
  comparing cheap models on the extraction evaluation set"; budget 20 PLN/month for all
  external APIs; swappability of the LLM provider is a configuration change (after this
  spec: a model change on OpenRouter); every LLM call traced; privacy (posts are public, so
  sending them to any OpenRouter host is acceptable).
- `docs/DECISIONS.md` — read in full. "The cheapest model that passes the eval set wins";
  thresholds F1 ≥ 0.85, linking ≥ 0.95, false alarms ≤ 5%, ≤ 5 PLN a month, no errored case;
  prompt work on the dev split only; the LLM behind the LangChain chat-model interface (kept:
  `ChatOpenRouter` is one); unit tests use a fake LLM and evaluations run outside `pytest`;
  exact pins; Langfuse Cloud EU.
- `docs/BACKLOG.md` — read in full. #14 is this spec. #13 (fallback to a second provider) is
  closed here by a fallback model through OpenRouter. #12 (GW6 extension of the set) stays
  open and reuses this tooling.
- `docs/CONVENTIONS.md` — read in full. Prompts live in `backend/app/content/` with a
  version; LLM steps tested against a fake model; evaluation through a separate command;
  exact pins; credentials never in logs.
- `docs/DEPLOYMENT.md` — searched for "extraction", "LLM_PROVIDER", "OPENROUTER",
  "USD_PLN_RATE". Section 8 (Tweet extraction) describes the four-provider variables and is
  rewritten for the OpenRouter-only configuration.
- `docs/adr/` — `README.md` read in full (sections Status, Context, Options, Decision,
  Consequences); ADR 0006 is the next free number.
- `specs/004-tweet-extraction/SPEC.md` and `PLAN.md` (Owner decisions, Deviations, Final
  review, manual scenarios) — read in full or searched for "prelabel", "manual", "step 22".
  They give the descoped AC26/AC27, the manual worker scenario taken over here and the
  final-review fix F6 (errored cases fail a run).

## Scope

- Checks on the reviewed set before any paid run: every case `reviewed: true`, the
  composition test of AC23 (004) passes, and a disagreement report compares the reviewed
  labels with the pre-labels at commit `dc02d98`.
- OpenRouter as the only LLM provider through `langchain-openrouter`. The Google, OpenAI and
  Anthropic adapters, their keys and the packages `langchain-google-genai`,
  `langchain-openai` and `langchain-anthropic` are removed.
- Configuration: extraction is on when `OPENROUTER_API_KEY` is set; `LLM_MODEL` is optional
  (default from ADR 0006); `LLM_FALLBACK_MODEL` is optional; `LLM_PROVIDER` and the CLI's
  `--provider` option go away.
- Reasoning set to the lowest level each model allows, the same in evaluation and in the
  worker.
- `prices.toml` entries for every candidate model (OpenRouter prices with the date
  checked); `USD_PLN_RATE` for the runs.
- Prompt tuning on the dev split, then one test-split run per candidate with the frozen
  prompt: `google/gemini-3.1-flash-lite`, `openai/gpt-6-luna`, `anthropic/claude-haiku-4.5`,
  `deepseek/deepseek-v4-flash`, `qwen/qwen3.8-flash`, `z-ai/glm-5.3-flash`, run by the agent
  with the owner's key.
- `docs/reports/extraction-eval-v1.md`, ADR 0006, DECISIONS rows, the default model and
  the fallback model in the configuration.
- Documentation: `.env.example`, `docs/DEPLOYMENT.md` section 8, README; ROADMAP tick;
  BACKLOG #13 and #14 closed.
- The owner's manual worker run with the real key and Langfuse (the scenario taken over from
  spec 004).

## Out of scope

- The interactive `review` command — delivered before this spec on the fast path.
- The owner's review of the cases itself. It is the precondition of this spec.
- Extending the set with the GW6 deadline window and re-running the comparison —
  BACKLOG #12 (P1, trigger unchanged).
- An outage of OpenRouter itself (no second gateway) — recorded as an accepted risk in
  ADR 0006, not a backlog item unless the owner adds one.
- Evaluation regression in CI and cost tracking against the budget — Stage 5.
- Enabling extraction on Railway — the owner's operation after the merge, following the
  updated DEPLOYMENT section.
- Comparing reasoning on against off — every model runs at its lowest reasoning level.

## Requirements and acceptance criteria

Evaluation set

- [ ] AC1: Before the first paid run, every case in `cases.jsonl` has `reviewed: true` and
  the composition test from spec 004 (AC23) passes. If it fails, the agent adds no cases or
  labels itself; it stops with an escalation that lists the failing rules. New cases are the
  owner's to add and review.
- [ ] AC2: A command compares the reviewed set with the pre-labelled set at a given git
  revision (default `dc02d98`). It prints, per split, the number of cases changed and events
  added, removed and relabelled (event type, certainty, FPL ID), and the pre-labels'
  precision, recall and F1 against the reviewed labels (the same event matching as
  `evaluate`). The comparison logic is tested in `pytest` on synthetic sets with known
  differences. The report includes its output.

Provider and configuration

- [ ] AC3: The only chat model built by the application is `ChatOpenRouter` from
  `langchain-openrouter`. `langchain-google-genai`, `langchain-openai` and
  `langchain-anthropic` are gone from `pyproject.toml` and `uv.lock`, and no module imports
  them.
- [ ] AC4: With `OPENROUTER_API_KEY` unset, the worker starts, runs the FPL jobs and the
  tweet ingest, logs `extraction disabled` once, and `status` shows `Extraction: disabled`,
  even when `LLM_MODEL` is set.
- [ ] AC5: With `OPENROUTER_API_KEY` set and `LLM_MODEL` empty, extraction uses the default
  model named in ADR 0006. With `LLM_MODEL` set, it uses that model. `LLM_PROVIDER` is no
  longer read. `reextract`, `prelabel` and `evaluate` accept `--model` and no longer accept
  `--provider`. Without the key they fail with a message naming `OPENROUTER_API_KEY`, never
  its value.
- [ ] AC6: With `LLM_FALLBACK_MODEL` set, a request whose primary model fails is answered by
  the fallback model through OpenRouter's fallback routing. The stored extraction and the
  Langfuse trace record the model that actually answered. Tested without network against
  recorded or faked responses.
- [ ] AC7: Every request asks for the lowest reasoning level the model allows. Reasoning
  tokens the provider still reports are recorded with the usage and shown per run by
  `evaluate`.
- [ ] AC8: The request timeout (60 s), no client-side retries (the service's 3-attempt rule
  stays the only retry), token usage and the Langfuse callback work with `ChatOpenRouter` as
  they did with the removed adapters. The existing extraction, service, worker and tracing
  tests pass on a fake model.
- [ ] AC9: The provider stored on new extractions is `openrouter` and the model is the full
  OpenRouter model ID (e.g. `google/gemini-3.1-flash-lite`). Existing rows are not changed.

Comparison

- [ ] AC10: `prices.toml` has an entry for every candidate, keyed by its OpenRouter ID, with
  the price and the date it was checked against OpenRouter's model list. `evaluate` computes
  cost from it for every run.
- [ ] AC11: The prompt is tuned only with `evaluate --split dev`. Every prompt change bumps
  the prompt's version. The report lists each dev iteration (prompt version, model, F1,
  linking, false alarms, cost).
- [ ] AC12: The prompt is frozen before the first test-split run. Every candidate runs on
  the test split once with that prompt version and the same reasoning setting. A run with
  errored cases caused by provider errors (rate limit, 5xx, timeout) may be repeated once;
  the report shows both runs.
- [ ] AC13: The total spend of all runs in this spec (dev and test) stays within 1.50 USD,
  summed from the run files and stated in the report. Before a run that would push the
  projected total over 1.50 USD, the agent stops with an escalation.
- [ ] AC14: The result files of the test-split runs are committed in
  `backend/evals/extraction/results/`. Dev-split result files are ignored by git.
- [ ] AC15: The OpenRouter host that served each open-weight model's requests is recorded in
  the run's results when OpenRouter reports it.

Decision and documents

- [ ] AC16: `docs/reports/extraction-eval-v1.md` holds:
  - the set's composition and the disagreement report (AC2);
  - the dev iterations;
  - for each candidate on the test split: precision, recall, F1, linking accuracy, false-alarm
    rate, certainty accuracy with its confusion table, errored cases, latency p50/p95, tokens
    (including reasoning), cost per post, and projected PLN a month at 1,050 posts;
  - pass or fail against each threshold;
  - the USD_PLN_RATE used and its date;
  - the total spend;
  - threats to validity: the pre-labels came from a Claude model, the test split is small
    (52 events, 64 empty posts), and one run per model.
- [ ] AC17: If at least one candidate passes all thresholds, the cheapest passing candidate
  by projected monthly cost is the default, and the second cheapest passing candidate is the
  fallback. If only one passes, it is the default and the fallback is the best other
  candidate within 5 PLN a month. "Best" means it passes the most thresholds, then has the
  higher F1.
- [ ] AC18: If no candidate passes, the best candidate within 5 PLN a month becomes the
  interim default, and the next best becomes the fallback. "Best" means it passes the most
  thresholds, then has the higher F1. The report and ADR 0006 say the default is interim
  and name the thresholds it misses. BACKLOG gets a P1 item to revisit the default. No
  threshold is changed.
- [ ] AC19: ADR 0006 records the default and fallback model, OpenRouter as the only provider
  through `langchain-openrouter`, the configuration shape (key as the switch) and the
  accepted risk of an OpenRouter outage. DECISIONS gets the matching rows. The configuration
  default names the default model.
- [ ] AC20: `backend/.env.example`, `docs/DEPLOYMENT.md` section 8 and the README describe
  the OpenRouter-only variables (`OPENROUTER_API_KEY`, `LLM_MODEL`, `LLM_FALLBACK_MODEL`,
  Langfuse, `USD_PLN_RATE`) with placeholders only, and mention no removed variable.
  `docs/PROJECT.md`'s swappability line says the LLM is swapped by model through OpenRouter.
- [ ] AC21: ROADMAP ticks the Stage 1 item "Extraction model comparison". BACKLOG removes
  #13 and #14 and keeps #12.

Manual (owner)

- [ ] AC22: Before the merge, the owner runs the worker locally with the real
  `OPENROUTER_API_KEY` and the Langfuse keys, and leaves it running until at least 5 new
  posts are extracted. In Langfuse, each post is one trace with the model, prompt version,
  tokens, cost and the X ID. `python -m app.worker status` shows the model and the latest
  extraction.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Two steps: an interactive `review` CLI on the fast path first; this spec starts from a reviewed set | a planned stop inside `/pipeline:ship`; the owner reviewing raw JSONL | `/pipeline:ship` stays autonomous. In raw JSONL, FPL IDs are unreadable numbers, so the review needs a tool |
| OpenRouter as the only provider through `langchain-openrouter` | keeping `langchain-openai` with a `base_url`; keeping direct adapters | one key and every candidate. The first-party client handles structured output, reasoning and routing natively, where `ChatOpenAI` with a `base_url` needs work-arounds. Three packages are removed and one added, pinned exactly despite 0.x |
| Extraction is on when `OPENROUTER_API_KEY` is set; `LLM_PROVIDER` removed | `LLM_PROVIDER` kept as a switch that only accepts `openrouter` | with one provider, the variable selects nothing; production has no LLM variables yet |
| Six candidates: the cheapest current model of Google, OpenAI and Anthropic, plus DeepSeek, Qwen and GLM | only the three big vendors | the open-weight models cost under 1 PLN a month and may win; the posts are public, so hosting location is no concern |
| Lowest reasoning level for every model, in evaluation and production | each model's default; comparing both | extraction is simple, and reasoning multiplies cost and latency. A shared, recorded setting keeps the comparison fair |
| No passing model → interim default (the best within 5 PLN), recorded in the ADR, plus a BACKLOG item | a ladder with a stronger model and an escalation; lowering thresholds | the owner's decision. Thresholds never change after test results are seen |
| Fallback model through OpenRouter's fallback routing, closing BACKLOG #13 | ADR note only; leaving #13 open | one variable. OpenRouter already reroutes between hosts of one model, and the fallback covers a failing model or vendor |
| Test-split result files committed; dev runs ignored by git | all or none committed | the report and ADR cite reproducible numbers; dev runs are working iterations |
| Spend ceiling 1.50 USD for all runs | 10 PLN | the owner's OpenRouter balance is 1.70 USD; the estimate for the six test runs is about 0.3 USD |

## Owner decisions

- New dependency accepted up front: `langchain-openrouter` (exact pin, pulls the official
  `openrouter` SDK). Removal of `langchain-google-genai`, `langchain-openai` and
  `langchain-anthropic` accepted.
- No data migration: existing tables are unchanged.
- The agent runs the paid evaluation runs with the owner's `OPENROUTER_API_KEY` in
  `backend/.env`, within 1.50 USD in total; the OpenRouter balance (1.70 USD) is the hard
  ceiling.
- The candidate list of six models (Scope), with Claude Haiku 4.5 kept as a quality
  reference despite its projected cost.
- If no model passes: interim default, no threshold change (AC18).
- Result files: test-split runs committed, dev runs ignored.

## Open questions (non-blocking)

- Structured-output method (`function_calling` or `json_schema`) — the plan picks one per
  model from what OpenRouter lists as supported and records it in the run results. The
  method should be the same for all candidates where every candidate supports it.
- Does OpenRouter's `usage.cost` in the response match the cost computed from
  `prices.toml`? If `ChatOpenRouter` exposes it, the report may show both. `prices.toml`
  stays the source for the pass rule.
