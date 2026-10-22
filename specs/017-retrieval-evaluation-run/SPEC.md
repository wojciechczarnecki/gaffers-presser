---
status: implemented
stage_history:
  - "spec-draft — 2026-10-16"
  - "spec-ready — 2026-10-16"
  - "plan-draft — 2026-10-22"
  - "plan-approved — 2026-10-22"
  - "implemented — 2026-10-22"
metrics:
  started_at: 2026-10-22T15:25
  plan_steps: 9
  plan_changes: 9
  escalations: 3
  escalations_permission: 1
  escalations_tooling: 1
  plan_review_blockers: 1
  plan_review_majors: 1
  implement_steps: 9
  implement_iterations: 5
  deviations_minor: 5
  deviations_major: 0
---

# SPEC 017 — Retrieval evaluation run on the GW6 window and rolling out the winners

## Goal

Choose how corroboration searches for a player's news from measured results on real pre-deadline
data rather than from spec 006's guess, and put the winner into production. Corroboration searches
with an embedding model, a mode, a query variant and a depth. The alert window also starts after
the previous gameweek's last match instead of at the previous deadline (BACKLOG #53). It works
when the report gives every configuration's recall at the judge's cut on the GW6 `player` slice.
The best configuration then runs in corroboration, an ADR records the embedding model, and the
README shows the results.

## Context

