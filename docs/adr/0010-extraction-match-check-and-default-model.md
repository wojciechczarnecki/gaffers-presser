# 0010 — Extraction match check and the default model on set v2

Status: accepted (2026-10-26). Confirms the default and fallback of
[ADR 0006](0006-default-extraction-model-openrouter.md) on the new evidence; it does not
supersede it.

## Context

The GW6 deadline window showed line-ups of other matches (a cup tie, an international, a
European night) read as line-ups for the next Premier League match (BACKLOG #54). The 60 h
filter of DECISIONS 2026-10-24 drops a `confirmed` line-up only when the club's next kickoff
is far away, so a line-up of another match inside that window passes. Spec 018 adds a check
that knows which match a post is about, relabels the extraction set as v2 (228 cases, the
test split has 156) and re-runs the six models of ADR 0006 with the frozen prompts
(`extraction@5`, `link_disambiguation@1`, `match_check@1`). The full tables, the dev
iterations and the threats to validity are in the
[report](../reports/extraction-eval-v2.md).

## Options

The match check, against the SPEC's alternatives:

- **A: a table of 20 clubs' fixtures in the main prompt.** Every call pays for it, and the
  club is still left to the model's memory.
- **B (chosen): a node after linking.** One call per post and club for linked line-up
  events, given that club's previous and next Premier League fixture from the FPL data. It
  costs a call only for line-up posts, and it is measurable on its own
  (`lineups_dropped_by_check`, `other_match_false_alarms`).
- **C: separate "who" and "what" calls for every post.** It fixes linking, which already
  scores above 0.95 for five of the six models, and doubles the calls and the latency of every post.
- **D: the LLM extracts the named match, code decides.** It fails when the post does not
  name the match, the typical case ("Haaland on the bench" during City v AEK).

Test-split results of the six models (156 cases, the whole flow, every step with the model
under test; a cross in "Thresholds" means fewer than five of the five thresholds hold):

| Model | F1 | Linking | False alarms | Other-match false alarms | Line-ups dropped by the check | PLN / month | Errored cases | Thresholds |
|---|---|---|---|---|---|---|---|---|
| `openai/gpt-6-luna` | 0.863 | 0.977 | 4.1% | 2 of 29 | 7 | 0.84 | 0 | 5 of 5 |
| `qwen/qwen3.8-flash` | 0.869 | 0.970 | 3.1% | 2 of 29 | 1 | 1.37 | 0 | 5 of 5 |
| `google/gemini-3.1-flash-lite` | 0.918 | 0.980 | 7.2% (x) | 2 of 29 | 2 | 2.39 | 0 | 4 of 5 |
| `anthropic/claude-haiku-4.5` | 0.911 | 0.976 | 4.1% | 2 of 29 | 2 | 14.30 (x) | 0 | 4 of 5 |
| `z-ai/glm-5.3-flash` (repeat) | 0.923 | 0.980 | 5.4% (x) | 2 of 29 | 5 | 2.11 | 4 (x) | 3 of 5 |
| `deepseek/deepseek-v4-flash` | 0.493 (x) | 0.948 (x) | 5.2% (x) | 2 of 29 | 146 | 1.35 | 0 | 2 of 5 |

`z-ai/glm-5.3-flash` errored in its first run too (5 cases, F1 0.902); the repeat replaces
it. `deepseek/deepseek-v4-flash` answered "not the next match" for almost every line-up, so
the check is model-dependent and its F1 collapses.

## Decision

- **The match check is architecture B,** in the extraction flow after linking.
- **Storage at read time.** The answer is a nullable column
  `extraction_event.for_next_pl_match` (migration 0014): true, false or null. The stored
  events stay unchanged, and `current_extractions(fresh_lineups_only=True)` drops a false
  answer through one predicate (`kept_at_read`) that `evaluate` uses too. It sits beside
  the 60 h filter, which stays: rows extracted before v5 have null and depend on it, and it
  is a cheap net under the check. The rule can be switched off without re-extraction.
- **Fail-open.** A failed or unparsable match check stores null and keeps the event. A real
  line-up is never lost to a provider error, and a `confirmed` one still meets the 60 h
  filter.
- **Default: `openai/gpt-6-luna`, unchanged.** Rule: the cheapest model passing every
  threshold. Luna and qwen pass, and luna costs 0.84 PLN a month against 1.37. The margin
  is thin on F1 (0.863 against 0.85), on a 156-case test split, and the set's line-up
  over-sampling overstates the cost of the check.
- **Fallback: `google/gemini-3.1-flash-lite`, unchanged.** Qwen is the best other model,
  but it answers by `json_schema` while luna answers by `function_calling`, so the pair rule
  of ADR 0006 excludes it, as does GLM's mandatory reasoning. Gemini is the best compatible
  model within 5 PLN a month (4 of 5 thresholds, F1 0.918, 2.39 PLN). It is a fallback for
  an outage, not a second default: its false-alarm rate (7.2%) misses the threshold.
- **The "no model passes" rule.** If no model passes every threshold, the one passing the
  most thresholds wins, then the higher F1, among the models at or under 5 PLN a month; the
  cost limit is hard. `choose_models` (`app/extraction/evaluation/selection.py`) applies the
  rule to the committed result files, and a test pins `DEFAULT_MODEL` and
  `DEFAULT_FALLBACK_MODEL` in `app/llm/chat.py` to its output.

## Consequences

- Other-match line-ups are filtered where the post is read, with a trace in `review` and in
  SQL. In most runs two of the 29 tagged cases still leave a surviving event (an unlinked
  `benched` event and an `out` event, which neither filter targets). On set v2's test split
  every tagged line-up the check dropped was one the 60 h filter alone would also have
  dropped (one exception, in a repeat run of GLM), so the check's gain over the filter is not
  yet measured: the set has no other-match line-up at `likely`/`rumour` certainty or inside
  60 h of the next kickoff.
- The synthetic line-up cases were re-dated before their match (owner decision 2026-10-26),
  otherwise the check rightly dropped them.
- Linking misses that cost F1 are BACKLOG #57.
- The owner may run `reextract` on stored GW8+ posts to apply prompt v5 and the check to
  them; it is outside this spec.
