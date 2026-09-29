# Extraction model comparison — evaluation set v1

Spec 005, run on 2026-09-29. Decision: [ADR 0006](../adr/0006-default-extraction-model-openrouter.md). All numbers below are generated from the run files in `backend/evals/extraction/results/` (test split, committed) and `backend/evals/extraction/results/dev/` (dev split, ignored by git); none is typed by hand.

## Summary

- Default: `openai/gpt-6-luna` — the only candidate that passes every threshold, and the cheapest (0.49 PLN a month). Not interim.
- Fallback: `google/gemini-3.1-flash-lite` — the best candidate that can answer the default's request through OpenRouter's shared-parameter `models` list (see ADR 0006; `qwen/qwen3.8-flash` ranks higher by the rule but cannot).
- Prompt `extraction@4` (frozen after two kept dev iterations), one test run per candidate, no errored case in any run, no repeat run needed.

## Set composition and the disagreement report (AC2)

| Split | Cases | Empty cases | Events | out | doubt | benched | confirmed_starter | confirmed | likely | rumour |
|---|---|---|---|---|---|---|---|---|---|---|
| dev | 45 | 34 | 23 | 2 | 12 | 2 | 7 | 13 | 8 | 2 |
| test | 91 | 66 | 50 | 8 | 19 | 7 | 16 | 28 | 16 | 6 |

136 cases in total; every case is `reviewed: true` (the gate `test_every_case_reviewed` is green).

Reviewed labels against the pre-labels at commit `dc02d98` (`python -m app.extraction compare-labels`):

```
split dev
  cases: 45
  cases changed: 2
  events added / removed / relabelled: 0 / 0 / 6
  relabelled by field: event_type 6, certainty 2, fpl_id 0
  ids only in the reviewed set: -
  ids only in the baseline: -
  pre-labels precision / recall / f1: 0.739 / 0.739 / 0.739
split test
  cases: 91
  cases changed: 7
  events added / removed / relabelled: 0 / 2 / 5
  relabelled by field: event_type 5, certainty 0, fpl_id 0
  ids only in the reviewed set: -
  ids only in the baseline: -
  pre-labels precision / recall / f1: 0.865 / 0.900 / 0.882
```

## Request settings per model

Every request asks for the lowest reasoning level the model allows and sets `require_parameters`, so OpenRouter routes only to hosts that honour the request. Source: `backend/app/extraction/model_settings.toml`, checked 2026-09-29.

| Model | Reasoning effort | Temperature 0 | Structured output |
|---|---|---|---|
| `openai/gpt-6-luna` | none | no (not accepted) | function_calling |
| `qwen/qwen3.8-flash` | none | yes | json_schema |
| `z-ai/glm-5.3-flash` | low | yes | function_calling |
| `google/gemini-3.1-flash-lite` | none | yes | function_calling |
| `deepseek/deepseek-v4-flash` | none | yes | function_calling |
| `anthropic/claude-haiku-4.5` | none | yes | function_calling |

Baseline adjustment: on the first dev run `qwen/qwen3.8-flash` errored on all 45 cases (`NotFoundResponseError`: with `require_parameters` no Qwen endpoint accepts a forced `tool_choice`), so its row got `structured_method = "json_schema"`; the second attempt had no errored case. The other five models needed no change. `json_schema` is covered only by live runs, since the fake model used in `pytest` supports function calling only. Qwen therefore runs by a different structured-output method than the other five, which is a threat to the fairness of the comparison.

## Dev iterations (AC11)

Prompt versions are `extraction@N`. Prompt work used the dev split only (45 cases). A version was kept only if the mean dev F1 across the six improved and no model's false-alarm rate worsened by more than 0.05; v2 is the starting prompt, v3 and v4 were kept.

