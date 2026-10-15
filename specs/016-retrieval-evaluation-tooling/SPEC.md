---
status: plan-draft
stage_history:
  - "spec-draft — 2026-10-15"
  - "spec-ready — 2026-10-15"
  - "plan-draft — 2026-10-15"
metrics:
  started_at: 2026-10-15T18:36
  plan_steps: 20
  escalations: 0
  escalations_permission: 0
  escalations_tooling: 0
---

# SPEC 016 — Retrieval evaluation tooling: the production-shaped slice and the session judge

## Goal

Make the retrieval evaluation measure the task corroboration gives it: finding the posts about a
player's availability that extraction missed and getting them past the judge's cut. The spec
delivers only tooling and tests. The run is spec 017: the GW6 data, the labels, the report, the
default-model ADR and the decisions on the query, the depth and reranking. This spec works when
spec 017 can run the whole comparison with commands and a script, and every result file says
what produced it.

## Context

- Spec 006 built hybrid retrieval and evaluation set v1 (`backend/evals/retrieval/v1/`: a
  190-post corpus frozen on 2026-09-30, 40 queries, 660 unreviewed labels by
  `openai/gpt-6-luna`). Its only result is a dev run with `--include-unreviewed`
  (`evals/retrieval/results/dev/`). The evaluation code is in `backend/app/retrieval/evaluation/`
  (`dataset.py`, `queries.py`, `labelling.py`, `runner.py`, `metrics.py`, `schema.py`) and its
  commands in the main retrieval CLI (`backend/app/retrieval/cli.py`, 609 lines).
- Spec 007's corroboration searches the player's bare `web_name` in hybrid mode over
  `[window start, as_of)` restricted to source posts. It takes `SEARCH_LIMIT = 20`, drops the
  posts extraction already claimed for the player and gives the judge at most `MAX_JUDGED = 10`
  (`backend/app/corroboration/sources.py:24-25`, `:142-170`).
- The code audit of October 2026 (`docs/reports/code-audit-2026-10.md`, sections A1–A4, N5)
  found the following:
  - Set v1 does not measure that query (A1).
  - Full-text never matches a `web_name` with an initial, e.g. `B.Fernandes` (A2). This
    affects 36 of 667 players.
  - Only posts pooled from `text-embedding-3-small` are judged, and the pool cannot be
    extended (A3).
  - Result files are not self-describing, and two CLIs overwrite results silently (A4, D2, D3).
  - `scripts/session_judge.sh` hard-codes the presser's verdict (N5).
- The session judge procedure (`docs/EVALUATION.md`, DECISIONS 2026-10-12) runs one headless
  `claude -p` call per item through `scripts/session_judge.sh`. The presser comparison taught
  three lessons: estimate tokens before a fan-out, use one model turn per item rather than a
  tool loop, and consider one call per case.
- `post_embedding.embedding` is `VECTOR()` with no fixed dimension, so a candidate embedding
  model needs no migration. OpenRouter lists over 30 embedding models, most at or below
  $0.20 per million tokens.

## Read context

- `docs/ROADMAP.md` — read in full. The Stage 2 item "Retrieval evaluation report on the GW6
  window" is the portfolio MVP and comes before extraction and corroboration evaluation v2,
  because the corroboration judge reads retrieval's output. The owner split it into this
  tooling spec and the run (017), and extraction v2 moves from "planned as 017" to 018.
- `docs/PROJECT.md` — searched for "retriev", "embedd", "evaluat", "rerank". FR-2.4 asks for
  an evaluation set for retrieval run on demand. The portfolio goal is an evaluated RAG system.
  The budget is ≤ 20 PLN a month, and evaluation runs are one-off.
- `docs/DECISIONS.md` — read the retrieval, evaluation and judge rows in full (2026-09-26,
  2026-09-29 rows 51–55, 2026-09-30 rows 56–58, 2026-10-12 row 94, 2026-10-15 rows 98–105).
  - The 2026-09-29 row sets out set v1: a frozen corpus in its own schema `retrieval_eval`,
    binary relevance, recall@5, recall@10 and MRR, and English and Polish slices. This spec
    keeps all of that.
  - The same row says labels are "reviewed by the owner". The owner changed that on
    2026-10-15: the session judge labels and the owner reviews a sample. This gets a new
    DECISIONS row.
  - "A shared evaluation framework" is rejected (row 54), so the shared module of AC16 holds
    the run header and the result write only, and the metrics stay per module.
  - The Compose database is production (row 100), so agents only read it.
  - Module boundaries are held by `test_module_boundaries.py` (row 103).
