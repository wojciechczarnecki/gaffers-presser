# Dev iterations — corroboration judge, set v2

Dev split of set v2 (31 cases, owner-reviewed). Prompt `corroboration_judge@1`; the dev result files are gitignored, this log is their record.

| Iteration | Model | Accuracy | False-support | `contradicts` recall | Verdict | Cost USD | Change |
|---|---|---|---|---|---|---|---|
| 0 | anthropic/claude-haiku-4.5 | 0.710 | 0.125 (3/24) | 0.400 | FAIL | 0.0508 | none (baseline) |
| 0 | deepseek/deepseek-v4-flash | 0.774 | 0.292 (7/24) | 0.400 | FAIL | 0.0049 | none (baseline) |
| 0 | google/gemini-3.1-flash-lite | 0.839 | 0.000 (0/24) | 0.800 | PASS | 0.0070 | none (baseline) |
| 0 | openai/gpt-6-luna | 0.774 | 0.000 (0/24) | 0.400 | FAIL | 0.0028 | none (baseline) |
| 0 | qwen/qwen3.8-flash | 0.839 | 0.083 (2/24) | 0.600 | FAIL | 0.0042 | none (baseline) |
| 0 | z-ai/glm-5.3-flash | 0.774 | 0.125 (3/24) | 0.600 | FAIL | 0.0049 | none (baseline) |

Dev spend: 0.0746 USD

Discarded test spend: 0.2040 USD

The discarded test spend is the two sets of six test runs that ran with an uncommitted tree (`git_dirty: true`) and were repeated; their result files were not kept, only luna's accuracy (0.733 and 0.667) was noted.

## Analysis and decision

Only google/gemini-3.1-flash-lite passes every threshold on dev. The errors shared by at least two models are single hard cases that the prompt's rules do not decide:

- jc-480 and jc-131 (expected `unrelated`): a post that names the player only as a hypothetical ("if X does miss out, any takers for Y?") or about another player of the same club; models answer `related` or `supports`. The prompt already says "mentions the player only in passing" and "another player" are `unrelated`; the models' reading differs from the owner's, and a stricter wording would be fitted to two cases.
- jc-556, jc-419, jc-379, jc-129 (expected `contradicts`): mixed team-news posts (several players in one post, or a squad list) where only one line concerns the anchor's player. The failure is attention to the right line in a long post, not a rule the prompt lacks.
- jc-532, jc-415, jc-391: the same list post judged against two different anchors, or speculative posts; the models disagree with each other as well, not on a shared rule.

Nothing is both explained by the prompt's rules and shared across models, so the prompt stays at version 1 and no further iteration runs. No change; the prompt is frozen as `corroboration_judge@1`.