- Spec 016 (merged in PR #37) built the tooling: set v2 with the production-shaped `player`
  slice and v1's queries as the `free` slice, query variants (`web_name`, `full_name`, `names`),
  recall at the judge's cut at several depths, pooling that extends, the session judge for
  relevance (`export-judge-inputs`, `estimate`, `scripts/session_judge.sh … retrieval`,
  `import-judge`, `agreement`, `review`), self-describing result files and `compare`. All of it
  is under `python -m app.retrieval.evaluation`, and the procedure is in `docs/EVALUATION.md`
  ("The retrieval procedure").
- Production today:
  - Corroboration searches the bare `web_name` (`QUERY_VARIANT = "web_name"`) in hybrid mode,
    over source posts in the alert window. It takes `SEARCH_LIMIT = 20`, drops the posts
    extraction claimed and judges at most `MAX_JUDGED = 10`
    (`backend/app/corroboration/sources.py:25-29`).
  - The default embedding model is `openai/text-embedding-3-small`
    (`backend/app/retrieval/config.py:9`), and RRF uses `k = 60`.
  - The alert window starts at `window_floor(previous deadline, deadline, lookback)`, the later of
    the previous deadline and the deadline minus `ALERT_MAX_LOOKBACK_DAYS` (default 7)
    (`backend/app/fpl/deadlines.py:45`). The same function is used by
    `backend/app/alerts/service.py:78` (the alerts and their corroboration),
    `backend/app/tweets/loop.py:119` (the ingest catch-up) and
    `backend/app/retrieval/evaluation/players.py:62` (the `alerts` rule of the slice builder).
- The GW6 data in the Compose database (read only, 2026-10-16):
  - The GW6 deadline was 2026-10-10 10:00 UTC. The GW5 deadline was 2026-09-18 17:30 UTC,
    and GW5's last kickoff was 2026-09-20 15:30 UTC (an international break in between).
  - There are 1148 posts before the GW6 deadline and 1965 in total, all embedded with
    `text-embedding-3-small`: about 110k tokens for 1965 posts, so about 56 tokens a post.
  - The alerts' window for GW6 starts on 2026-10-03 10:00 (the deadline minus 7 days). In that
    window, 117 linked players have current events on 130 posts. From GW5's last match, there
    are 132 players.
- Because of the international break, the GW6 window under the alerts' rule starts two weeks
  after GW5's last match. GW6 therefore cannot hold a stale claim, and it gives no number for or
  against #53. The owner decided to implement #53 regardless.
- Embedding cost is negligible. OpenRouter lists 33 embedding models from $0 to $0.20 per
  million tokens (checked 2026-10-16). At about 65k tokens, the GW6 corpus costs at most about
  $0.013 per model. The binding cost is the session judge's tokens, which grow with every
  model's unseen pooled posts.
- Precedent for owner-attended labelling before `/pipeline:ship`: spec 005 started from a set
  the owner had reviewed on the fast path beforehand, so `/pipeline:ship` stayed autonomous.

## Read context

- `docs/ROADMAP.md` — read in full. This is the Stage 2 item "Retrieval evaluation report on
  the GW6 window", the spec 017 half (the run), and the **portfolio MVP**. It comes before
  extraction and corroboration v2 (018), because the corroboration judge reads retrieval's
  output. The Stage 4 Q&A agent item is where the `free` slice comes back.
- `docs/PROJECT.md` — searched for "retriev", "embedd", "evaluat", "rerank", "budget",
  "portfolio". FR-2.4 asks for a retrieval evaluation set run on demand. The portfolio goal is
  an evaluated RAG system. External APIs are capped at 20 PLN a month. Each default model is
  picked by comparing candidates on its evaluation set.
- `docs/DECISIONS.md` — searched for "retriev", "embedd", "judge", "rerank", "window_floor",
  "evaluat". The relevant rows were read in full:
  - 2026-09-26 (provider APIs, own hybrid retrieval, ADR 0002);
  - 2026-09-29 rows 51–55 (embeddings through OpenRouter keyed by (post, model), RRF `k = 60`,
    depth 50 per mode, set v1, no shared evaluation framework);
  - 2026-09-30 rows 56–57 (the corroboration window and the judge's 10 posts);
  - 2026-10-07 (one `window_floor` for the alerts and the ingest catch-up);
  - 2026-10-12 (the session judge);
  - 2026-10-15 rows 98–107 (orchestration, module boundaries, set v2 and its labelling policy,
    the evaluation result files).
  #53 supersedes the "previous deadline" part of the 2026-10-07 row with a new row.
- `docs/BACKLOG.md` — read in full:
  - #16 (reranking) is decided here by a rule set in advance;
  - #53 (the alert window) is implemented here and its row removed;
  - #17 (an ANN index) is unaffected, because the corpus stays small;
  - #12, #23, #46 (the extraction entry point) and #47 belong to 018.
- `docs/CONVENTIONS.md` — searched for "test", "docstring", "evaluat", "constant". There are
  no docstrings. Tests mirror `app/`. Evaluation sets never run inside `pytest`.
- `docs/EVALUATION.md` — read in full. Phase A follows "The retrieval procedure":
  - the pilot with Sonnet (medium effort) and Opus;
  - `estimate` before every judge run;
  - one call per query, at most 30 posts per input;
  - the blind review of every disagreement plus a seeded sample.
  Its example `--until 2026-10-10T18:30` is wrong for GW6, whose deadline was 10:00 UTC.
- `docs/DEPLOYMENT.md` — searched for "EMBEDDING_MODEL", "index", "ALERT_MAX_LOOKBACK". A
  model switch changes the default in code. The Railway deployment is postponed (BACKLOG #37),
  so re-indexing is the owner's operation on the Compose database.
- `docs/adr/` — listed, and ADR 0002 and ADR 0006 read for format. This spec writes ADR 0009
  for the embedding model.
- `docs/reports/` — searched `code-audit-2026-10.md` for "017", "A1", "A2", "A3", "#53". Its
  "For spec 017" section predates the renumbering, and its contents (A5, N5, D3) belong to 018
  or were done in 016. Searched `presser-model-comparison.md` for "token", "judge": the session
  judges took about 7–8M tokens, and a per-item fan-out hit the session limit.

## Scope

Two phases. Only phase B runs under `/pipeline:ship`.

**Phase A — the labelled set, before `/pipeline:ship`.** The owner and the session do this on the
fast path, and it is merged as its own data PR:

- export the GW6 corpus and build the `player` slice (read only on the Compose database);
- add price rows for the three candidate embedding models;
- pool every candidate configuration;
- run the judge pilot, the owner's review of it and the choice of judge;
- judge the full slice and run the owner's review of disagreements plus a sample;
- commit set v2 with its labels.

**Phase B — the run and the rollout (`/pipeline:ship 017`):**

- one evaluation run per embedding model on the `player` slice, and the `compare` table;
- the report `docs/reports/retrieval-eval-v2.md`;
- ADR 0009 for the embedding model;
- the winning configuration in production: the embedding model, the corroboration mode, the
  query variant and the search depth;
- the decision on reranking (#16) by its rule;
- the new alert window start (#53) for the alerts, their corroboration and the ingest catch-up;
- the README results section;
- the documents: DECISIONS, BACKLOG, ROADMAP and `docs/EVALUATION.md`.

## Out of scope

- The `free` slice. It is not judged, evaluated or reported in this spec. Its v1 queries stay in
  the set unchanged, and it comes back with the Stage 4 Q&A agent, which asks free questions.
  A BACKLOG row and a note at the ROADMAP's Q&A item record this (AC14).
- Implementing reranking. #16 gets its priority and trigger from the rule in AC9, and a
  reranker is its own spec, because it brings a new LLM step or a new dependency with its own
  evaluation set.
- Re-indexing the Compose database and any write to it. These are the owner's operations
  (AC8).
- Extraction and corroboration evaluation v2 (#12, #23, #46 for extraction, #47), which is
  spec 018.
- An ANN index (#17).
- Changing set v1's files or the spec 016 tooling. A tooling defect found during the run is
  fixed within this spec only if it blocks the run, and it is recorded as a deviation.
- The Railway deployment (#37).

## Requirements and acceptance criteria

**Phase A — the labelled set (fast path, before `/pipeline:ship`; a precondition of phase B)**

- [ ] AC1: Set v2 in `backend/evals/retrieval/v2/` holds the following:
      - the corpus, exported with `--since 2026-09-18T17:30Z` (the GW5 deadline) and
        `--until 2026-10-17T10:00Z` (the GW7 deadline);
      - the `player` slices for gameweeks 6 and 7, each built with the `alerts` window rule and
        split into dev and test by the builder's seed (owner decision of 2026-10-17);
      - v1's queries as the `free` slice, carried with their labels and not judged again.
      The set's `set.json` names its set version. Only the commands
      `export-corpus` and `build-player-slice` touched the Compose database, read only.
- [ ] AC2: `backend/app/llm/prices.toml` has dated price rows for `qwen/qwen3-embedding-8b`,
      `openai/text-embedding-3-large` and `google/gemini-embedding-001`, with prices from
      OpenRouter's model list on the day of the run. Every pool and evaluation run is started
      with `--max-cost-usd 0.10` (the estimate is about $0.05 for all four models together, and
      the owner's cap is $1, cheaper preferred), and the total paid embedding
      spend is recorded in the report.
- [ ] AC3: The pool of every `player` query is the union of the judge's cut at the largest
      depth (50) across the three modes, the three query variants and the four models
      (baseline and three candidates). Before every judge run, `estimate` is printed and the
      owner sees it. The session judge's tokens for the whole of phase A stay at or below 3M
      (set for one gameweek; the owner accepted about 5.8M with GW7, 2026-10-22).
- [ ] AC4: The pilot runs on a seeded sample of queries (`--query-sample 20 --seed 1`, which
      draws over both slices and gave 15 `player` queries and 769 pairs). It is judged by Sonnet
      at medium effort and by Opus, and `agreement` is run. Relevant pairs are about 4% of the
      pool, so raw agreement does not choose the judge. The owner reviews the pilot by stratum
      (`review --relevant N --named N --rest N`): every disagreement, every pair both judges
      called relevant, every not-relevant pair whose post names the player, and 20 of the other
      pairs. Each judge's precision and recall on the relevant class against the owner are
      estimated with each reviewed pair weighted by its stratum's size over its reviewed pairs,
      and Cohen's kappa beside them. Sonnet becomes the judge for the rest when its precision
      and its recall are each at least 0.90 and each no more than 0.05 below Opus's; otherwise
      Opus does. The choice, both judges' precision, recall and kappa, and their kappa with
      each other are recorded in the data PR's description and later in the report.
- [ ] AC5: The chosen judge labels every remaining pooled pair of the `player` slice. The owner
      reviews the rest by stratum too: a seeded 30 of the pairs the judge called relevant, a
      seeded 30 of the not-relevant pairs whose post names the player, and a seeded 20 of the
      other pairs. Every pair a judge called relevant under `retrieval_relevance_player` v1 is
      judged again under v2 (owner decision of 2026-10-21), and those still relevant under v3
      (owner decision of 2026-10-22); the not-relevant labels stand, as each version only
      narrows relevance. Set v2 is committed with all labels, and the data PR is merged before
      `/pipeline:ship 017` starts.

**Phase B — the run and the report**

- [ ] AC6: For each of the four models there is a result file
      `backend/evals/retrieval/results/test-v2-<model>.json` from `evaluate --split test
      --reference-judge <judge>`. Each covers the `player` slice in the three modes and the three
      variants at depths 20, 30 and 50, and its header names the set version, the reference
      judge, the owner-reviewed and judge-only label counts, and the judge's agreement with the
      owner, with its weighted precision, recall and kappa (`judge_owner_estimates`). A dev run per model is also kept. The `compare` table is in the report, and
      `compare` accepts the four test files together.
- [ ] AC7: The winner is the configuration (model, mode, variant, depth) with the highest recall
      at the judge's cut on the test split of the `player` slice. On a tie, the configuration
      equal to today's production wins (`text-embedding-3-small`, hybrid, `web_name`, 20). After
      that, the cheaper model wins, then the smaller depth, then the simpler variant, in the
      order `web_name`, `full_name`, `names`. There is no margin, so the absolute best wins.
      The report states that differences below about 0.05 on the 69 test queries recall at the cut counts (of 188;
      the others have no unclaimed relevant post) are within
      noise. The rule is applied exactly, and a test of a function implementing it is not
      required: it is applied by reading the `compare` table and shown in the report.
- [ ] AC8: The winner runs in production:
      - **The query variant and depth:** `QUERY_VARIANT` and `SEARCH_LIMIT` in
        `app/corroboration/sources.py`. `MAX_JUDGED` stays 10.
      - **The mode:** the corroboration search mode, if it differs from hybrid.
      - **The embedding model:** `DEFAULT_EMBEDDING_MODEL`, if it differs from
        `text-embedding-3-small`.

      Every existing corroboration and alert test passes, updated only where it names the old
      value. A test shows corroboration searching with the winner's variant text, depth and
      mode.

      If the model changes, the PR description gives the owner's re-index command for the
      Compose database. The owner runs it and confirms before the merge (owner decision of
      spec 016).
- [ ] AC9: The reranking decision (#16). Reranking is needed when both of these hold for the
      winner on the test split:
      - its recall at the judge's cut is below 0.80;
      - the recall of the unclaimed relevant posts within the top 50 (claimed posts dropped, no
        cut to 10) is at least 0.10 higher than its recall at the cut.

      Needed → #16 becomes P1 with the trigger "spec 017's report", and ROADMAP Stage 2 gets an
      item for a reranking spec. Not needed → #16 becomes P3 with the trigger "a later retrieval
      evaluation meets the rule". The report shows both numbers.
- [ ] AC10: `docs/reports/retrieval-eval-v2.md` contains:
      - the set: corpus size, window, queries per split, and the count of players with a
        `web_name` initial;
      - the labelling: the pilot, the chosen judge, both judges' weighted precision, recall
        and kappa against the owner, their kappa with each other, the strata and their review
        rates, and the label counts;
      - the relevance distribution: relevant posts per query, the share of relevant pairs, and
        the count of queries with no unclaimed relevant post, which recall at the cut leaves
        out (with one relevant post, a query's recall is a hit or a miss);
      - the session tokens and the paid embedding spend;
      - the `compare` table;
      - the winner and the tie rule applied;
      - per-query misses of the winner taken from the result file (at least the five worst
        queries);
      - the reranking numbers and decision;
      - the stale-claim and stale-anchor counts per gameweek: GW6 cannot show them because of
        the international break, GW7 can (121 of its 332 claims are stale);
      - the limits: two gameweeks, 69 test queries counted by recall at the cut (of 188), labels of one query not independent
        (one call per query), and labels of several prompt versions (each later one only narrows
        relevance, so a not-relevant label of an earlier version stands).
- [ ] AC11: ADR 0009 records the embedding model chosen, or kept, with the candidates, their
      prices and recall at the cut, and the rejected alternatives. DECISIONS gets a row that
      links it and names the production query variant, mode and depth.

**Phase B — the alert window (BACKLOG #53)**

- [ ] AC12: `window_floor` starts the window at the later of:
      - the end of the previous gameweek's last match, taken as the latest `fixture.kickoff_at`
        of gameweek N−1 plus 2 hours;
      - the deadline minus the lookback (or now minus the lookback when no deadline is known).

      The alerts, their corroboration and the ingest catch-up keep using this one function, and
      the slice builder's `alerts` rule follows it.

      Edge cases:
      - **Before GW1** (no gameweek N−1): the window is the deadline minus the lookback, as
        today.
      - **A gameweek N−1 with no fixture with a kickoff:** the window falls back to the previous
        deadline, as today.
      - **A rehearsal deadline:** uses the last real gameweek before it, as the previous
        deadline does today.

      No guard for an N−1 kickoff after the current deadline is needed. FPL moves a postponed
      fixture to the gameweek where it is replayed (a double gameweek), and leaves a blank in
      N−1, so a fixture of N−1 always kicks off before N's deadline.

      Tests with a seeded calendar check each case:
      - a normal week: the window starts at the last match plus 2 hours, after the previous
        deadline;
      - a blank N−1 (a fixture moved to a later gameweek): the last remaining N−1 match counts;
      - an international break: the lookback wins;
      - before GW1: the deadline minus the lookback;
      - no N−1 fixtures: the previous deadline;
      - the ingest catch-up stops at the same floor as the alerts for the same moment.
- [ ] AC13: DECISIONS gets a row superseding the "previous deadline" part of the 2026-10-07 row,
      and BACKLOG #53's row is removed. The docstring of `window_floor` and the comment in
      `alerts/service.py` describe the new rule. The stale comment in `alerts/config.py:55`
      (BACKLOG #50) is fixed in the same change.

**Phase B — documents**

- [ ] AC14: The README has a retrieval results section: the setup in two sentences, the
      `compare` table cut to the winner per model, and a link to the report. BACKLOG gets a P3
      row: "judge and evaluate the `free` slice of set v2 (English and Polish free questions)",
      with the trigger "building the Q&A agent (Stage 4)". The ROADMAP's Stage 4 Q&A item names
      that row. The ROADMAP's Stage 2 retrieval item is ticked with links to the report and ADR
      0009. `docs/EVALUATION.md`'s retrieval example uses the real GW6 deadline, and it says the
      `free` slice is not judged until Stage 4.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Phase A (labels, owner reviews) on the fast path as a data PR before `/pipeline:ship`; phase B under ship | everything in ship with escalations at every review; the whole spec in the session without the pipeline | spec 005's precedent: ship stays autonomous and never waits hours for the owner; phase B still gets a plan, plan review and final review (the owner, 2026-10-16) |
| Candidates: `qwen/qwen3-embedding-8b`, `openai/text-embedding-3-large` and `google/gemini-embedding-001` beside the baseline | five candidates (plus `voyage-4-lite`, `bge-m3`); only models cheaper than the baseline | three different profiles: cheaper than the baseline, the same family's upper bound, a strong multilingual model. Each model adds about 0.3M judge tokens, and three fit the 3M cap (the owner, 2026-10-16) |
| The absolute best configuration on the test split wins, ties go to production, then cheaper, smaller and simpler | a +0.05 margin for a switch; choosing only the model and variant; choosing on dev+test | the owner's choice: a switch costs a re-index of about $0.002, so a margin buys little; the tie rule avoids a pointless switch; the report states the noise (the owner, 2026-10-16) |
| The alert window starts at the later of the previous gameweek's last match plus 2 hours and the deadline minus the lookback, implemented regardless of GW6's numbers | implementing only on a numeric trigger; no lookback; a separate rule for the ingest; a per-club window (after the player's own club's last N−1 match) | news before the previous gameweek's last match concerns that gameweek, and GW6 cannot measure it because of the break. The lookback still cuts break windows, and one function keeps the ingest covering the alert window (DECISIONS 2026-10-07). The gameweek-wide floor cuts post-match news about clubs that played earlier (e.g. a Sunday "out for 3 weeks" when the last match is on Monday), and the owner accepts this: news about an important player is repeated in the following days, fresh post-match news is often unconfirmed sensation, and a per-club window would split the window per player and the ingest's rule from the alerts' (overengineering) (the owner, 2026-10-16) |
| Reranking: decided by a rule set now, implemented in its own spec | implementing it here; no rule, the owner decides after reading | a reranker is a new LLM step or dependency with its own evaluation set; setting the rule before the results keeps the decision honest (the owner, 2026-10-16) |
| The `free` slice is skipped until the Stage 4 Q&A agent | judging it with the session judge now (about 0.25M tokens) | it decides nothing in production today, and the owner's review time goes to the `player` slice (the owner, 2026-10-16) |
| The judge is chosen by weighted precision and recall on the relevant class from a stratified owner review | raw agreement with the owner (the original AC4); a random review sample | with relevant pairs at about 4%, raw agreement is near 0.96 for any judge and a random sample of 30 holds about one relevant pair; weighting by stratum keeps the estimates unbiased over the whole pool (the owner, 2026-10-17) |
| `--max-cost-usd 0.10` for the embedding runs | the tooling's default 1.00 | the estimate is about $0.05 for all four models; the owner prefers to spend less than the $1 cap |

## Owner decisions

- 2026-10-16: phase A (the labelled set) runs in the session on the fast path as a data PR, and
  `/pipeline:ship 017` starts from the merged set.
- 2026-10-16: three candidates beside the baseline: `qwen/qwen3-embedding-8b`,
  `openai/text-embedding-3-large`, `google/gemini-embedding-001`.
- 2026-10-16: the absolute best configuration (model, mode, variant, depth) by recall at the
  judge's cut on the test split wins with no margin. A tie keeps production, then the cheaper,
  smaller and simpler option.
- 2026-10-16: BACKLOG #53 is implemented in this spec regardless of the numbers, as
  `max(the previous gameweek's last kickoff + 2 h, deadline − lookback)` for the alerts, their
  corroboration and the ingest catch-up.
- 2026-10-16: reranking (#16) is decided by the rule in AC9 and implemented in its own spec if
  needed.
- 2026-10-16: the `free` slice is skipped here, and a note at the ROADMAP's Q&A item plus a
  BACKLOG row bring it back.
- 2026-10-16: the paid embedding spend is capped at $1, and cheaper is preferred.
- 2026-10-17: the judge is chosen and checked by its precision and recall on the relevant class
  against the owner, estimated from a stratified review, not by raw agreement: relevant pairs
  are about 4% of the pilot, so a judge that calls everything not relevant would agree 0.96.
  `review` gains a stratified mode and `agreement` and the result header the weighted
  estimates and kappa, in the data PR (a tooling change this spec otherwise excludes).
  Recall at the cut stays the selection metric: it is computed on relevant posts only.
- 2026-10-17: the set takes the GW7 slice and the corpus runs to the GW7 deadline, for more
  test queries and for stale claims GW6 cannot show.
- 2026-10-21: a line-up or an event on the pitch from any other match (cup, European, national
  team) is not relevant, even when the post does not name the match: the window ends at the
  deadline, before the gameweek's line-ups are announced, so such a post in the window is never
  from the gameweek's match. An injury from such a match is relevant. This is the extraction
  rule of 2026-09-28 (DECISIONS); `retrieval_relevance_player` v2 states it, the pairs judged
  relevant under v1 are judged again, and the owner's review restarts under it.
- 2026-10-22: after the review, `retrieval_relevance_player` v3 adds three not-relevant
  cases the owner labelled so: a card without the word that the player is suspended or banned
  (a cup or European card does not suspend him in the league, and the judge has no fixture
  calendar), a post that only points to news elsewhere ("Updates on X… READ NOW", a video), and
  "no updates on X". The pairs still relevant are judged again under v3.
- 2026-10-22: the phase A judge spend above AC3's 3M (about 5.8M with GW7 and two re-judges)
  is accepted.
- Carried from spec 016:
  - session judge tokens ≤ 3M, estimated and shown before launch;
  - a judge pilot with Sonnet (medium) and Opus, the cheaper judge chosen when its agreement
    with the owner holds;
  - the winning query variant goes into corroboration;
  - the owner re-indexes the Compose database before the merge if the model changes.
- Agents read the Compose database only for `export-corpus` and `build-player-slice`. Writes,
  migrations and re-indexing stay the owner's.
- New dependencies: none. Data migrations: none, because `post_embedding.embedding` has no
  fixed dimension and embeddings are keyed by (post, model).

## Open questions (non-blocking)

- none