- `docs/BACKLOG.md` — read in full. #43, #44, #45, #46 and #48 are in this spec's scope. #16
  (reranking) is decided in 017. #12, #23, #47 and #29 belong to 018 and later.
- `docs/adr/` — listed. ADR 0002 (own hybrid retrieval) and ADR 0008 (orchestration in plain
  Python) were read through the DECISIONS rows. Spec 017 writes the embedding-model ADR.
- `docs/CONVENTIONS.md` — searched for "test", "docstring", "cli", "evaluat". There are no
  docstrings. Tests mirror `app/`. LLM steps are tested against a fake model, and evaluation
  sets never run inside `pytest`.
- `docs/EVALUATION.md` — read in full. The session judge's rules apply to the retrieval
  judge: the same versioned prompt, a fresh context per call, blind inputs, a validated schema,
  and the judge recorded by name. So do the lessons on token estimates and per-case calls.
- `docs/DEPLOYMENT.md` — searched for "embedding", "evaluat". This spec changes no
  environment variable and no image. The evaluation runs locally, never in the worker.
- `docs/reports/` — read sections A1–A4 and N5 of `code-audit-2026-10.md`, and searched
  `presser-model-comparison.md` for "token", "judge", "cost". The presser session judges took
  about 7–8M tokens. A per-item subagent fan-out hit the session limit.

## Scope

- Set v2 format: a frozen corpus with the fields the production filter needs, and two slices of
  queries, `player` (production-shaped) and `free` (the v1 kind).
- A builder of the `player` slice for a gameweek deadline from the stored extractions.
- Query variants for a player, available to the evaluation and to corroboration. The production
  default stays the bare `web_name`.
- The evaluation of the `player` slice at the judge's cut, at several search depths, and of the
  `free` slice as in v1, per embedding model, with the unjudged share reported.
- Pooling that extends a judged query with only the posts it has not seen yet.
- The session judge for relevance: export, import, two-judge agreement, the owner's sample
  review and a token estimate before a run.
- A run header, an atomic write and an overwrite guard shared by the four evaluations.
- The retrieval evaluation's own entry point, `python -m app.retrieval.evaluation`.
- `scripts/session_judge.sh` with the verdict schema as a parameter.
- Documents: `docs/EVALUATION.md`, `docs/DECISIONS.md`, and the ROADMAP and BACKLOG
  references to the 017/018 renumbering.

## Out of scope

- The run itself:
  - exporting the GW6 corpus and building set v2 from the Compose database;
  - the judge pilot and the labels;
  - the owner's sample review;
  - the comparison of modes, embedding models, query variants and depths;
  - the report, the embedding-model ADR, and the README results section;
  - switching the production query, depth or default model, and re-indexing.

  All of this is spec 017 (planned).
- Changing the production window rule (`window_floor`, shared by the alerts, their
  corroboration and the ingest catch-up; DECISIONS 2026-10-06) to start after the previous
  gameweek's last match. It goes to BACKLOG as P1 (AC19), decided on spec 017's numbers.
