# 0011 — The corroboration judge's own model and its default

Status: accepted (2026-10-30). It does not supersede [ADR 0006](0006-default-extraction-model-openrouter.md):
extraction keeps its default; the judge gets a setting of its own.

## Context

Until spec 019 the corroboration judge ran on `LLM_MODEL`, the setting extraction uses. Spec
019 measured the judge on its own: set v2 (106 owner-reviewed cases, 75 in the test split,
31 in dev) and a pass threshold fixed before any run (accuracy at least 0.80, false-support
at most 10%, `contradicts` recall at least 0.70, no errored case). The six models of ADR 0006
ran with the frozen prompt `corroboration_judge@1`; the dev iterations found no prompt change
to make. The tables, the agreement with the pre-labels and the threats to validity are in the
[report](../reports/corroboration-eval-v2.md).

## Options

Test-split results (75 cases; the cost a month is an estimate at the volume the report
derives):

| Model | Accuracy | False-support | `contradicts` recall | PLN / month | Thresholds held |
|---|---|---|---|---|---|
| `anthropic/claude-haiku-4.5` | 0.640 | 31.0% (18/58) | 0.500 | 53.60 | 0 of 3 |
| `deepseek/deepseek-v4-flash` | 0.693 | 31.0% (18/58) | 0.667 | 5.16 | 0 of 3 |
| `google/gemini-3.1-flash-lite` | 0.720 | 12.1% (7/58) | 1.000 | 7.38 | 1 of 3 |
| `openai/gpt-6-luna` | 0.613 | 20.7% (12/58) | 0.167 | 2.91 | 0 of 3 |
| `qwen/qwen3.8-flash` | 0.653 | 27.6% (16/58) | 0.833 | 4.46 | 1 of 3 |
| `z-ai/glm-5.3-flash` | 0.680 | 20.7% (12/58) | 0.667 | 5.56 | 0 of 3 |

No model passes. The rule of the SPEC then takes the runs at or under 5 PLN a month, and
among them the most thresholds held, then the lower false-support rate, then the cheaper.
Two runs are eligible, `openai/gpt-6-luna` and `qwen/qwen3.8-flash`; `deepseek` (5.16),
`gemini` (7.38), `glm` (5.56) and `haiku` (53.60) are over the cap.

- **A: keep `LLM_MODEL` for both.** It leaves the extraction default (luna) as the judge,
  which held no threshold and found one `contradicts` in six.
- **B (chosen): a separate `LLM_JUDGE_MODEL`, default the rule's winner.**
- **C: no default until a model passes.** The judge cannot be left unset in production.

## Decision

- `qwen/qwen3.8-flash` is the judge's default (`DEFAULT_JUDGE_MODEL`), because it holds the
  `contradicts` recall threshold and luna does not. It does not meet the pass threshold: its
  false-support rate is 27.6% and its accuracy 0.653. The default is the best of a set that
  does not pass, not a model proven good enough.
- `CorroborationSettings.llm_judge_model` (`LLM_JUDGE_MODEL`, empty = the default). The judge
  is built from `single_model_config` with no fallback (a failed judge call already leaves the
  post unjudged). Extraction keeps `LLM_MODEL` and its fallback.
- `evaluate` and the composition check use the judge's default.

## Consequences

- The grade can still be inflated by a false `supports`; the report's recommendations go to
  the backlog (a prompt or rule change is out of scope of spec 019).
- Test runs are not deterministic at temperature defaults: the same model scored differently
  across repeated runs, so close results (luna against qwen) are within noise on 75 cases.
- `LLM_MODEL` no longer changes the judge; the owner sets `LLM_JUDGE_MODEL` to switch it.
