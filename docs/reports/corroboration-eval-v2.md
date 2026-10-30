# Corroboration judge comparison — evaluation set v2

Spec 019, run on 2026-10-30. Decision: [ADR 0011](../adr/0011-corroboration-judge-model.md). The test table is generated from the run files in `backend/evals/corroboration/results/v2/` (test split, committed); the dev log is `specs/019-corroboration-evaluation-v2/dev-iterations.md` (the dev run files are ignored by git).

## Summary

- No model passes the threshold on the test split. The best, `google/gemini-3.1-flash-lite`, holds one of three (`contradicts` recall 1.00) with accuracy 0.720 and false-support 12.1%; the closest on false-support is also the closest on accuracy.
- The rule (no model passes, so: at most 5 PLN a month, most thresholds, lower false-support, cheaper) picks `qwen/qwen3.8-flash` (0.653, 27.6%, recall 0.83, 4.46 PLN a month). It is the default judge now, under its own setting `LLM_JUDGE_MODEL` (ADR 0011); `LLM_MODEL` keeps extraction. It is the best of a set that does not pass, not a model proven good enough.
- The weak spot is the `related` label (precision 0.20-0.57, recall 0.05-0.60 for every model) and the false `supports` on posts about teammates or about the same club. The judge prompt stayed at `corroboration_judge@1`: no dev error was both explained by its rules and shared by two models.
- Model answers are not deterministic: `openai/gpt-6-luna` scored 0.733, 0.667 and 0.613 on the same 75 cases in three runs. Differences of a few points between models are noise at this set size.

## The set

Set v2 (`backend/evals/corroboration/v2/`) is every v1 case (51, v1's split kept) plus 55 new cases drawn from the GW6 and GW7 alert windows: 106 cases, every one `reviewed: true`.

| Split | Cases | supports | contradicts | related | unrelated |
|---|---|---|---|---|---|
| dev | 31 | 7 | 5 | 4 | 15 |
| test | 75 | 17 | 6 | 20 | 32 |

`has_player_event: false` (what the judge sees in production) 89 cases; `true` (SQL-context pairs, which production labels by the fixed table) 17 cases. The test split holds 61 production-slice cases.

Source: the retrieval v2 corpus (2,557 public posts of the watched List, 2026-09-18 to 2026-10-17) was loaded into a throwaway container, embedded with `text-embedding-3-large` and extracted with prompt v5 and the match check (`extraction@5+link_disambiguation@1+match_check@1`, `openai/gpt-6-luna`). The windows come from the production window function over the corpus's fixtures: GW6 [2026-10-03T10:00Z, 2026-10-10T10:00Z) and GW7 [2026-10-12T21:00Z, 2026-10-17T10:00Z). Anchors are current events under `fresh_lineups_only=True`; candidates come from `retrieval_candidates` with the 2026-10-22 configuration, plus up to 3 SQL-context claims per anchor. The build produced 2,765 candidates; 250 were judged by the session and 55 selected (seed 19, `contradicts` first). The Compose database was neither read nor written.

The corpus holds the watched accounts' posts and the public posts they quote, and the build does not filter the quoted ones out: 8 of the 55 new cases have an anchor by 5 accounts outside the List and 8 a post by 7 such accounts. They are public posts reached through the List, never a private account or manager and league data.

## Pre-labels and the owner's agreement

Every case was pre-labelled blind by the Claude Code session judge (`claude-opus-5-5 (Claude Code session)`, prompt `corroboration_judge@1`), through `scripts/session_judge.sh` with the `corroboration` schema. A v1 case also keeps haiku's original label (`anthropic/claude-haiku-4.5`). The owner then reviewed all 106 cases. Output of `agreement` (cases where the owner's label differs):

```
reviewed: 106
claude-opus-5-5 (Claude Code session): 18 of 106 differ
anthropic/claude-haiku-4.5: 11 of 51 differ
```

