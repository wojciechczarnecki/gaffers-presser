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

## Other evaluations

The corroboration judge (`app/corroboration/evaluation/`) and extraction have no
`--no-judge` / export / import commands yet: their evaluations compare a model's labels
with the owner's reviewed labels, so a judge is only needed where a model pre-labels a set.
To judge in the session there, add the same three pieces to that evaluation's CLI: a flag
that skips the API judge, an export of the prompt and the exact rendered input per item,
and an import that validates the verdicts with the judge's schema and recomputes the totals.
