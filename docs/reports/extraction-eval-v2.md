# Extraction model comparison — evaluation set v2

Spec 018, run on 2026-10-26. Decision: [ADR 0010](../adr/0010-extraction-match-check-and-default-model.md), which confirms [ADR 0006](../adr/0006-default-extraction-model-openrouter.md). The test table is generated from the run files in `backend/evals/extraction/results/v2/` (test split, committed); the dev log is `specs/018-extraction-evaluation-v2/dev-iterations.md` (the dev run files are ignored by git).

## Summary

- Default stays `openai/gpt-6-luna`: the cheapest of the two models that pass every threshold (luna and `qwen/qwen3.8-flash`), 0.84 PLN a month. Fallback stays `google/gemini-3.1-flash-lite`: qwen answers by `json_schema`, so the pair rule excludes it.
- Prompts `extraction@5`, `link_disambiguation@1`, `match_check@1`. The flow is extract, link, match check; every step ran with the model under test.
- The match check is architecture B (ADR 0010), stored in `extraction_event.for_next_pl_match` and filtered at read time beside the 60 h filter.

## Set composition

Set v2 is v1 relabelled by the session judge, plus new cases from the retrieval v2 corpus. Every case is `reviewed: true`; the owner reviewed all changed and new cases (`review --set evals/extraction/v2`).

| Split | Cases | Empty cases | Events | out | doubt | benched | confirmed_starter | confirmed | likely | rumour | Synthetic | `other_match_lineup` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | 72 | 47 | 140 | 8 | 16 | 8 | 108 | 121 | 13 | 6 | 7 | 14 |
| test | 156 | 97 | 311 | 26 | 30 | 45 | 210 | 268 | 32 | 11 | 22 | 29 |

Tagged cases beyond `other_match_lineup`: test has `cup_european_lineup` 3, `national_lineup` 2, `womens_lineup` 3, `international_injury` 9, `multi_player` 9, `ambiguous_name` 5, `accented_name` 3, `nickname` 3; dev has 1, 2, 2, 5, 4, 4, 2 and 1. No `other_match_lineup` case has an event (AC13).

Pilot of the Claude Code session judge (Sonnet, medium effort, v1 dev split, 45 cases): 38 cases unchanged (84%, gate 80%), 7 changed (5 of them only gained the `other_match_lineup` tag). Its F1 against v1's reviewed dev labels was 0.884 (gate 0.85), where v1's paid pre-labels scored 0.739. Sonnet therefore judged the rest; the few inputs it could not return as bare JSON were judged by Opus with the same prompt.

Reviewed labels against the pre-labels at commit `f6ced46` (`python -m app.extraction.evaluation compare-labels --set evals/extraction/v2 --revision f6ced46`, the per-case lines left out):

```
split dev
  cases: 72
  cases changed: 9
  events added / removed / relabelled: 3 / 4 / 9
  relabelled by field: event_type 0, certainty 0, fpl_id 9
  ids only in the reviewed set: -
  ids only in the baseline: -
  pre-labels precision / recall / f1: 0.908 / 0.914 / 0.911
split test
  cases: 156
  cases changed: 19
  events added / removed / relabelled: 25 / 4 / 20
  relabelled by field: event_type 1, certainty 0, fpl_id 19
  ids only in the reviewed set: -
  ids only in the baseline: -
  pre-labels precision / recall / f1: 0.917 / 0.855 / 0.885
```

Most changes in the test split are `fpl_id` fixes of unlinked mentions (19 of the 20 relabels). The 25 added events are mostly the 10 line-up events of `syn-001` (tag removed, its v1 labels restored after the re-dating) and the 10 of #23's post 2104158837540622748.

## Dev iterations