The owner changed 17% of the session judge's labels and 22% of haiku's on v1. The session's 18 changes: 6 `related` to `supports` (haiku had `supports` in each, so the owner sided with haiku there), 3 `supports` to `related`, 5 `related` to `unrelated`, 2 `supports` to `unrelated` and 2 `contradicts` to `related`. Haiku's 11 changes: 5 `supports` to `related`, 3 `related` to `supports`, 2 `related` to `unrelated` and 1 `related` to `contradicts`. The pre-labellers disagree with the owner mostly on the border between `related` and its neighbours, the same border where the candidate models fail.

## Threshold

Set before any run, on the test split: accuracy at least 0.80, false-support at most 10%, `contradicts` recall at least 0.70, no errored case. False-support is the share of cases expected `unrelated`, `related` or `contradicts` that the judge called `supports`: a false `supports` inflates a grade. The threshold applies to the whole split; the production slice is reported apart.

## Dev iterations

One iteration (the baseline) of all six models on the 31 dev cases with `corroboration_judge@1`; see `specs/019-corroboration-evaluation-v2/dev-iterations.md`.

| Model | Accuracy | False-support | `contradicts` recall | Verdict |
|---|---|---|---|---|
| `google/gemini-3.1-flash-lite` | 0.839 | 0.0% | 0.80 | pass |
| `qwen/qwen3.8-flash` | 0.839 | 8.3% | 0.60 | fail |
| `deepseek/deepseek-v4-flash` | 0.774 | 29.2% | 0.40 | fail |
| `openai/gpt-6-luna` | 0.774 | 0.0% | 0.40 | fail |
| `z-ai/glm-5.3-flash` | 0.774 | 12.5% | 0.60 | fail |
| `anthropic/claude-haiku-4.5` | 0.710 | 12.5% | 0.40 | fail |

The errors shared by two or more models were single hard cases (a hypothetical about the player, a long squad post where one line concerns him, one list post judged against two anchors). The prompt's rules already cover them, so the prompt was not edited and stays frozen at version 1. Gemini passing on dev and failing on test shows how little 31 cases decide.

## Test results

75 cases, `corroboration_judge@1`, one run per model (no run had an errored case, so no repeat). Precision/recall per label; the production slice is the 61 cases with `has_player_event: false`; verdict per threshold is accuracy / false-support / `contradicts` recall.

| Model | Accuracy | supports P/R | contradicts P/R | related P/R | unrelated P/R | False-support | Production slice accuracy / false-support | Verdict | PLN a month | Errored | Cost USD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `anthropic/claude-haiku-4.5` | 0.640 | 0.42/0.76 | 0.50/0.50 | 0.40/0.20 | 1.00/0.88 | 31.0% (18/58) | 0.672 / 28.6% | fail / fail / fail | 53.60 | 0 | 0.1226 |
| `deepseek/deepseek-v4-flash` | 0.693 | 0.45/0.88 | 0.80/0.67 | 0.57/0.20 | 0.97/0.91 | 31.0% (18/58) | 0.721 / 28.6% | fail / fail / fail | 5.16 | 0 | 0.0118 |
| `google/gemini-3.1-flash-lite` | 0.720 | 0.59/0.59 | 0.67/1.00 | 0.52/0.60 | 1.00/0.81 | 12.1% (7/58) | 0.770 / 10.2% | fail / fail / pass | 7.38 | 0 | 0.0169 |
| `openai/gpt-6-luna` | 0.613 | 0.43/0.53 | 0.50/0.17 | 0.33/0.40 | 1.00/0.88 | 20.7% (12/58) | 0.656 / 20.4% | fail / fail / fail | 2.91 | 0 | 0.0067 |
| `qwen/qwen3.8-flash` | 0.653 | 0.47/0.82 | 0.56/0.83 | 0.20/0.05 | 0.94/0.91 | 27.6% (16/58) | 0.689 / 26.5% | fail / fail / pass | 4.46 | 0 | 0.0102 |
| `z-ai/glm-5.3-flash` | 0.680 | 0.50/0.71 | 0.67/0.67 | 0.44/0.40 | 1.00/0.84 | 20.7% (12/58) | 0.689 / 20.4% | fail / fail / fail | 5.56 | 0 | 0.0127 |