| Prompt | Model | F1 | Linking | False alarms | Errored | Cost (USD) |
|---|---|---|---|---|---|---|
| v2 | `openai/gpt-6-luna` | 0.791 | 1.000 | 5.9% | 0 | 0.0045 |
| v2 | `qwen/qwen3.8-flash` | 0.727 | 0.947 | 2.9% | 0 | 0.0076 |
| v2 | `z-ai/glm-5.3-flash` | 0.844 | 0.950 | 2.9% | 0 | 0.0167 |
| v2 | `google/gemini-3.1-flash-lite` | 0.756 | 1.000 | 5.9% | 0 | 0.0119 |
| v2 | `deepseek/deepseek-v4-flash` | 0.756 | 1.000 | 5.9% | 0 | 0.0082 |
| v2 | `anthropic/claude-haiku-4.5` | 0.844 | 1.000 | 5.9% | 0 | 0.0845 |
| v3 | `openai/gpt-6-luna` | 0.791 | 1.000 | 2.9% | 0 | 0.0051 |
| v3 | `qwen/qwen3.8-flash` | 0.837 | 1.000 | 0.0% | 0 | 0.0085 |
| v3 | `z-ai/glm-5.3-flash` | 0.837 | 0.947 | 0.0% | 0 | 0.0224 |
| v3 | `google/gemini-3.1-flash-lite` | 0.800 | 1.000 | 5.9% | 0 | 0.0134 |
| v3 | `deepseek/deepseek-v4-flash` | 0.800 | 0.947 | 5.9% | 0 | 0.0090 |
| v3 | `anthropic/claude-haiku-4.5` | 0.844 | 1.000 | 5.9% | 0 | 0.0905 |
| v4 | `openai/gpt-6-luna` | 0.810 | 1.000 | 0.0% | 0 | 0.0053 |
| v4 | `qwen/qwen3.8-flash` | 0.818 | 0.950 | 0.0% | 0 | 0.0089 |
| v4 | `z-ai/glm-5.3-flash` | 0.818 | 0.950 | 0.0% | 0 | 0.0091 |
| v4 | `google/gemini-3.1-flash-lite` | 0.773 | 1.000 | 2.9% | 0 | 0.0139 |
| v4 | `deepseek/deepseek-v4-flash` | 0.864 | 1.000 | 2.9% | 0 | 0.0093 |
| v4 | `anthropic/claude-haiku-4.5` | 0.864 | 1.000 | 2.9% | 0 | 0.0927 |

Mean dev F1 across the six: v2 0.786, v3 0.818, v4 0.824. Tuning stopped after two kept iterations, to avoid fitting 45 dev cases.

## Test split results (AC16)

One run per candidate, frozen prompt `extraction@4+link_disambiguation@1`, 91 cases (see the composition table). Latency is per case; tokens are means per case.

| Model (run) | Precision | Recall | F1 | Linking | False alarms | Certainty acc. | Errored | Latency p50 / p95 (s) | In / out / reasoning tokens |
|---|---|---|---|---|---|---|---|---|---|
| `openai/gpt-6-luna` (`test-p4-gpt-6-luna`) | 0.870 | 0.940 | 0.904 | 1.000 | 4.5% | 0.936 | 0 | 1.2 / 2.5 | 1044 / 32 / 0 |
| `qwen/qwen3.8-flash` (`test-p4-qwen3.8-flash`) | 0.873 | 0.960 | 0.914 | 1.000 | 6.1% | 0.750 | 0 | 1.4 / 5.6 | 1218 / 34 / 0 |
| `z-ai/glm-5.3-flash` (`test-p4-glm-5.3-flash`) | 0.842 | 0.960 | 0.897 | 1.000 | 6.1% | 0.896 | 0 | 1.4 / 7.4 | 1233 / 36 / 8 |
| `google/gemini-3.1-flash-lite` (`test-p4-gemini-3.1-flash-lite`) | 0.803 | 0.980 | 0.883 | 0.980 | 9.1% | 0.776 | 0 | 1.0 / 1.7 | 1083 / 30 / 0 |
| `deepseek/deepseek-v4-flash` (`test-p4-deepseek-v4-flash`) | 0.759 | 0.820 | 0.788 | 1.000 | 10.6% | 0.683 | 0 | 2.7 / 5.5 | 1349 / 62 / 0 |
| `anthropic/claude-haiku-4.5` (`test-p4-claude-haiku-4.5`) | 0.839 | 0.940 | 0.887 | 1.000 | 6.1% | 0.936 | 0 | 1.0 / 2.7 | 1819 / 56 / 0 |

### Cost

List prices from `prices.toml` decide the pass rule. OpenRouter's reported cost is shown beside them. Projection: 1,050 posts a month, `USD_PLN_RATE` 3.85.

| Model | Cost per post, prices.toml (USD) | Cost per post, OpenRouter reported (USD) | PLN / month | Serving host(s) |
|---|---|---|---|---|
| `openai/gpt-6-luna` | 0.000120 | 0.000121 | 0.49 | OpenAI (91) |
| `qwen/qwen3.8-flash` | 0.000199 | 0.000061 | 0.80 | Alibaba (91) |
| `z-ai/glm-5.3-flash` | 0.000203 | 0.000103 | 0.82 | Together (42), Wafer (1), Phala (11), OpenInference (19), DigitalOcean (16), Modal (2) |
| `google/gemini-3.1-flash-lite` | 0.000316 | 0.000316 | 1.28 | Google (91) |
| `deepseek/deepseek-v4-flash` | 0.000206 | 0.000091 | 0.83 | OpenInference (91) |
| `anthropic/claude-haiku-4.5` | 0.002099 | 0.002099 | 8.49 | Amazon Bedrock (91) |

