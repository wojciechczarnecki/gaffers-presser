# 0007 — Default presser model

Status: accepted (2026-10-13)

## Context

Spec 012 left the presser writer on the extraction default, `openai/gpt-6-luna`, until a
comparison (BACKLOG #32). The pass rule from that spec is faithfulness ≥ 0.95 and style ≥ 3.5
on the test split; the cheapest passing model wins. Eleven model configurations were tried;
the tables, the method and the threats to validity are in the
[report](../reports/presser-model-comparison.md).

## Options

| Model | Faithfulness | Style | $ / season |
|---|---|---|---|
| `openai/gpt-6-sol` | 0.996 | 3.50 | 2.59 |
| `openai/gpt-6-luna`, reasoning high | 0.996 | 3.08 | 0.21 |
| `openai/gpt-6-luna` | 0.986 | 3.21 | 0.13 |
| `google/gemini-3.8-flash` | 0.982 | 3.12 | 1.07 |
| `mistralai/mistral-large-4-0` | 0.959 | 2.83 | 0.86 |
| others | < 0.95 | – | – |

## Decision

`PRESSER_MODEL` defaults to `openai/gpt-6-sol` at its lowest reasoning level (`none`). The
model takes no temperature, so the writer's 0.8 is not sent.

Why:

- gpt-6-sol is the only configuration that passes the rule. It ties the best faithfulness and
  has the best style and the fewest errors in names.
- The owner preferred it in all three blind pairs against luna with high reasoning.
- It costs about $2.6 a season for two leagues, within the project's budget. Luna with high
  reasoning is a twelfth of that, and it is the fallback if the cost ever matters (the same
  faithfulness, weaker Polish).
- The name-forms dictionary that #32 kept as a fallback is not needed: gpt-6-sol makes 0.12
  name errors per presser.

## Consequences

- The writer prompt was tuned on luna; the next prompt changes are tuned on gpt-6-sol's dev
  output.
- Style was rated by Claude with an owner check on 3 pairs, not by the owner on every
  presser; a future comparison can reuse the same blind per-case rating
  ([docs/EVALUATION.md](../EVALUATION.md)).
