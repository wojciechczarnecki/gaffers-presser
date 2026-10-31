# Extraction evaluation — set v3 (the `available` type and the match check for absences)

Spec 020, run on 2026-10-31. The test run is `test-v3-openai-gpt-6-luna` in `backend/evals/extraction/results/v3/` (test split, committed); the dev log is `specs/020-availability-and-match-check/dev-iterations.md` (the dev run files are ignored by git). Only the default model ran, as the SPEC decided; the model choice of [ADR 0010](../adr/0010-extraction-match-check-and-default-model.md) is not re-opened.

## Summary

- `openai/gpt-6-luna` with `extraction@6`, `link_disambiguation@1` and `match_check@2` **passes all five thresholds** on the test split (F1 0.853, a margin of 0.003), and both absence checks (0 of 12 `other_match_absence` false alarms, 1 injury dropped by the check; the limit is 1 for each).
- `available` F1 is 0.730 on test. It is reported, and no threshold covers it.
- The test F1 sits at the threshold. The dev F1 was 0.81–0.85 over five iterations, and BACKLOG #60 says one model's runs vary by several points, so a second run could fall under 0.85.

## Set composition

Set v3 is set v2 relabelled under prompt v6's rules by the session judge (Sonnet, medium effort), plus 70 new real cases from the GW8–GW9 window, read-only from the Compose export. 298 cases: dev 91, test 207.

| Split | Cases | Empty cases | Events | out | doubt | benched | confirmed_starter | available | Synthetic |
|---|---|---|---|---|---|---|---|---|---|
| dev | 91 | 47 | 236 | 32 | 23 | 13 | 145 | 23 | 7 |
| test | 207 | 107 | 482 | 64 | 47 | 72 | 266 | 33 | 22 |

Tags. Test: `other_match_lineup` 36, `cup_injury` 19, `other_match_absence` 12, `international_injury` 9, `multi_player` 9, `ambiguous_name` 5, `accented_name` 3, `nickname` 3, `cup_european_lineup` 3, `womens_lineup` 3, `national_lineup` 2. Dev: 15, 8, 2, 5, 4, 4, 2, 1, 1, 2, 2. The composition validator `composition_problems_v3` passes (at least 5 `available` events in test, 8 `other_match_absence` cases of which 5 in test, 3 `cup_injury` cases).

`compare-labels` against v2 (`--baseline evals/extraction/v2/cases.jsonl`): dev has 10 changed cases (4 events added, none removed or relabelled) and 19 new ones; test has 19 changed (6 added, 3 relabelled by event type and certainty) and 51 new. Tag-only changes add the two new v3 tags (`cup_injury`, `other_match_absence`) and make up about half of the changed cases; no v2 `other_match_lineup` tag is changed.

BACKLOG #24 posts (`backlog_cases["24"]` in `set.json`, 12 posts): the Wednesday "Evanilson not in the squad" posts (2115513723146441168, 2115514185723654583: no event), FFScout "rested for GW9" (2116079251476611126: no event), the Palmer "will apparently serve his suspension in the Carabao Cup" posts (2114056901528863119 by LetsTalk_FPL: `available`, 2114056912610460055: no event), the pre-Carabao-Cup press-conference post of the SPEC's Palmer anchor (2115090924053147753, Xabi Alonso via BenDinnery: Joao Pedro `out`, no Palmer event), "Palmer is NOT suspended" and "won't be banned for GW9" (2114425128088109407, 2115048903402021224: `available`), the Palmer-served-ban post (2116189958142566833), and the N.Williams posts (2115812355351224672, 2115813636962762862, 2115827279431430357: `available`).

**Who reviewed the labels.** By the owner's decision of 2026-10-31 the owner did not review every case. Each case records its source in `review_source`:

| Source | Cases | Meaning |
|---|---|---|
| `owner` | 171 | v2 cases left unchanged by the relabelling; the owner reviewed them for set v2 |
| `agent` | 71 | reviewed in a fresh agent context against the SPEC rules: the 11 named GW8–GW9 posts, `other_match_absence` cases still carrying `out`/`doubt`, v2 cases changed beyond an added `available`, and 9 cases whose tags broke the composition rules (50); and 21 v2 cases whose tags the relabelling alone had changed, reviewed in the final review (finding F4) |
| `prelabel` | 56 | the session judge's pre-label only; not reviewed by a person |

The agent kept the owner's v2 labels wherever no SPEC rule forced a change (23 v2 cases were put back to the owner's events and tags). The final review put 12 more back: 10 regained the owner's `other_match_lineup` (national-team, women's and cup line-ups), one lost an added `other_match_lineup` and one (2111086653565480960, a club back injury) lost an added `cup_injury`; the other 9 kept the new tags (`other_match_absence` for a cup rest, `cup_injury` for an international injury). Judgement calls that were not rule-checkable are listed in PLAN → Deviations for the owner's later glance.

## Dev iterations

Five iterations with the default model on the dev split (log: `dev-iterations.md`).

| Run | F1 | available F1 | Change |
|---|---|---|---|
| dev-v3-luna-i1 | 0.810 | 0.772 | prompt v6 as first written |
| dev-v3-luna-i2 | 0.848 | 0.780 | `available` is never for team-sheet players or a player hurt in the match |
| dev-v3-luna-i3 | 0.844 | 0.830 | `available` needs a statement about the next Premier League match |
| dev-v3-luna-i4 | 0.837 | 0.714 | rule for name-only LINE-UPS posts; no gain, reverted |
| dev-v3-luna-i5 | 0.819 | 0.808 | the i3 prompt again (variance check) |