The reported cost is lower than the list price for `qwen/qwen3.8-flash`, `deepseek/deepseek-v4-flash` and `z-ai/glm-5.3-flash` and equal for the other three. Serving hosts are recorded for every model from OpenRouter's generation record (AC15).

### Thresholds

| Model | F1 ≥ 0.85 | Linking ≥ 0.95 | False alarms ≤ 5% | ≤ 5 PLN / month | No errored case | Passes |
|---|---|---|---|---|---|---|
| `openai/gpt-6-luna` | pass | pass | pass | pass | pass | yes |
| `qwen/qwen3.8-flash` | pass | pass | FAIL | pass | pass | no |
| `z-ai/glm-5.3-flash` | pass | pass | FAIL | pass | pass | no |
| `google/gemini-3.1-flash-lite` | pass | pass | FAIL | pass | pass | no |
| `deepseek/deepseek-v4-flash` | FAIL | pass | FAIL | pass | pass | no |
| `anthropic/claude-haiku-4.5` | pass | pass | FAIL | FAIL | pass | no |

### Certainty confusion tables

Rows are the expected certainty, columns the predicted one, over correctly paired events.

`openai/gpt-6-luna`

| expected \ predicted | confirmed | likely | rumour |
|---|---|---|---|
| confirmed | 27 | 0 | 0 |
| likely | 2 | 14 | 0 |
| rumour | 0 | 1 | 3 |

`qwen/qwen3.8-flash`

| expected \ predicted | confirmed | likely | rumour |
|---|---|---|---|
| confirmed | 25 | 2 | 0 |
| likely | 10 | 6 | 0 |
| rumour | 0 | 0 | 5 |

`z-ai/glm-5.3-flash`

| expected \ predicted | confirmed | likely | rumour |
|---|---|---|---|
| confirmed | 25 | 3 | 0 |
| likely | 1 | 15 | 0 |
| rumour | 0 | 1 | 3 |

`google/gemini-3.1-flash-lite`

| expected \ predicted | confirmed | likely | rumour |
|---|---|---|---|
| confirmed | 26 | 2 | 0 |
| likely | 9 | 6 | 0 |
| rumour | 0 | 0 | 6 |

`deepseek/deepseek-v4-flash`

| expected \ predicted | confirmed | likely | rumour |
|---|---|---|---|
| confirmed | 16 | 3 | 0 |
| likely | 0 | 6 | 10 |
| rumour | 0 | 0 | 6 |

`anthropic/claude-haiku-4.5`

| expected \ predicted | confirmed | likely | rumour |
|---|---|---|---|
| confirmed | 24 | 2 | 0 |
| likely | 0 | 16 | 0 |
| rumour | 0 | 1 | 4 |

There are no repeat (`-r2`) runs: no run had an errored case.

## Rate and spend (AC13)

- `USD_PLN_RATE` = 3.85, set in the owner's `backend/.env` for these runs (2026-09-29).
- total spend (`python -m app.extraction spend`, all dev and test runs): 0.7076 USD (prices.toml); reported 0.6349 USD (OpenRouter). Ceiling 1.50 USD.
- OpenRouter credits (balance 5 USD): usage 3.3045 USD before the first run, 3.9530 USD after the last call of this spec, that is 0.6485 USD including the probe calls, the failed first qwen run and the live pair check, which write no run file.

## Decision

See [ADR 0006](../adr/0006-default-extraction-model-openrouter.md): default `openai/gpt-6-luna`, fallback `google/gemini-3.1-flash-lite`.

## Threats to validity

- The pre-labels came from a Claude model, and `anthropic/claude-haiku-4.5` is a candidate; the owner reviewed every case, and the disagreement report above shows how much was changed.
- The test split is small: 91 cases with 50 events and 66 empty posts. One false alarm moves a false-alarm rate by about 1.5 points, so the default's 4.5% against the 5% threshold has no margin, and neighbouring F1 values are within noise.
- One run per model: no variance estimate. Provider errors did not occur, so no repeats were needed.
- The relabel rule of prompt v2 (the event type describes the next Premier League match, DECISIONS 2026-09-29) was applied to the set before the comparison, on the owner's review, not after seeing test results.
- `qwen/qwen3.8-flash` runs by `json_schema` and the others by function calling, and `z-ai/glm-5.3-flash` reasons at `low` while the others do not reason.
- A fallback shares the request's parameters with the primary (reasoning effort, structured-output method, temperature only when both accept it), which is why the fallback is the best compatible model, not the best ranked one. The fallback pair was checked live on one case only, not evaluated as a pair.
- The set has few real line-up leaks (`confirmed_starter`, `benched`); BACKLOG #12 extends it with the GW6 deadline window and re-runs the comparison.
