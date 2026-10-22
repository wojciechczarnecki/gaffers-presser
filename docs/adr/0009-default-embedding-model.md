# 0009 — Default embedding model and the corroboration search

Status: accepted (2026-10-22)

## Context

Spec 006 chose `openai/text-embedding-3-small`, hybrid search, the bare `web_name` as the query
and a depth of 20 without a measurement on real pre-deadline data. Spec 016 built the tooling
and spec 017 phase A built set v2: 2557 posts, a `player` slice of 269 queries (one per player
with a claim in a gameweek's alert window, GW6 and GW7) and relevance labels (Opus as the
reference judge, the owner's stratified review, prompt v3). Spec 017 phase B ran every model on
the test split (188 queries, of which 69 have an unclaimed relevant post) in three modes, three
query variants and at depths 20, 30 and 50, and measured recall at the judge's cut of 10. The
rule, set before the run: the highest recall at the cut wins with no margin; a tie goes to
production's configuration, then the cheaper model, the smaller depth and the simpler variant.
The full tables are in the [report](../reports/retrieval-eval-v2.md).

## Options

Best recall at the cut per model (test split, exact values from the result files):

| Model | Price per million tokens | Recall at the cut | Mode, variant, depth |
|---|---|---|---|
| `openai/text-embedding-3-large` | $0.13 | 0.933 | hybrid, `names`, 20 |
| `openai/text-embedding-3-small` (the old default) | $0.02 | 0.919 | full-text, `names`, 30 |
| `google/gemini-embedding-001` | $0.15 | 0.919 | full-text, `names`, 30 |
| `qwen/qwen3-embedding-8b` | $0.01 | not run | dropped before the run |

The two 0.919 rows are full-text rows, which do not use the embedding. The old default's own
configuration (hybrid, `web_name`, 20) scores 0.906, and its best hybrid row 0.918. Qwen was a
candidate, but OpenRouter embedded about 100 posts per 10 minutes with repeated read timeouts
(over 15 hours for one test run), and the owner dropped it on 2026-10-22; that says nothing
about its quality.

## Decision

- **Embedding model: `openai/text-embedding-3-large`** (`DEFAULT_EMBEDDING_MODEL`).
- **Corroboration search:** hybrid mode (`SEARCH_MODE`), the `names` query variant
  (`QUERY_VARIANT`) and depth 20 (`SEARCH_LIMIT`). `MAX_JUDGED` stays 10.
- **Reranking is not needed**: the winner's recall at the cut is 0.933 (not below 0.80), and its
  recall within the top 50 is 0.998, only 0.065 higher (not 0.10). BACKLOG #16 is P3.
- Rejected: keeping `text-embedding-3-small` (0.906 for its own configuration, 0.027 below the
  winner); `gemini-embedding-001` (the dearest model, hybrid no better than 3-large's: 0.919 at
  best); a full-text-only search (its best, 0.919, is below the winner, and it ignores the
  vector leg that RRF fuses).

## Consequences

- The difference between the winner and the old configuration is about two queries of 69, within
  the noise of this set (differences below about 0.05 are not significant). The choice follows the
  rule the owner set, which has no margin, and it is not a proven improvement. A later, larger
  set can reverse it, and the cost of switching back is small.
- The embedding cost is negligible: about 120k tokens for the corpus, so about $0.016 at
  $0.13 per million.
- A model change needs a re-index of the owner's Compose database
  (`uv run python -m app.retrieval index --model openai/text-embedding-3-large`, the owner's
  operation). Until then hybrid degrades to full-text with the vector leg marked failed
  (DECISIONS 2026-09-29), and an `EMBEDDING_MODEL` in the owner's `.env` overrides the new
  default. Embeddings are keyed by (post, model), so the old ones stay and the change is
  reversible.
- The labels are one judge's and the window frozen at GW6 and GW7; see the report's limits.