- Implementing reranking (BACKLOG #16). Spec 017 decides whether it is needed.
- Moving the extraction evaluation to its own entry point (#46, the D3 part for extraction)
  goes to spec 018. So do #12, #23 and #47.
- Price rows for candidate embedding models. Spec 017 adds them when it picks the candidates.
- An ANN index (BACKLOG #17).
- Changing set v1's files. They stay as the frozen baseline.

## Requirements and acceptance criteria

**Set v2 format**

- [ ] AC1: A set v2 lives in its own directory (`backend/evals/retrieval/v2/`) and states its
      set version, which every run copies into its header (AC16). A corpus post also carries
      whether it is a source post, by the same rule as `source_post_sql`. When the corpus is
      loaded into `retrieval_eval`, a search with `sources_only` returns exactly the posts
      marked as sources. A test proves this with a corpus mixing list posts, off-list posts and
      quoted posts.
- [ ] AC2: A `player` query records the following:
      - the player (season, FPL ID, `web_name`, first and second name, club, aliases);
      - the gameweek N whose deadline ends the window;
      - the window `[start, end)`, where `end` is the deadline of gameweek N;
      - the window rule used to compute `start`;
      - the IDs of the posts extraction claimed for the player in that window, each flagged
        stale when it was published before the previous gameweek's last match ended (AC3);
      - its judgements.
      A `free` query keeps v1's fields. Set v1 files still load unchanged, and a v1 query can be
      carried into a v2 set as a `free` query with its judgements and their `labelled_by`.

**Building the player slice**

- [ ] AC3: `build-player-slice --gameweek N` writes one `player` query for every player
      with at least one current extraction event on a source post in the window. Its claimed
      IDs equal what `sql_claims` returns for that player and window. It reads the database
      only.

      `--window` chooses one of two rules for the window start:
      - `alerts` (the default): the alerts' rule today, `window_floor` of the previous deadline
        and the deadline minus the default `ALERT_MAX_LOOKBACK_DAYS`;
      - `after-last-match`: the end of gameweek N−1's last match, taken as its latest
        `fixture.kickoff_at` plus 2 hours (FPL gives no end time).

      Under either rule, a claimed post published before that end is flagged stale.

      A test on a seeded container checks the following cases:
      - a player claimed twice yields one query with two claimed IDs;
      - a post outside the window is not claimed;
      - an off-list post is not a source post;
      - a player with no event gets no query;
      - a line-up post published between the previous deadline and the previous gameweek's
        last match is claimed and flagged stale under `alerts`, and falls outside the window
        under `after-last-match`;
      - a window longer than the lookback (an international break) starts at the deadline
        minus the lookback under `alerts`.
- [ ] AC4: `export-corpus` takes `--until` (and an optional `--since`), so the corpus can end
      at the deadline. It still leaves out the project's own test account, as in v1.

**Query variants (BACKLOG #44)**

- [ ] AC5: The following query variants exist for a player:
      - `web_name`: today's query;
      - `full_name`: first and second name;
      - `names`: the `web_name` with an initial dropped, OR-ed with the second name's last
        token and the player's aliases from `aliases.toml`.

      For `B.Fernandes` with first name Bruno and second name Borges Fernandes:
      - `full_name` and `names` find, in full-text mode, a post saying "Bruno Fernandes" and
        one saying "Fernandes";
      - `web_name` finds neither.

      A test on a container checks both directions.
- [ ] AC6: `retrieval_candidates` builds its query through the variant named by one constant in
      `app/corroboration/`, which defaults to `web_name`. With the default, every existing
      corroboration and alert test passes unchanged. A test with another variant shows the
      search receiving that variant's text.

**Evaluation of the player slice (BACKLOG #43)**

- [ ] AC7: For each `player` query, configuration and search depth N, the evaluation does the
      following:
      - **Configuration:** a mode, an embedding model, a query variant, and RRF `k`.
      - **Search:** the variant's text, filtered to the query's window and to source posts.
      - **The judge's cut:** the top N, with the claimed IDs dropped, then the first 10. This is
        what corroboration would give the judge.
      - **Primary metric:** recall at the judge's cut. The reference set is the relevant
        posts not among the claimed IDs.

      It reports three more numbers per query: recall@10 over all relevant posts, claimed
      included; whether the cut was empty while an unclaimed relevant post existed; and the
      unjudged share of the cut.

      Five counts are reported separately:
      - queries with no unclaimed relevant post;
      - queries with no relevant post at all;
      - players with a `web_name` initial;
      - stale claimed posts;
      - queries whose newest claimed post, which corroboration would take as the anchor, is
        stale. The depths are
      given on the command line (default `20,30,50`).

      A test with a hand-built corpus and labels checks every number, including one case
      with more than 20 claimed posts.
- [ ] AC8: A run evaluates one embedding model. It reports every mode and every query variant
      at every depth for the `player` slice, and recall@5, recall@10 and MRR per mode with the
      `en`/`pl` slices for the `free` slice, as v1 does. Its result file holds per-query
      details: for each configuration, the IDs at the cut and their labels. A reader can then
      find the misses without re-running.
- [ ] AC9: Before embedding the corpus, the evaluation prints the corpus's estimated token
      count and cost for the model from `prices.toml`. It refuses to start above
      `--max-cost-usd`, which defaults to 1.00. A model without a price row is refused, as
      `resolve_embedding` does today.
- [ ] AC10: `compare` prints one table from several result files: one row per model, mode,
      variant and depth, and the unjudged share. It refuses files with different set versions
      or splits, names the offending file, and never mixes them.

**Pooling (BACKLOG #45)**

- [ ] AC11: The pool of a query is the union of what the configured runs would show:
      - for `player` queries: the judge's cut at the largest depth, for every mode, variant and
        model given;
      - for `free` queries: the top 10 per mode and model.

      Extending a query adds only the posts it has no judgement for and keeps its existing
      judgements. A second extension with the same models adds nothing. A test shows a
      judged query gaining exactly the new model's unseen posts.

**Session judge (relevance)**

- [ ] AC12: `export-judge-inputs` writes one input per query. The input holds the versioned
      relevance prompt for its slice (both prompts live in `backend/app/content/prompts/`).
      It also holds the query: for `player` queries, the player, the club and the window. Then
      it lists the query's unjudged pooled posts in a seeded random order under neutral IDs
      (`P1`, `P2`, …), each with its author and text. The mapping to X IDs is kept outside the
      inputs directory.

      For the `player` slice the prompt defines relevance as follows: the post carries
      information about this player's availability for his gameweek N Premier League match
      (injury, fitness, line-up, rotation, suspension), a denial included. The input names
      gameweek N and its deadline. A line-up or team news for an earlier gameweek's match,
      or a mention without such information, is not relevant.

      A query with more posts than `--max-posts` (default 30) is split into several inputs.
- [ ] AC13: `import-judge --judge-model "<name>"` reads the verdicts and does the following:
      - **Validation:** each verdict must hold exactly one boolean label per neutral ID of its
        input. A missing, extra or unknown ID fails that input, and the failure names it.
      - **Storage:** the labels are stored with `labelled_by` set to the judge's name.
      - **Second judge:** a second judge's labels for the same pairs are stored beside the
        first, not over them.

      `agreement` prints the two judges' agreement and their disagreements. `estimate` prints
      the number of calls and the estimated tokens of an export before any call is made, using
      characters / 4 plus a fixed per-call overhead.
- [ ] AC14: `review --sample N --seed S` runs the owner's review. It shows every pair the judges
      disagree on, then a seeded random sample of N of the remaining pairs, each with the
      player or query and the post. The owner's label is stored as reviewed, and the judges'
      labels are kept.

      The reference label of a pair is the owner's label if reviewed, otherwise the chosen
      judge's label (`--reference-judge`).

      Every run's header records:
      - the reference judge;
      - the counts of owner-reviewed labels and judge-only labels;
      - the judge's agreement with the owner on the reviewed pairs.

      An evaluation with no label from the reference judge or the owner refuses to run.
- [ ] AC15: `scripts/session_judge.sh` takes the verdict schema as a parameter: a reply format
      and a validation command for each kind of verdict. Its default is the presser's, so the
      presser procedure in `docs/EVALUATION.md` runs unchanged and closes BACKLOG #48. With
      the retrieval schema it rejects a reply missing a label, and a re-run judges only what
      failed.

**Run header and result files (BACKLOG #46)**

- [ ] AC16: One shared module writes every evaluation result of extraction, corroboration,
      retrieval and the presser. It provides:
      - **A run header:** set version, model, prompt version, reasoning effort, judge, date,
        and git commit with a dirty flag. A field that does not apply is null.
      - **An atomic write.**
      - **An overwrite guard:** writing to an existing result file fails with its path unless
        `--force` is given.

      The seven copies of the atomic write in the evaluation packages are gone. Result files
      already in the repository still load, and a reader shows them as "no header". A test per
      evaluation shows the header written and the guard refusing.
- [ ] AC17: The presser's `summary` and `choose` group runs by set version and prompt version
      and never rank runs from different groups together. A run without a header is shown
      apart.

**Entry point**

- [ ] AC18: `python -m app.retrieval.evaluation` holds the evaluation commands. Most come from
      the main retrieval CLI, and the commands of AC2, AC3, AC10 and AC12–AC14 are new. The commands
      are `export-corpus`, `build-queries`, `build-player-slice`, `carry-v1`, `prelabel`,
      `export-judge-inputs`, `import-judge`, `agreement`, `estimate`, `review`, `evaluate` and
      `compare`. `python -m app.retrieval` keeps `index`, `status` and `search` only. The
      `evaluate` and `prelabel` commands of the old place are removed, not aliased
      `test_module_boundaries.py` passes with no new edge outside what the
      evaluation already imports.

**Documents**

- [ ] AC19: `docs/EVALUATION.md` describes the retrieval procedure: build, pool, estimate,
      export, judge, import, agreement, review, evaluate, compare. It also records the
      per-query batching choice. DECISIONS gets the row of this spec's labelling policy.
      BACKLOG gets a P1 row: the alert window starts after the previous gameweek's last match
      instead of at the previous deadline. Its trigger is spec 017's report showing stale
      claims or stale anchors in the GW6 window.
      ROADMAP and BACKLOG name spec 017 as the retrieval run and 018 as extraction and
      corroboration v2, wherever they said 017 before.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Two specs: 016 tooling, 017 the run and decisions; extraction v2 becomes 018 | one spec with an owner-attended second phase; a lettered 016b | the owner's choice: smaller PRs, and the pipeline's autonomous stages never need the owner mid-implementation; numbers keep the order retrieval → extraction v2 |
| Set v2 with a `player` slice that decides and a `free` slice (v1's queries) kept | the player slice only; v1 extended with GW6 queries | the player slice measures what corroboration does (#43); the free slice keeps the Polish and free-search check for the Stage 4 Q&A agent |
| Relevance for the player slice: information about the player's availability for his gameweek N match, denials included; binary | any mention of the player; graded 0/1/2 with nDCG; "his next match" at the time of the post | it is the set the corroboration judge can turn into supports, contradicts or related; tied to gameweek N, an earlier gameweek's line-up is not relevant; binary keeps DECISIONS 2026-09-29 |
| The player slice's window follows the alerts' rule today; an `after-last-match` rule and a stale flag on claims are measured beside it | changing the production window now; the corroboration-only `window_start` without the lookback | the evaluation measures production as it is, and the owner's concern (line-ups of the previous gameweek inside the window, possibly a stale anchor) gets numbers in 017 before the alerts change (the owner, 2026-10-15) |
| Primary metric: recall of unclaimed relevant posts at the judge's cut (top N minus claimed, first 10) | recall@10 of the raw ranking | retrieval's job in production is to surface what extraction missed; a player with many claimed posts can empty the cut (A1) |
| The session judge labels; the owner reviews disagreements between two judges plus a seeded sample; agreement is reported | the owner reviews every label (the 2026-09-29 rule); the owner reviews the player slice only | hundreds to over a thousand pairs; disagreements are where the judge is weakest; the sample measures the rest (the owner, 2026-10-15) |
| One judge call per query (split above 30 posts), each post labelled on its own, in seeded random order | one call per pair | lesson 5 of `docs/EVALUATION.md`: several times fewer tokens and one reading of the player per query; positional bias is limited by shuffling, and the choice is recorded |
| Query variants as code shared by evaluation and corroboration, production default unchanged | changing the production query now | the baseline is measured before the fix (A2); spec 017 picks the variant by its numbers |
| The shared module holds only the header, the write and the guard | a shared evaluation framework | DECISIONS 2026-09-29 row 54: the metrics differ per evaluation |
| `--max-cost-usd` default 1.00 on embedding a corpus | no guard | the owner's cap for the comparison is $1 |

## Owner decisions

- 2026-10-15: the work is split into 016 (tooling) and 017 (the run, the report, the ADR,
  rolling out the winners). Extraction and corroboration v2 become 018.
- 2026-10-15: set v2 has the production-shaped `player` slice, which decides, and v1's queries
  as the `free` slice.
- 2026-10-15: player-slice relevance is information about the player's availability for the
  gameweek N match (denials included), binary.
- 2026-10-15: the player slice uses the alerts' window rule today, and the builder can also
  start the window after the previous gameweek's last match. Stale claims are flagged and
  counted. The production window change waits for spec 017's numbers (BACKLOG P1).
- 2026-10-15: labels come from the Claude Code session judge, and the owner reviews a sample
  with every disagreement. This supersedes "reviewed by the owner" in DECISIONS 2026-09-29.
- For spec 017, recorded here so that it starts from them:
  - paid embedding API budget ≤ $1 for the comparison, which need not be spent;
  - a judge pilot on about 20 queries with Sonnet (medium effort) and Opus, with the cheaper
    judge chosen when its agreement with the owner holds;
  - session tokens ≤ 3M in total, estimated and shown before launch;
  - the winning query variant goes into corroboration;
  - the default embedding model switches only when a candidate beats
    `text-embedding-3-small` on the player slice by a set margin, with an ADR, and the
    owner re-indexes the Compose database before the merge.
- Agents may read the Compose database (read only) to export the corpus and build the player
  slice. Writes, migrations and re-indexing stay the owner's.
- `scripts/session_judge.sh` is parameterised in this spec, which closes #48 here.
- The shared run header covers all four evaluations in this spec.
- New dependencies: none expected. Data migrations: none, because the evaluation lives in the
  `retrieval_eval` schema and its own files.

## Open questions (non-blocking)

- The `free` slice's v1 labels came from `openai/gpt-6-luna`. Spec 017 decides whether the
  session judge relabels them or they are carried as they are.
- The margin for switching the embedding model and the rule for reranking (#16) are spec
  017's. The proposal is +0.05 recall at the judge's cut for a switch. For reranking: needed
  if recall at the cut is below 0.8 while recall at depth 50 is at least 0.1 higher.