The absence checks were green in every iteration (0 injuries dropped; at most 1 of 2 absence false alarms). The prompt was frozen as the i3 text when the iteration limit was reached. Dev never reached 0.85 on two consecutive looks; the remaining misses were single slips (a 22-name line-up list returned empty, name forms, certainty), which the SPEC says not to patch.

## Test results

Run `test-v3-openai-gpt-6-luna`, 207 cases, no errored case.

The run file was scored before the final review restored the tags of F4. Rescored offline against the current set from the stored predictions (no model call), only the other-match line-up count changes, from 1 of 31 (0.032) to 1 of 36 (0.028); the table shows the rescored value. Every threshold metric and both absence checks are unchanged, and so is the injuries-dropped count under the final review's metric fix (an absence found as the other of `out`/`doubt` now counts).

| Measure | Value | Threshold |
|---|---|---|
| Precision / recall | 0.905 / 0.807 | |
| F1 | 0.853 | >= 0.85 |
| Linking accuracy | 1.000 (394 paired) | >= 0.95 |
| False alarm rate | 0.019 | <= 0.05 |
| Monthly cost | 1.12 PLN | <= 5 PLN |
| Errored cases | 0 | 0 |
| `available` F1 | 0.730 | reported only |
| `other_match_absence` false alarms | 0 of 12 | <= 1 |
| Injuries dropped by the check | 1 | <= 1 |
| Line-ups dropped by the check | 3 | reported only |
| Other-match line-up false alarms | 1 of 36 (0.028) | reported only |
| Certainty accuracy | 0.946 | |
| Latency p50 / p95 | 1.29 / 5.47 s | |

## Corroboration regression

Judge `google/gemini-3.1-flash-lite`, set v2 test split (75 cases), three runs each, a committed clean tree.

| Run | Prompt | Accuracy | False-support rate | Cost (USD) |
|---|---|---|---|---|
| test-judge-v1-r1 / r2 / r3 | `corroboration_judge@1` | 0.720 / 0.720 / 0.720 | 0.121 / 0.121 / 0.121 | 0.016885 each |
| test-judge-v2-r1 | `corroboration_judge@2` | 0.720 | 0.103 | 0.019393 |
| test-judge-v2-r2 | `corroboration_judge@2` | 0.707 | 0.103 | 0.019394 |
| test-judge-v2-r3 | `corroboration_judge@2` | 0.747 | 0.086 | 0.019396 |

Means: accuracy v1 0.720, v2 0.724 (tolerance: v2 at least v1 − 0.05); false-support rate v1 0.121, v2 0.097 (tolerance: v2 at most v1 + 0.05). The regression passes (AC12).

## Total spend

| Item | USD |
|---|---|
| Extraction dev runs (5) | 0.1218 |
| Extraction test run | 0.0575 |
| Corroboration regression (6 runs) | 0.1089 |
| **Total spend** | **0.2882** |

The cap is 5 USD (AC18).

The session judge is not paid API spend, but it has its own limit. Building set v3 sent about 3.8M judge tokens (439 candidates and 228 v2 relabels, by `estimate`) against the 3M limit of the extraction procedure; the owner accepted the overrun for this run (PLAN → Owner decisions, 2026-10-30). `spend --results-dir evals/extraction/results/v3` prints 0.1793 USD for the extraction runs (prices.toml), and OpenRouter reported 0.0812 USD for them.

## Threats to validity

- **Judge pre-labels.** 56 of the 298 cases carry only the session judge's pre-label, and the 71 agent-reviewed cases were reviewed by a model, not by the owner. Their labels follow the same prompt rules the model under test reads, so the score may be flattering. The `owner` cases (171) are the v2 ones the owner reviewed.
- **The GW9 cases come from the same posts as the SPEC's examples.** The prompt rules for `available` were written with those posts in mind, so the test split holds the cases the rules were shaped by. The result says the rules work on them, not that they generalise.
- **One answer per post and club.** The match check gives one `for_next_pl_match` answer per post and club. A post holding a cup suspension and an injury for one club gets one answer (the Alonso Carabao Cup post 2115090924053147753 is one: Palmer's suspension is left out of the labels while Joao Pedro's injury is kept). The "injuries dropped" metric measures the other side of this rule: 1 on test, at the limit.
- **Variance (BACKLOG #60).** The test F1 of 0.853 beats the threshold by 0.003. Dev runs of one prompt ranged from 0.819 to 0.844. A repeat run could fail F1; this report does not claim a stable pass.
- **The pre-labels follow an earlier v6 text.** The session judge pre-labelled at commit c6b940d under `extraction@6`. Dev iterations i2 and i3 then added two `available` rules to the prompt with the version kept at 6, so the 56 pre-label-only cases follow a v6 text without those rules, and `set.json` → `prelabels` cannot show the difference.
- **`cup_injury` also covers injuries from international matches.** The tag is used for any injury that must keep its `out`/`doubt` after the check, not only cup or European ones.

## Re-extraction

The owner's AC21 step re-extracts the current alert window on Compose with prompt v6 and the v2 match check. Expected: Evanilson and N.Jackson leave "🔴 Nie zagra" (their absences concern the EFL Cup tie; the check answers false); a Palmer-shaped `available` post reverses older `out` news and shows under "✅ Wraca do gry"; and N.Williams follows his newest post. Posts already extracted before this spec keep `for_next_pl_match` null until re-extracted, and are read as before (fail-open).
