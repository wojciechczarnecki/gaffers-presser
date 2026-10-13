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
- **A fresh context per item.** One subagent per judged item, like one API call per item. A
  subagent never judges two items, so it cannot compare them or drift.
- **Blind.** The input directories are named `A`, `B`, … and the mapping to models is kept
  outside them until the verdicts are imported.
- **The same output schema**, validated on import (`JudgeVerdict` for the presser); an
  invalid file fails the import and the item is judged again by a fresh subagent.
- **Recorded.** The run file's `judge_model` names the session judge, e.g.
  `claude-opus-5-5 (Claude Code session)`, and keeps `judge_prompt_version`; the report says
  who judged. `judge_cost_usd` stays empty (the subagents count against the subscription,
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

# 3. One subagent per input file (the prompt below) writes $SCRATCH/judge/A/verdicts/<case_id>.json

# 4. Import per run; review, judge-review and summary then work as with an API judge
uv run python -m app.presser.evaluation import-judge \
    --run evals/presser/results/test-MODEL.json --verdicts $SCRATCH/judge/A/verdicts \
    --judge-model "claude-opus-5-5 (Claude Code session)"
```

The subagent prompt, filled with the three paths:

```text
You are the faithfulness judge of an evaluation. Work only from the two files below; do not
read anything else in the repository or the directory.

1. Read your instructions: <inputs>/judge-prompt.md
2. Read the item to judge: <inputs>/<case_id>.md (a fact sheet, previous pressers and the
   presser to check)
3. Follow the instructions exactly. Write your answer with the Write tool to
   <verdicts>/<case_id>.json as JSON only, in this shape:
   {"claims": [{"claim": "<the claim, quoted or paraphrased from the presser>",
                "label": "supported" | "unsupported"}]}
   Every factual claim of the presser is one entry; the label is one of the two words.
Reply with one line: the number of claims and how many are unsupported.
```

## Other evaluations

The corroboration judge (`app/corroboration/evaluation/`) and extraction have no
`--no-judge` / export / import commands yet: their evaluations compare a model's labels
with the owner's reviewed labels, so a judge is only needed where a model pre-labels a set.
To judge in the session there, add the same three pieces to that evaluation's CLI: a flag
that skips the API judge, an export of the prompt and the exact rendered input per item,
and an import that validates the verdicts with the judge's schema and recomputes the totals.
