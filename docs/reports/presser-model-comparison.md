# Presser model comparison (BACKLOG #32)

Date: 2026-10-12 – 2026-10-13. Decision: [ADR 0007](../adr/0007-default-presser-model.md).

## Setup

- **Set and prompts:** evaluation set v6, test split of 24 cases: 5 real league-gameweeks
  (GW1–5, pseudonymised) and 19 synthetic edge cases. Writer prompt version 12, judge prompt
  version 9. Each model wrote each case once, at temperature 0.8 where the model accepts one.
  The writer prompt was tuned over several rounds on the dev split with `openai/gpt-6-luna` as
  the writer, so the comparison favours the luna family.
- **Faithfulness:** claude-opus-5-5 judged every presser in the Claude Code session
  ([docs/EVALUATION.md](../EVALUATION.md)), blind to the model. It listed every factual claim
  and labelled each one `supported` or `unsupported` against the fact sheet and the previous
  pressers. Faithfulness is the supported share per presser, averaged over the 24 pressers.
  Three judging modes were used as the method got cheaper:
  - per item, one subagent per presser (the first five models);
  - per item, one headless call per presser (luna with low reasoning);
  - joint, one headless call per case judging every model's presser in shuffled order (luna
    with high reasoning, deepseek-v4-pro, gpt-6-sol).

  Control: luna and gemini, re-judged jointly on 16 cases, scored 0.988 and 0.981 against
  0.986 and 0.982 per item, so the joint judge is not more lenient.
- **Style:** claude-opus-5-5 rated style 1–5, blind, with all models' pressers for one case in
  one call, against a rubric built from the owner's earlier feedback. It also counted errors
  in names: wrong Polish case endings, misspellings and wrong alphabet. The absolute level
  depends on the comparison set: the same luna run scored 3.21 in one batch and 2.88 in
  another. The order of the models is the reliable part.
- **Owner check:** the owner judged 3 blind pairs, luna (high reasoning) against gpt-6-sol on
  the same case. The owner preferred gpt-6-sol in all three: twice clearly ("more detail,
  nicer language") and once by a small margin.
- **Pass rule (spec 012):** faithfulness ≥ 0.95 and style ≥ 3.5; the cheapest passing model
  wins. A season is 2 leagues × 38 gameweeks = 76 pressers.

## Results

| Model | Faithfulness | Unsupported / claims | Style | Name errors / presser | $ / presser | $ / season | Latency |
|---|---|---|---|---|---|---|---|
| `openai/gpt-6-sol` | **0.996** | 2 / 710 | **3.50** | **0.12** | 0.0341 | 2.59 | 7.0 s |
| `openai/gpt-6-luna`, reasoning high | **0.996** | 3 / 675 | 3.08 | 0.50 | 0.0028 | 0.21 | 28.1 s |
| `openai/gpt-6-luna` | 0.986 | 11 / 835 | 3.21 | 0.83 | 0.0017 | 0.13 | 7.1 s |
| `openai/gpt-6-luna`, reasoning low | 0.985 | 13 / 857 | 3.12 | 0.88 | 0.0017 | 0.13 | 6.1 s |
| `google/gemini-3.8-flash` | 0.982 | 16 / 950 | 3.12 | 0.17 | 0.0140 | 1.07 | 8.2 s |
| `mistralai/mistral-large-4-0` | 0.959 | 40 / 989 | 2.83 | 1.12 | 0.0114 | 0.86 | 24.5 s |
| `deepseek/deepseek-v4-pro` | 0.946 | 43 / 776 | – | – | 0.0154 | 1.17 | 15.1 s |
| `deepseek/deepseek-v4.1-flash` | 0.939 | 62 / 1013 | – | – | 0.0053 | 0.40 | 7.0 s |
| `anthropic/claude-haiku-5.5` | 0.898 | 88 / 852 | – | – | 0.0024 | 0.19 | 5.4 s |

Dropped before judging:

- `moonshotai/kimi-k2.6`: without reasoning it stops after the title (about 40 characters).
  With low reasoning it writes a full presser but spends about 14k reasoning tokens, about
  $0.065 per presser.
- `xiaomi/mimo-v2.6-pro`: rate-limited upstream (HTTP 429) for hours; not usable in
  production.

`summary` names `openai/gpt-6-sol` as the only model that passes.

## What the judges found

- **Errors common to every model:** ranks rounded with two decimals or onto a threshold
  ("1,01M", "1M" for 1 029 798); streaks invented from a count of 1 ("druga wtopa z rzędu");
  "nobody made transfers" inferred from empty lists; counterfactuals computed wrongly ("without
  it he would have won" when the swing only ties); counts of distinct captains.
- **luna:** few slips, mostly counts. Short and disciplined, sometimes report-like. Mistakes
  diminutive name forms ("Heniekowi" instead of "Heńkowi"), and twice wrote "Tadek" partly in
  Cyrillic.
- **gemini:** almost no number errors. Adds world knowledge (nationality, stadium, position),
  which the owner welcomes when it is true and current; "Mbeumo is a Brentford player" shows
  the risk of stale knowledge. The richest vocabulary.
- **mistral:** invented streaks and wrong counterfactuals; typos and case errors ("na Salah").
- **deepseek-flash and deepseek-pro:** unsupported negations ("nobody got a red card") and
  wrong numbers.
- **haiku:** invents match events (a "brace" that never happened, "won the gameweek with the
  TC"). Probable causes: the prompt tuned on luna, `json_schema` structured output (its
  providers reject a forced tool call), no reasoning, and a long rule-dense prompt.
- **gpt-6-sol:** the best Polish of the set, more detail per paragraph, the fewest name errors.

## Threats to validity

- n = 24, one sample per case.
- Three judging modes (the control above covers per item against joint).
- The style ratings are model ratings calibrated by only 3 owner pairs.
- The prompt was tuned on luna.
- The cost of reasoning runs may miss reasoning tokens in our own count.
- The owner has not yet run `judge-review` on this run, so the judge's agreement with the
  owner is not measured here.

## Cost of the comparison

- **OpenRouter:** about $2.6 for the writers in total, including the dropped kimi run.
- **Claude subscription:** judges and style ratings took about 7–8M tokens. The first
  per-item subagent run alone used about 5.8M tokens; the lessons are in
  [docs/EVALUATION.md](../EVALUATION.md).
