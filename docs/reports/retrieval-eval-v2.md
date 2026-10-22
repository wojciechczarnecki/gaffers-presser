# Retrieval evaluation on set v2 (GW6 and GW7 pre-deadline windows)

Spec 017, phase B. Result files: `backend/evals/retrieval/results/test-v2-*.json` (test split,
committed) and `results/dev/` (dev split, local). Procedure: `docs/EVALUATION.md`.

**In short.** The best configuration on the test split is `openai/text-embedding-3-large`,
hybrid mode, the `names` query variant, depth 20, with recall at the judge's cut of 0.933
against 0.906 for today's production configuration. The difference is about two queries out of
69, which is within noise, but the SPEC's rule has no margin, so it wins. Reranking is not
needed. The candidate `qwen/qwen3-embedding-8b` was dropped before the run for its embedding
throughput on OpenRouter, not for its quality.

## Set

- Corpus: 2557 posts from the list's 19 accounts, from 2026-09-18 17:30Z (the GW5 deadline) to
  2026-10-17 10:00Z (the GW7 deadline).
- `player` slice, built with the `alerts` window rule: 269 queries. GW6 has 82 test and 35 dev
  queries, GW7 has 106 test and 46 dev. The test split is 188 queries and the dev split 81.
- Players with a `web_name` initial (e.g. "B. Fernandes"): 7 in the test split and 1 in dev.
- The `free` slice (v1's queries) is not judged or reported here; it returns with the Stage 4
  Q&A agent (BACKLOG #56). `compare` also prints its table, which is left out below.

## Labelling

Phase A (PR #38, `78e82b6`) labelled the pooled posts: the union of every model's, mode's and
variant's top 50 for each query.

- Labels: 13,906 Opus pairs (`claude-opus-5-5 (session)`), 769 Sonnet pilot pairs (15 queries
  from `--query-sample 20 --seed 1`) and 139 pairs reviewed by the owner.
- Relevance prompt `retrieval_relevance_player` v3. Each version only narrowed relevance, so
  the pairs still relevant were judged again (v2: Opus 66 of 270 flipped, Sonnet 5 of 32; v3:
  Opus 42 of 204, Sonnet 3 of 27) and earlier not-relevant labels stand.
- Owner review by stratum (`review --relevant 30 --named 30 --rest 20 --seed 1`):

| stratum | pairs | reviewed |
|---|---|---|
| pilot: disagreement / relevant / named / rest | 2 / 25 / 12 / 730 | 2 / 25 / 12 / 20 |
| Opus only: relevant / named / rest | 179 / 756 / 12,202 | 30 / 30 / 20 |

| against the owner (weighted by stratum) | precision | recall | kappa |
|---|---|---|---|
| pilot, Opus | 1.000 | 1.000 | 1.000 |
| pilot, Sonnet | 0.958 | 0.958 | 0.957 |
| whole slice, Opus | 1.000 | 0.833 | 0.908 |

The two judges' kappa with each other on the pilot is 0.957. In the test result files' header
the estimate over the 104 owner-reviewed test pairs is precision 1.000, recall 0.759 and kappa
0.861 (`judge_owner_estimates`); the reference judge is `claude-opus-5-5 (session)`.

**Judge choice.** AC4's rule (precision and recall each at least 0.90 and no more than 0.05
below Opus's) points to Sonnet on the final pilot numbers. Opus had already labelled the whole
slice, and switching would have meant judging about 13k pairs again, so Opus stayed. Phase A
recorded this as a deviation and the PR was merged; the owner is asked to confirm it at the
final review gate.

Test split labels: 104 owner-reviewed and 9,582 judge-only (dev: 35 and 4,185).

## Relevance distribution

Reference labels are the owner's where reviewed and the judge's otherwise. On the test split:

- 121 relevant pairs among 10,292 pooled pairs (1.2%).
- 69 of the 188 queries have a relevant post, and each of them has an unclaimed relevant post.
  The other **119 queries have no unclaimed relevant post**, and recall at the cut leaves them
  out. With one relevant post a query's recall is a hit or a miss, and one query moves the mean
  by about 1.4 points.
- Relevant posts per query among those 69: minimum 1, median 1, maximum 11.
- Over the whole slice, 164 of 13,906 Opus-judged pairs are relevant (1.2%), and 175 of the 269
  queries have none.

## Tokens and spend

- Session judge tokens, phase A: about 5.8M (pilot 0.48M, Opus rest 3.98M, re-judges 1.33M).
  AC3 set 3M for one gameweek; the owner accepted the excess on 2026-10-22 (GW7 and two
  re-judges).
- Paid embedding spend, phase A: about $0.08 (four corpus exports).
- Paid embedding spend, phase B: about $0.072, an estimate from the runs' token counts and
  `prices.toml` (three models, a test and a dev run each, about 120k tokens a run). Each run was
  started with `--max-cost-usd 0.10`. The per-run estimates are $0.0024 (3-small), $0.0157
  (3-large) and $0.0181 (gemini) for a test run, and the same for a dev run.
- Total about $0.15, against the owner's cap of $1.

## The compare table

The `player` slice, test split, 188 queries (recall counts the 69 with an unclaimed relevant
post). `recall@cut` is the recall of unclaimed relevant posts within the judge's cut of 10;
`recall@depth` is the same within the search depth, with claimed posts dropped and no cut to 10.
Output of `python -m app.retrieval.evaluation compare evals/retrieval/results/test-v2-*.json`:

```
model                             mode      variant     depth  recall@cut  recall@depth  recall@10  empty  unjudged
google/gemini-embedding-001       fulltext  web_name       20       0.885         0.892      0.843      3     0.000
google/gemini-embedding-001       fulltext  web_name       30       0.890         0.936      0.843      3     0.000
google/gemini-embedding-001       fulltext  web_name       50       0.890         0.946      0.843      3     0.000
google/gemini-embedding-001       fulltext  full_name      20       0.918         0.929      0.833      1     0.000
google/gemini-embedding-001       fulltext  full_name      30       0.918         0.951      0.833      1     0.000
google/gemini-embedding-001       fulltext  full_name      50       0.918         0.977      0.833      1     0.000
google/gemini-embedding-001       fulltext  names          20       0.914         0.917      0.872      1     0.000
google/gemini-embedding-001       fulltext  names          30       0.919         0.965      0.872      1     0.000
google/gemini-embedding-001       fulltext  names          50       0.919         0.975      0.872      1     0.000
google/gemini-embedding-001       vector    web_name       20       0.485         0.535      0.471      0     0.000
google/gemini-embedding-001       vector    web_name       30       0.485         0.595      0.471      0     0.000
google/gemini-embedding-001       vector    web_name       50       0.485         0.677      0.471      0     0.000
google/gemini-embedding-001       vector    full_name      20       0.757         0.776      0.693      0     0.000
google/gemini-embedding-001       vector    full_name      30       0.762         0.847      0.693      0     0.000
google/gemini-embedding-001       vector    full_name      50       0.762         0.889      0.693      0     0.000
google/gemini-embedding-001       vector    names          20       0.470         0.531      0.459      0     0.000
google/gemini-embedding-001       vector    names          30       0.470         0.563      0.459      0     0.000
google/gemini-embedding-001       vector    names          50       0.470         0.645      0.459      0     0.000
google/gemini-embedding-001       hybrid    web_name       20       0.902         0.910      0.859      0     0.000
google/gemini-embedding-001       hybrid    web_name       30       0.907         0.951      0.859      0     0.000
google/gemini-embedding-001       hybrid    web_name       50       0.907         0.971      0.859      0     0.000
google/gemini-embedding-001       hybrid    full_name      20       0.891         0.940      0.840      0     0.000
google/gemini-embedding-001       hybrid    full_name      30       0.891         0.961      0.840      0     0.000
google/gemini-embedding-001       hybrid    full_name      50       0.891         0.984      0.840      0     0.000
google/gemini-embedding-001       hybrid    names          20       0.914         0.924      0.865      0     0.000
google/gemini-embedding-001       hybrid    names          30       0.919         0.963      0.865      0     0.000
google/gemini-embedding-001       hybrid    names          50       0.919         0.986      0.865      0     0.000
openai/text-embedding-3-large     fulltext  web_name       20       0.885         0.892      0.843      3     0.000
openai/text-embedding-3-large     fulltext  web_name       30       0.890         0.936      0.843      3     0.000
openai/text-embedding-3-large     fulltext  web_name       50       0.890         0.946      0.843      3     0.000
openai/text-embedding-3-large     fulltext  full_name      20       0.918         0.929      0.833      1     0.000
openai/text-embedding-3-large     fulltext  full_name      30       0.918         0.951      0.833      1     0.000
openai/text-embedding-3-large     fulltext  full_name      50       0.918         0.977      0.833      1     0.000
openai/text-embedding-3-large     fulltext  names          20       0.914         0.917      0.872      1     0.000
openai/text-embedding-3-large     fulltext  names          30       0.919         0.965      0.872      1     0.000
openai/text-embedding-3-large     fulltext  names          50       0.919         0.975      0.872      1     0.000
openai/text-embedding-3-large     vector    web_name       20       0.501         0.570      0.442      0     0.001
openai/text-embedding-3-large     vector    web_name       30       0.501         0.702      0.442      0     0.001
openai/text-embedding-3-large     vector    web_name       50       0.501         0.784      0.442      0     0.001
openai/text-embedding-3-large     vector    full_name      20       0.644         0.683      0.573      0     0.001
openai/text-embedding-3-large     vector    full_name      30       0.649         0.817      0.573      0     0.001
openai/text-embedding-3-large     vector    full_name      50       0.649         0.872      0.573      0     0.001
openai/text-embedding-3-large     vector    names          20       0.499         0.560      0.440      0     0.001
openai/text-embedding-3-large     vector    names          30       0.499         0.671      0.440      0     0.001
openai/text-embedding-3-large     vector    names          50       0.499         0.751      0.440      0     0.001
openai/text-embedding-3-large     hybrid    web_name       20       0.921         0.933      0.861      0     0.000
openai/text-embedding-3-large     hybrid    web_name       30       0.921         0.952      0.861      0     0.000
openai/text-embedding-3-large     hybrid    web_name       50       0.921         0.984      0.861      0     0.000
openai/text-embedding-3-large     hybrid    full_name      20       0.920         0.942      0.834      0     0.000
openai/text-embedding-3-large     hybrid    full_name      30       0.924         0.966      0.834      0     0.000
openai/text-embedding-3-large     hybrid    full_name      50       0.924         0.998      0.834      0     0.000
openai/text-embedding-3-large     hybrid    names          20       0.933         0.941      0.874      0     0.000
openai/text-embedding-3-large     hybrid    names          30       0.933         0.960      0.874      0     0.000
openai/text-embedding-3-large     hybrid    names          50       0.933         0.998      0.874      0     0.000
openai/text-embedding-3-small     fulltext  web_name       20       0.885         0.892      0.843      3     0.000
openai/text-embedding-3-small     fulltext  web_name       30       0.890         0.936      0.843      3     0.000
openai/text-embedding-3-small     fulltext  web_name       50       0.890         0.946      0.843      3     0.000
openai/text-embedding-3-small     fulltext  full_name      20       0.918         0.929      0.833      1     0.000
openai/text-embedding-3-small     fulltext  full_name      30       0.918         0.951      0.833      1     0.000
openai/text-embedding-3-small     fulltext  full_name      50       0.918         0.977      0.833      1     0.000
openai/text-embedding-3-small     fulltext  names          20       0.914         0.917      0.872      1     0.000
openai/text-embedding-3-small     fulltext  names          30       0.919         0.965      0.872      1     0.000
openai/text-embedding-3-small     fulltext  names          50       0.919         0.975      0.872      1     0.000
openai/text-embedding-3-small     vector    web_name       20       0.509         0.532      0.470      0     0.001
openai/text-embedding-3-small     vector    web_name       30       0.509         0.585      0.470      0     0.001
openai/text-embedding-3-small     vector    web_name       50       0.509         0.655      0.470      0     0.001
openai/text-embedding-3-small     vector    full_name      20       0.644         0.697      0.569      0     0.001
openai/text-embedding-3-small     vector    full_name      30       0.649         0.809      0.569      0     0.001
openai/text-embedding-3-small     vector    full_name      50       0.649         0.830      0.569      0     0.001
openai/text-embedding-3-small     vector    names          20       0.498         0.526      0.458      0     0.001
openai/text-embedding-3-small     vector    names          30       0.498         0.608      0.458      0     0.001
openai/text-embedding-3-small     vector    names          50       0.498         0.664      0.458      0     0.001
openai/text-embedding-3-small     hybrid    web_name       20       0.906         0.926      0.869      0     0.001
openai/text-embedding-3-small     hybrid    web_name       30       0.906         0.953      0.869      0     0.001
openai/text-embedding-3-small     hybrid    web_name       50       0.906         0.955      0.869      0     0.001
openai/text-embedding-3-small     hybrid    full_name      20       0.918         0.935      0.853      0     0.000
openai/text-embedding-3-small     hybrid    full_name      30       0.918         0.960      0.853      0     0.000
openai/text-embedding-3-small     hybrid    full_name      50       0.918         0.986      0.853      0     0.000
openai/text-embedding-3-small     hybrid    names          20       0.913         0.935      0.878      0     0.001
openai/text-embedding-3-small     hybrid    names          30       0.913         0.965      0.878      0     0.001
openai/text-embedding-3-small     hybrid    names          50       0.913         0.984      0.878      0     0.001
```

The full-text rows are identical across models, as they must be. Vector-only rows are far below
the others (0.47 to 0.76), and hybrid is on top for the models that embed well.

## Winner

The rule (AC7), applied to the exact values in the result files and not to the 3-decimal table:

1. The highest recall at the cut is 0.933319, shared by three rows:
   `openai/text-embedding-3-large`, hybrid, `names`, at depths 20, 30 and 50.
2. Production's configuration (`text-embedding-3-small`, hybrid, `web_name`, 20) is not among
   them: it scores 0.9058.
3. The model is the same in all three rows, so the tie goes to the smaller depth: 20.

**Winner: `openai/text-embedding-3-large`, hybrid, `names`, depth 20.** In production:
`DEFAULT_EMBEDDING_MODEL = openai/text-embedding-3-large`, `QUERY_VARIANT = "names"`,
`SEARCH_MODE = "hybrid"`, `SEARCH_LIMIT = 20` (`MAX_JUDGED` stays 10).

Best per model, by the same rule:

| model | price per million tokens | best recall@cut | mode, variant, depth |
|---|---|---|---|
| openai/text-embedding-3-large | $0.13 | 0.933 | hybrid, names, 20 |
| openai/text-embedding-3-small (production) | $0.02 | 0.919 | full-text, names, 30 |
| google/gemini-embedding-001 | $0.15 | 0.919 | full-text, names, 30 |

The full-text rows do not use the embedding, so 3-small and gemini tie on them; the best hybrid
row of 3-small is 0.918 (`full_name`). The margin of 3-large over production's own
configuration (0.906) is 0.027, about two queries of 69, **within the noise: differences below
about 0.05 on 69 queries are not significant**. The rule has no margin by the owner's decision,
so the best row wins; this report does not claim a proven improvement.

## Misses

The five worst test queries for the winner (unclaimed relevant posts missing from the cut of 10):

| query | player | relevant | found | recall |
|---|---|---|---|---|
| p-gw7-368 | Szoboszlai | 1 | 0 | 0.00 |
| p-gw7-391 | Gvardiol | 1 | 0 | 0.00 |
| p-gw7-411 | Haaland | 1 | 0 | 0.00 |
| p-gw6-411 | Haaland | 9 | 4 | 0.44 |
| p-gw6-31 | Konsa | 11 | 5 | 0.45 |

The next are `p-gw6-165` (Joao Pedro) and `p-gw7-398` (Foden), each 3 of 4. The three zero rows
are single-post queries, a plain miss. For Haaland (GW6) and Konsa, more relevant posts than the
cut of 10 can hold compete for it.

## Reranking

AC9 asks for a reranker when both of these hold for the winner on the test split:

- recall at the cut below 0.80: it is 0.933, so **no**;
- recall within the top 50 at least 0.10 higher than at the cut: the same model, mode and variant
  at depth 50 gives 0.998, which is 0.065 higher, so **no** either.

**Decision: reranking is not needed.** BACKLOG #16 becomes P3 with the trigger "a later
retrieval evaluation meets the rule".

## Stale claims and anchors

A claim is stale when it is older than the end of the previous gameweek's last match, so the old
window counts it though it belongs to the earlier gameweek.

| gameweek | claims | stale claims | queries with claims | stale anchors |
|---|---|---|---|---|
| GW6 | 343 | 0 | 117 | 0 |
| GW7 | 332 | 121 | 152 | 48 |

GW6 shows none because of the international break: its window starts two weeks after GW5's last
match. GW7 shows 121 of 332 claims. In the test split alone there are 98 stale claims and 35
stale anchors. This is what BACKLOG #53 (the new window start, delivered in this spec) addresses.
The frozen windows of set v2 were built with the old `alerts` rule and do not change with it.

## Limits

- Two gameweeks and **69 test queries** counted by recall at the cut (of 188): one query is
  about 1.4 points, and differences below about 0.05 are within noise.
- Labels of one query are not independent: one judge call labels all of a query's pooled posts.
- Labels of several prompt versions: each later version only narrows relevance, so a
  not-relevant label of an earlier version stands.
- The reference judge is Opus where AC4's rule points to Sonnet (see Labelling). The owner's
  review estimates Opus's recall on relevant pairs at 0.83 over the whole slice (0.76 on the
  test split's reviewed pairs), so some relevant posts are labelled not relevant, and every
  recall is measured against that reference.
- An unjudged post counts as not relevant. Re-embedding moved a few ranks against phase A's pool:
  the unjudged share is at most 0.0011 in the test files (3-small, vector and some hybrid rows),
  0.0005 for 3-large (vector rows), and 0 for gemini and for every full-text row.
- `qwen/qwen3-embedding-8b` was a candidate and was dropped before the run: OpenRouter embedded
  about 100 posts per 10 minutes with repeated read timeouts, over 15 hours for one test run
  (owner decision, 2026-10-22). It says nothing about the model's quality. Its price row stays.
- The `free` slice is not covered.