Cost a month: the volume is the candidates of each listed player's newest anchor over the two windows (2,129), times 2 alert slots (digest and news), divided by the 2 windows, times 4 gameweeks a month: 8,516 judge calls. Each model's mean cost per judged case times that volume, times `USD_PLN_RATE`. This overstates news slots without new posts, so it is an upper-side estimate; it is also at the scale of the 20 PLN a month budget only for haiku.

## The rule and the winner

No model passes every threshold, so the SPEC's second branch applies: among runs at or under 5 PLN a month, the most thresholds held (counting "no errored case"), then the lower false-support rate, then the cheaper. Eligible: `openai/gpt-6-luna` (2.91 PLN, 1 held) and `qwen/qwen3.8-flash` (4.46 PLN, 2 held). `deepseek` (5.16), `glm` (5.56), `gemini` (7.38) and `haiku` (53.60) are over the cap. The winner is `qwen/qwen3.8-flash`. Had the cap been 8 PLN, `gemini` would win: it is the only model near the thresholds.

The win rests on one case. qwen holds the `contradicts` recall threshold with 5 of 6 (0.83); one more miss (4 of 6, 0.67) and it holds as many thresholds as luna, and luna wins on the lower false-support rate (20.7% against 27.6%). The six models also ran twice before the committed runs; those runs were discarded for `git_dirty: true` and their result files were not kept, so what the rule picked in them is not known. Only luna's accuracy was noted (0.733 and 0.667, against 0.613 in the committed run). The pick is the rule's answer on these runs, not a stable ranking of qwen above luna.

## Spend

| Item | USD |
|---|---|
| Build: embeddings 0.0192, extraction 0.3764, query embeddings 0.0001 | 0.396 |
| Dev iteration (six runs) | 0.075 |
| Test runs (six, committed) | 0.181 |
| Two discarded sets of test runs (they ran with an uncommitted tree, `git_dirty: true`, and were repeated; recorded as `Discarded test spend` in the dev log) | about 0.204 |
| Total | about 0.856 |

Against the $5 cap for the spec and the $1 cap for the build (0.396).

## Threats to validity

- **Set size.** 75 test cases, 6 with `contradicts`: one case moves recall by 0.17 and false-support by about 1.7 points. A model's rank is not stable (luna scored 0.733, 0.667 and 0.613 on the same split), and gemini passed dev and failed test.
- **Vendor overlap (vendor overlap between the Claude pre-labels and the haiku candidate).** The session pre-labels come from an Anthropic model and `anthropic/claude-haiku-4.5` is a candidate, and v1's labels came from haiku. Every case is owner-reviewed (18 and 11 labels changed), and haiku scored worst, so no sign of a bias in its favour; the overlap is still named.
- **SQL-context pairs.** 17 cases carry a claim the production judge never sees (it uses the fixed table). They count in the threshold, as the owner decided; the production slice is reported apart and the whole-split accuracy differs from it by at most 0.05 for every model.
- **The estimated volume.** The cost a month is derived from the windows, not measured in production.
- **One reviewer, one prompt.** The owner's labels for `related` against `unrelated` are a judgement call; the prompt was frozen without iterating on test cases.

## Recommendations

As BACKLOG proposals only; none is done here.

- A judge prompt or label change for `related` (P2): merge it into `unrelated` for the grade, or give it an example set, because it is the weakest label for every model and does not change a grade.
- A larger `contradicts` set (P3): more real contradictions from later gameweeks, trigger: the next judge change.
- Averaged runs (P3): the repeated runs show noise, trigger: a model change that rests on a gap smaller than 0.05.