Prompt work used the dev split only: 72 cases, 140 expected events, two models (`openai/gpt-6-luna`, `google/gemini-3.1-flash-lite`), a baseline i0 and three edits, stopped before the five-iteration limit. The runs predate the re-dating of the synthetic line-up cases (owner decision 2026-10-26), so they are reported as run. One later run of the frozen prompts with the winner on the re-dated dev split (`e2e-018`, `openai/gpt-6-luna`) gave F1 0.895, linking 0.961, false alarms 0.000, other-match false alarms 0 of 14, no line-up dropped by the check, and passed every threshold.

| Run | F1 | Precision | Recall | Linking | False alarms | Other-match false alarms | Line-ups dropped by the check | Change made before the run |
|-----|----|-----------|--------|---------|--------------|--------------------------|-------------------------------|----------------------------|
| dev-v2-luna-i0 | 0.264 | 0.676 | 0.164 | 0.929 | 0.000 | 0 of 14 | 9 | none (prompt as frozen in step 9) |
| dev-v2-gemini-i0 | 0.800 | 0.848 | 0.757 | 0.949 | 0.043 | 0 of 14 | 9 | none |
| dev-v2-luna-i1 | 0.831 | 0.874 | 0.793 | 0.950 | 0.000 | 0 of 14 | 9 | owner's labelling rules (left out of squad is `out`; returning player and fatigue remarks give no event; fake leak after the fact gives no event) and a line-up rule: a post with two clubs' elevens or "starts / bench" is a Premier League line-up unless it names a cup or Europe; newly promoted clubs (Coventry, Ipswich, Hull) play in the Premier League |
| dev-v2-gemini-i1 | 0.809 | 0.850 | 0.771 | 0.950 | 0.043 | 0 of 14 | 9 | same |
| dev-v2-luna-i2 | 0.845 | 0.896 | 0.800 | 0.950 | 0.000 | 0 of 14 | 9 | a quote from the manager or club is `confirmed`; an analyst's prediction is `likely`; a knock that keeps a player out of a European or cup squad is `doubt` |
| dev-v2-gemini-i2 | 0.822 | 0.854 | 0.793 | 0.951 | 0.064 | 0 of 14 | 9 | same |
| dev-v2-luna-i3 | 0.847 | 0.910 | 0.793 | 0.949 | 0.000 | 0 of 14 | 9 | examples for "take care", an absence that already happened, and a fantasy manager's own team |
| dev-v2-gemini-i3 | 0.821 | 0.859 | 0.786 | 0.951 | 0.021 | 0 of 14 | 9 | same |

### Reading

- i0 for luna: the model took the "LINE-UPS | #CHEBOU" posts of promoted clubs (Hull, Coventry) for
  cup ties and returned nothing for four 22-player line-ups. The line-up rule of i1 fixed it.
- Neither model passes the F1 threshold on dev, and the prompt cannot lift it much further:
  - the 9 line-up events of three synthetic cases (syn-004, syn-012, syn-016, dated 2026-09-27
    between GW5 and GW6, with no Premier League match for 13 days) are dropped by the match check
    in every run, which is its designed behaviour (6.4 % of the dev events; 12 of 301 test events
    are in the same position);
  - the linking misses (Ben White, Guimarães, Nico González, Andy Robertson, Pedro, Thomas) are
    the linker's, BACKLOG #57, outside this spec. Fixed on 2026-10-27 (DECISIONS): resolving the
    reviewed labels of set v2 with no team, the misses fell from 11 to 0 and the unique links rose
    from 385 to 439 of 450; the model runs above were not repeated.
- Only 1 of the 14 other-match cases had a raw event in any run, so the 60 h filter caught it;
  the match check was not under pressure on dev.
