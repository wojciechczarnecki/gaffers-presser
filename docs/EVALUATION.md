# Evaluation with a session judge

How to run an LLM-judged evaluation with the Claude Code session as the judge instead of a
paid API model. The models under test still run through OpenRouter; only the judge moves into
the session. Decided on 2026-10-12 (DECISIONS) for the presser model comparison (BACKLOG #32).

## When to use it

- A model comparison or a prompt check that a person starts and watches. The judge is then
  about 90% of the paid cost (the presser test split: writers about $0.8 for five models,
  the API judge about $6), and the owner's budget for one check is about $5.
- Not for unattended runs (an evaluation regression in CI, Stage 5): nobody is there to run
  the session, so those keep an API judge.

## Rules that keep it comparable with an API judge

- **The same prompt and the same input.** The judge reads the exported prompt file (the
  versioned prompt from `app/content/prompts/`) and the input file holding exactly the
  user message the API judge would get (`render_input`). Nothing else: no repository, no
  other cases, no other models' outputs.
- **A fresh context per item.** One headless `claude -p` call per judged item, like one API
  call per item. A call never judges two items, so it cannot compare them or drift.
- **Blind.** The input directories are named `A`, `B`, … and the mapping to models is kept
  outside them until the verdicts are imported.
- **The same output schema**, validated on import (`JudgeVerdict` for the presser); an
  invalid file fails the import and the item is judged again by a fresh call.
- **Recorded.** The run file's `judge_model` names the session judge, e.g.
  `claude-opus-5-5 (Claude Code session)`, and keeps `judge_prompt_version`; the report says
  who judged. `judge_cost_usd` stays empty (the judges count against the Claude subscription,
  not OpenRouter).
- **Vendor overlap is reported, not avoided.** Every judge shares a vendor with some
  candidate (the API judge `openai/gpt-6.1-sol` with `openai/gpt-6-luna`, the session judge
  with `anthropic/claude-haiku-5.5`); the report names the overlap, and the owner's
  `judge-review` on sampled pressers measures the judge's agreement either way.

## The presser procedure

```bash
cd backend
# 1. Writers only, paid through OpenRouter (one run file per model)
uv run python -m app.presser.evaluation evaluate --split test --model MODEL --no-judge

# 2. Blind export: one directory per run, named A, B, ... (keep the mapping outside)
uv run python -m app.presser.evaluation export-judge-inputs \
    --run evals/presser/results/test-MODEL.json --out $SCRATCH/judge/A/inputs

# 3. One headless judge call per input, 6 in parallel; rerun to retry what failed
../scripts/session_judge.sh $SCRATCH/judge/A 6 opus

# 4. Import per run; review, judge-review and summary then work as with an API judge
uv run python -m app.presser.evaluation import-judge \
    --run evals/presser/results/test-MODEL.json --verdicts $SCRATCH/judge/A/verdicts \
    --judge-model "claude-opus-5-5 (Claude Code session)"
```

`scripts/session_judge.sh` runs `claude -p` outside the repository with the judge prompt as
the system prompt, the input as the only message and no tools, so one item is one model turn.
It writes the bare JSON to `verdicts/` and the CLI result with its token usage to `raw/`.

## Lessons from the first run (presser comparison, 2026-10-12)

The first session-judged comparison (5 models × 24 test pressers = 120 items) used one
`general-purpose` subagent per item, orchestrated from the main session. It cost about 5.8M
tokens of the Claude subscription and hit the session limit after 91 of 120 verdicts. What
made it expensive, and what to do instead:

1. **Estimate before fanning out.** Tokens per item × items, stated to the owner before the
   run, with a warning when it may reach the session limit; spread a large run across limit
   windows rather than finding out mid-run.
2. **One model turn per item, not a tool loop.** A subagent read three files and wrote one
   (about 5 tool calls), and every call re-sent its whole context: about 45–70k tokens per
   item. A headless call with the prompt and the input inline and the verdict in the reply is
   one turn: about 25–30k tokens for the largest real case, less for the synthetic ones.
3. **No heavy agent type.** `general-purpose` carries every tool and a long system prompt;
   the headless call carries the judge prompt only (`--system-prompt`, `--tools ""`).
4. **Keep the orchestrator's context out of the loop.** Each subagent's completion woke the
   main session, which re-read its growing context about 120 times — likely a large share of
   the cost. A shell script running the calls in parallel (`xargs -P`) writes only files; the
   main session reads the summary once.
5. **Consider one call per case instead of one per item** when comparing models: one judge
   sees the case's fact sheet once and judges every model's presser for it, in shuffled order
   and unnamed. That is several times fewer tokens and labels the models by the same reading
   of the facts; the cost is that verdicts on one case are no longer independent. Decide per
   run and write the choice in the report.
6. **Try a cheaper judge on a sample first.** Opus judged every item. Before a large run,
   judge a handful of items with Sonnet (or Opus at a lower `--effort`) as well and compare
   with the owner's `judge-review`; switch when the agreement holds.

## The retrieval procedure

Retrieval's labels come from the session judge too (spec 016; DECISIONS 2026-10-15): the judge
labels, the owner reviews every disagreement between two judges plus a seeded sample, and every
run records how far the judge agrees with the owner. The same rules as above apply (the
versioned prompt, a fresh context per call, blind inputs, a validated schema, the judge named),
with one change in the unit of a call: **one call per query** (see below). Commands run from
`backend/` as `python -m app.retrieval.evaluation <command>`; a set lives in a directory
(`--set`, default `evals/retrieval/v2`) with `corpus.jsonl`, `queries.jsonl` and `set.json`.

**Run the commands that load a corpus against a scratch database.** `evaluate` and
`export-judge-inputs` drop and recreate the `retrieval_eval` schema in the database
`DATABASE_URL` names. The Compose database is production (DECISIONS 2026-10-15): only
`export-corpus` and `build-player-slice` read it, and both are read-only.

```bash
# 1. Build the set (read-only on the Compose database)
uv run python -m app.retrieval.evaluation export-corpus --until 2026-10-10T10:00Z
uv run python -m app.retrieval.evaluation build-player-slice --gameweek 6 [--window after-last-match]
uv run python -m app.retrieval.evaluation carry-v1          # v1's queries and labels as the free slice

# 2. Pool and export: the posts nobody has judged yet, for every model, mode and variant
uv run python -m app.retrieval.evaluation export-judge-inputs --out $SCRATCH/judge \
    --embedding-model openai/text-embedding-3-small --embedding-model OTHER/MODEL \
    [--query-sample 20 --seed 1] [--for-judge NAME]

# 3. Estimate, then judge, one headless call per input
uv run python -m app.retrieval.evaluation estimate --export $SCRATCH/judge
../scripts/session_judge.sh $SCRATCH/judge/player 6 sonnet retrieval medium
../scripts/session_judge.sh $SCRATCH/judge/free 6 sonnet retrieval medium

# 4. Import, compare two judges, review blind
uv run python -m app.retrieval.evaluation import-judge --export $SCRATCH/judge \
    --judge-model "claude-sonnet-5-5 (Claude Code session)"
uv run python -m app.retrieval.evaluation agreement --judge NAME_A --judge NAME_B
uv run python -m app.retrieval.evaluation review --relevant 30 --named 30 --rest 20 --seed 1

# 5. One run per embedding model, then one table
uv run python -m app.retrieval.evaluation evaluate --split test --reference-judge NAME \
    --embedding-model MODEL
uv run python -m app.retrieval.evaluation compare evals/retrieval/results/test-v2-*.json
```

- **The `free` slice is not judged until Stage 4** (BACKLOG #56): it decides nothing in production
  today. For set v2 only the `player` slice's numbers count: `evaluate` and `compare` still print a
  `free` table, which is unjudged and to be ignored.
- **The `alerts` window rule now follows the production floor** (DECISIONS 2026-10-22: the
  previous gameweek's last match plus 2 hours, counting only matches that have ended). A rebuild
  of set v2's `player` slice would therefore give other windows than the frozen set, whose
  windows were built with the old rule (the previous deadline).

- **Pooling extends, it never restarts.** `export-judge-inputs` pools what the configured runs
  would show: for the `player` slice the judge's cut at the largest depth of each ranking
  `evaluate` makes (depths up to 50 share one search at leg depth 50, a deeper depth has its
  own), for every mode, variant and model given; for the `free` slice the top 10 per mode and
  model. It exports only
  the posts a query has no judgement for, so a new model costs only its unseen posts. Run it
  again with `--for-judge NAME` for a second judge: it exports the posts that judge has not
  labelled, the first judge's included, and `import-judge` stores such an export only under
  that judge's name (never under `owner`). The cost of embedding the corpus is estimated and
  summed over the models before any call, and the command stops above `--max-cost-usd`
  (default 1.00).
- **One call per query, not per pair.** An input holds one query (the player, the club, the
  gameweek and its deadline, or the free query) and its unjudged posts under neutral IDs
  `P1`, `P2`, … in a seeded random order, each with its author and time; the judge labels each
  post on its own and replies with `{"labels": {"P1": true, ...}}`. A query with more than
  `--max-posts` (default 30) posts is split into several inputs. This is lesson 5 above: several
  times fewer tokens than a call per pair and one reading of the player per query; the cost is
  that the labels of one query are not independent, which the shuffle limits and the report must
  state. The mapping from neutral IDs to X IDs is `mapping.json` in the export directory, outside
  every `inputs/` directory.
- **Estimate before every run** (lesson 1): `estimate` prints the calls and the tokens of an
  export, characters / 4 plus 3000 tokens of overhead per call, before one call is made.
  `scripts/session_judge.sh DIR PARALLEL MODEL retrieval [EFFORT]` judges only the inputs
  without a verdict; a reply that misses a label moves to `raw/<id>.invalid.json` and the next
  run calls the judge again for that input only.
- **The pilot.** `--query-sample N --seed S` exports a seeded subset of queries, drawn over both
  slices. Judge it with Sonnet at medium effort and with Opus, run `agreement`, and let the
  owner `review` it: switch to the cheaper judge when its precision and recall against the
  owner hold (lesson 6). The second judge can judge a copy of the same export directory instead
  of a `--for-judge` export, which saves embedding the corpus again.
- **Review by stratum; raw agreement misleads.** Relevant pairs are a few per cent of a pool
  (about 4% on GW6), so a judge calling everything not relevant agrees about 0.96 and a random
  sample of 30 holds about one relevant pair. A pair's stratum is the judges that labelled it
  and its category: a disagreement, relevant for every judge, not relevant with the player named
  in the post (`web_name`, `second_name` or an alias as a word with up to three letters of a
  Polish ending, accents folded), or the rest. `review --relevant N --named N --rest N` shows
  every disagreement, then a seeded sample of N pairs of each other stratum, and N applies to
  each set of judges apart (the pilot's pairs and the rest's are sampled separately).
  `agreement` needs the corpus; it prints the judges' kappa with each other and, once the owner
  has reviewed, each judge's precision, recall, kappa and agreement against the owner on the
  pairs both judges labelled, with each reviewed pair weighted by its stratum's size over its
  reviewed pairs. A stratum with no reviewed pair is left out and counted, so the run header's
  `judge_owner_estimates` cover the whole pool only when every stratum was sampled. The weights
  assume each stratum's reviewed pairs are a seeded sample of it, so a set is reviewed in one
  mode: the stratified one, or `--sample` alone. The stratified review covers the `player`
  slice only, and its first session saves the strata to `strata.json` in the set before any
  answer, so it runs once every judge has labelled the pool. A pair judged again later (a new
  prompt version) keeps the stratum it was sampled from; a pair labelled after that, labelled
  by another judge since, or turned into a disagreement falls in a `(new)` stratum (a new
  disagreement is shown like any other), and a pair with no judge label left is dropped.
  `agreement` and the run header read the file, and a run checks it before any embedding.
  Delete it only together with the owner's review labels, to draw new strata, and after
  `build-player-slice --force`, whose query IDs may then name other players.
- **The review is blind.** `review` shows every pair the judges disagree on, then a seeded
  sample of the other pairs, with the player or the query and the post but no judge's label,
  and stores the owner's answer as a reviewed label beside the judges'. The reference label of
  a pair is the owner's if there is one, otherwise `--reference-judge`'s; each run's header
  records the reference judge, the counts of owner-reviewed and judge-only labels and the
  judge's agreement with the owner on the reviewed pairs (raw, and weighted by stratum with
  precision, recall and kappa as `judge_owner_estimates`), and the relevance prompt versions
  behind the judge labels it measured against (each imported label keeps its export's
  version). An evaluation with no label from the reference judge or the owner refuses to run,
  and `compare` refuses result files of different sets, splits, reference judges or `k`, and
  prints each file's depths.
- **Read the unjudged share.** Every table reports the share of the shown posts (the judge's
  cut, or the top 10) with no reference label; a configuration with a high share is measured
  on a pool that never contained its results.
- **Corpus bound.** `carry-v1` needs v1's judged posts in the v2 corpus, so export it without
  `--since`, or with one no later than v1's earliest post; only `--until` ends it at the deadline.
- **Windows.** `build-player-slice --window alerts` (default) measures production as it is;
  `after-last-match` starts after the previous gameweek's last match (its latest kickoff plus
  two hours). Under either rule a claimed post published before that end is flagged stale, and
  the run reports stale claims and stale anchors.

## Other evaluations

The corroboration judge (`app/corroboration/evaluation/`) and extraction have no
`--no-judge` / export / import commands yet: their evaluations compare a model's labels
with the owner's reviewed labels, so a judge is only needed where a model pre-labels a set.
To judge in the session there, add the same three pieces to that evaluation's CLI: a flag
that skips the API judge, an export of the prompt and the exact rendered input per item,
and an import that validates the verdicts with the judge's schema and recomputes the totals.

`scripts/session_judge.sh` takes the verdict schema as its fourth argument (`presser`, the
default, or `retrieval`), so another evaluation adds a schema there instead of a script.
