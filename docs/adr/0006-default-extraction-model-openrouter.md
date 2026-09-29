# 0006 — Default extraction model and OpenRouter as the only LLM provider

Status: accepted (2026-09-29)

## Context

Spec 004 built the extraction flow and left the choice of the default model open (BACKLOG
#14). Spec 005 chose it from measured results: six cheap models ran on the reviewed
evaluation set (136 cases, test split of 91 cases), each once with the frozen prompt
(`extraction@4`, tuned on the dev split only) at its lowest reasoning level. A model passes
with event F1 ≥ 0.85, linking accuracy ≥ 0.95, false-alarm rate ≤ 5%, a projected cost of at
most 5 PLN a month (1,050 posts, `USD_PLN_RATE` 3.85) and no errored case. The full tables,
the dev iterations and the threats to validity are in the
[report](../reports/extraction-eval-v1.md).

## Options

Test-split results (list prices from `prices.toml`; a cross means the threshold is missed):

| Model | F1 | Linking | False alarms | PLN / month | Passes |
|---|---|---|---|---|---|
| `openai/gpt-6-luna` | 0.904 | 1.00 | 4.5% | 0.49 | all five |
| `qwen/qwen3.8-flash` | 0.914 | 1.00 | 6.1% (x) | 0.80 | 4 of 5 |
| `z-ai/glm-5.3-flash` | 0.897 | 1.00 | 6.1% (x) | 0.82 | 4 of 5 |
| `google/gemini-3.1-flash-lite` | 0.883 | 0.98 | 9.1% (x) | 1.28 | 4 of 5 |
| `deepseek/deepseek-v4-flash` | 0.788 (x) | 1.00 | 10.6% (x) | 0.83 | 3 of 5 |
| `anthropic/claude-haiku-4.5` | 0.887 | 1.00 | 6.1% (x) | 8.49 (x) | 3 of 5 |

Gateway options: OpenRouter for every model through `langchain-openrouter`; keeping
`langchain-openai` with a `base_url`; keeping direct adapters for Google, OpenAI and
Anthropic. The first was chosen (one key reaches every candidate; the first-party client
handles structured output, reasoning and routing without work-arounds).

## Decision

- **Default: `openai/gpt-6-luna`.** It is the only candidate that passes all thresholds and
  the cheapest at 0.49 PLN a month. The default is not interim.
- **Fallback: `google/gemini-3.1-flash-lite`.** By the rule (SPEC 005 AC17) the fallback of a
  single passing default is the best other candidate within 5 PLN a month, "best" meaning
  most thresholds passed, then higher F1. That is `qwen/qwen3.8-flash`, but a fallback has to
  answer the primary's request: OpenRouter's `models` list shares the request parameters
  between the models, and no Qwen endpoint accepts the forced `tool_choice` of the primary's
  `function_calling` under `require_parameters` (a live pair check returned
  `NotFoundResponseError`); `z-ai/glm-5.3-flash` fails too, since its mandatory reasoning
  rejects the primary's `effort: none`. The owner decided (2026-09-29) to add a
  pair-compatibility filter to the rule: the fallback must use the same structured-output
  method and reasoning effort as the default (`pair_compatible` in `model_settings.py`).
  Gemini is the best compatible candidate (4 of 5 thresholds, F1 0.883, 1.28 PLN a month) and
  answered the live pair check. It is a fallback for an outage, not a second default: its
  false-alarm rate (9.1%) misses the threshold.
- **OpenRouter is the only LLM provider**, through `langchain-openrouter` (`ChatOpenRouter`,
  exact pin). The Google, OpenAI and Anthropic packages and keys are removed.
- **Configuration.** The key `OPENROUTER_API_KEY` is the switch: unset means extraction is
  disabled, whatever `LLM_MODEL` says. `LLM_MODEL` and `LLM_FALLBACK_MODEL` are optional and
  default to the two models above (`app/extraction/config.py`). `LLM_PROVIDER` and the CLI's
  `--provider` are gone. A model outside the six needs a row in `model_settings.toml` and in
  `prices.toml`; without it the worker fails on start, not per post.
- **Reasoning at the lowest level** each model allows (`none` for five of the six, `low` for
  `z-ai/glm-5.3-flash`), recorded per model in `model_settings.toml` and used identically by
  evaluation and the worker. Extraction is a simple task, and reasoning multiplies cost and
  latency.
- **Fallback through the `models` list** of OpenRouter, so a failing model or vendor is
  covered by one variable. The parameters are shared: the request carries the primary's
  reasoning effort and structured-output method, and `temperature` only when both models
  accept it. The stored extraction and the Langfuse trace record the model that answered.
  `require_parameters` is set so that OpenRouter routes only to hosts that honour the request.
- **Cost figures.** The projected monthly cost uses list prices from `prices.toml` and is what
  the pass rule uses. OpenRouter's reported cost per call on the test split is lower than list
  for qwen (0.000061 vs 0.000199 USD), deepseek (0.000091 vs 0.000206) and glm (0.000103 vs
  0.000203), and equal for luna, gemini and haiku; the default's cost is exact either way.
- **Accepted risk.** An outage of OpenRouter itself stops extraction, since there is no
  second gateway. The fallback covers a failing model or vendor, not the gateway.

## Consequences

- One key enables extraction in production; Railway needs only `OPENROUTER_API_KEY` (and the
  Langfuse keys) to switch it on.
- The comparison rests on 91 test cases (50 events, 66 empty posts), one run per model and
  pre-labels written by a Claude model; differences of a few points are within noise. BACKLOG
  #12 extends the set with the GW6 deadline window and re-runs the comparison, after which the
  default is revisited.
- Swapping the model is a change of `LLM_MODEL` plus a catalogue row for a model outside the
  compared set; a fallback must share the primary's structured-output method and reasoning
  effort.
- A default that passes all thresholds on the current set leaves margin only on the
  false-alarm rate (4.5% against 5%); a single extra false alarm on the test split moves it
  past the threshold.