- Stopped after three prompt iterations: the remaining misses are single-case wording noise
  (certainty of one post, a prediction post's extra players) and an edit would fit dev.


## Test results

One run per candidate on the frozen prompts, 156 cases (see the composition table); a run with errored cases got one repeat (`-r2`). Latency is per case and includes the match check. The cost a month is the list price from `prices.toml`, 1,050 posts a month, `USD_PLN_RATE` 3.85. "Thresholds" counts F1 ≥ 0.85, linking ≥ 0.95, false alarms ≤ 5%, ≤ 5 PLN a month and no errored case.

| Run | Model | F1 | Linking | False alarms | Other-match false alarms | Line-ups dropped by the check | PLN / month | p50 / p95 (s) | Errored | Thresholds |
|---|---|---|---|---|---|---|---|---|---|---|
| `test-v2-gpt-6-luna` | `openai/gpt-6-luna` | 0.863 | 0.977 | 4.1% | 2 of 29 | 7 | 0.84 | 1.33 / 7.21 | 0 | 5 of 5 |
| `test-v2-qwen3.8-flash` | `qwen/qwen3.8-flash` | 0.869 | 0.970 | 3.1% | 2 of 29 | 1 | 1.37 | 1.53 / 13.64 | 0 | 5 of 5 |
| `test-v2-gemini-3.1-flash-lite` | `google/gemini-3.1-flash-lite` | 0.918 | 0.980 | 7.2% | 2 of 29 | 2 | 2.39 | 1.04 / 4.55 | 0 | 4 of 5 |
| `test-v2-claude-haiku-4.5` | `anthropic/claude-haiku-4.5` | 0.911 | 0.976 | 4.1% | 2 of 29 | 2 | 14.30 | 1.31 / 6.61 | 0 | 4 of 5 |
| `test-v2-glm-5.3-flash` | `z-ai/glm-5.3-flash` | 0.902 | 0.979 | 6.5% | 3 of 28 | 6 | 2.21 | 2.34 / 14.13 | 5 | 3 of 5 |
| `test-v2-glm-5.3-flash-r2` | `z-ai/glm-5.3-flash` | 0.923 | 0.980 | 5.4% | 2 of 29 | 5 | 2.11 | 2.15 / 13.43 | 4 | 3 of 5 |
| `test-v2-deepseek-v4-flash` | `deepseek/deepseek-v4-flash` | 0.493 | 0.948 | 5.2% | 2 of 29 | 146 | 1.35 | 1.39 / 14.09 | 0 | 2 of 5 |

- `z-ai/glm-5.3-flash` errored on 5 cases in its first run and on 4 in the repeat (the repeat's F1 is the one the rule uses); with errored cases it cannot pass.
- `deepseek/deepseek-v4-flash` answered "not the next match" for 146 expected line-up events, so its recall and F1 collapse; the check depends on the model.
- Other-match false alarms count the cases tagged `other_match_lineup` that keep an event after the filters: 2 of 29 for every run (GLM's first run 3 of 28).
- Line-ups dropped by the check are expected line-up events the check answered false. They include the cost of any real mistakes of the check, and luna's 7 is the largest value among the passing models.

## The 60 h filter alone

For each run: the tagged cases with a raw event before any filter (`other_match_with_raw_events`) and how many of them the 60 h filter alone would have caught (`other_match_caught_by_lead_only`).

| Run | Tagged cases with raw events | Caught by the 60 h filter alone |
|---|---|---|
| `test-v2-gpt-6-luna` | 4 | 2 |
| `test-v2-qwen3.8-flash` | 3 | 1 |
| `test-v2-gemini-3.1-flash-lite` | 5 | 3 |
| `test-v2-claude-haiku-4.5` | 5 | 3 |
| `test-v2-glm-5.3-flash` | 4 | 1 |
| `test-v2-glm-5.3-flash-r2` | 4 | 1 |
| `test-v2-deepseek-v4-flash` | 3 | 1 |

On this test split the match check removed no tagged event beyond the 60 h filter, except in `test-v2-glm-5.3-flash-r2` (four line-up events of one case). Every linked line-up event in a tagged case that the check answered false was also `confirmed` and more than 60 h before the club's next kickoff, so the 60 h filter alone would have dropped it. The tagged events that survive both filters are an unlinked `benched` event (no club, so no check) and `out` events, which neither filter targets; in `test-v2-glm-5.3-flash` the check also answered true for four `likely` `confirmed_starter` events of an other-match line-up. The set holds no other-match line-up at `likely` or `rumour` certainty, or inside the 60 h window, that the check catches and the filter misses, so this run does not measure the case the check was built for (the SPEC's goal).

## Choice

The rule (SPEC 018, ADR 0010): the cheapest model passing every threshold is the default; with none passing, the most thresholds then the higher F1 among the models at or under 5 PLN a month; the fallback is the best other model that is pair-compatible with the default.

- Passing: `openai/gpt-6-luna` (0.84 PLN) and `qwen/qwen3.8-flash` (1.37 PLN). Default: luna.
- Fallback: qwen ranks first but is not pair-compatible (`json_schema` against luna's `function_calling`), and GLM's mandatory reasoning excludes it, so `google/gemini-3.1-flash-lite` (4 of 5 thresholds, F1 0.918) is the fallback.
- `choose_models` (`app/extraction/evaluation/selection.py`) applies the rule to the committed files, and a test pins `DEFAULT_MODEL` and `DEFAULT_FALLBACK_MODEL` to it.

## Total spend

The paid spend of the spec's runs (`python -m app.extraction.evaluation spend --results-dir evals/extraction/results/v2`, dev and test): total spend 1.14 USD by `prices.toml` (1.01 USD reported by OpenRouter), against the cap of 5 USD. The dev runs cost 0.20 USD of it. The session judge ran on the Claude subscription, not on the paid key.

## Threats to validity

- **Vendor overlap.** The pre-labels were written by Claude models in the Claude Code session, and `anthropic/claude-haiku-4.5` is a candidate. The owner reviewed every changed and new case, but the unchanged v1 cases keep labels the judge agreed with, which may favour the Claude family. Haiku fails on cost anyway, so the choice does not depend on it.
- **Noise.** The test split has 156 cases and 311 events; a difference of one or two points of F1 is within noise. Luna passes F1 with 0.863 against the threshold 0.85, and its false-alarm rate is 4.1%.
- **The judge and the review.** The judge is one model (Sonnet at medium effort, Opus for a few inputs), and the owner's review of v1 in spec 005 was partial; v1's unchanged labels were not re-reviewed.
- **Rules written from the test split.** Two of the BACKLOG #23 posts that shaped the v5 rules fall in the test split (2106010889262436608 and 2104158837540622748), since the split is by `x_id % 10`.
- **Line-up over-sampling.** The test split holds far more line-up posts than the production stream, which overstates the match check's share of the cost a month and of the latency.
- **Dev ceiling.** On dev, neither model passed the F1 threshold; the three synthetic line-up cases were dated inside an international break and the linker misses are BACKLOG #57. The synthetic cases were re-dated afterwards, and the test runs use the re-dated set.
- **The check's own effect is not measured.** No tagged case on test is an other-match line-up the 60 h filter misses and the check catches (see "The 60 h filter alone"), so the test runs show the check costs nothing in real line-ups but not how much it adds over the filter. Measuring that needs other-match line-ups at `likely`/`rumour` certainty or inside 60 h of the next Premier League kickoff in the set.
- **The check is model-dependent.** The 146 drops of deepseek show a model can break the check, and a fail-open rule does not cover a wrong answer.

## Re-extraction

Posts stored before prompt v5 have `for_next_pl_match` null, so only the 60 h filter protects the alerts that read them. The owner's optional `reextract` of the stored GW8+ posts (a write to the Compose database, outside this spec) would give them the check's answer and the v5 rules. Expected effect: other-match line-ups the 60 h filter misses (at `likely` or `rumour` certainty, or inside its window) should disappear from the alerts, though set v2 has no such case to confirm it, at the cost of the match-check calls for the line-up posts among them, about 0.84 PLN a month for the stream at this volume.
