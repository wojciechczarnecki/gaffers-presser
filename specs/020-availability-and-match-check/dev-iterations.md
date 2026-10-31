# Dev iterations — spec 020

## Corroboration regression

Judge model `google/gemini-3.1-flash-lite`, set v2 test split (75 cases), committed clean tree.

| run | prompt | accuracy | false-support rate | cost_usd |
|-----|--------|----------|--------------------|----------|
| test-judge-v1-r1 | corroboration_judge@1 | 0.720 | 0.121 (7/58) | 0.016885 |
| test-judge-v1-r2 | corroboration_judge@1 | 0.720 | 0.121 (7/58) | 0.016885 |
| test-judge-v1-r3 | corroboration_judge@1 | 0.720 | 0.121 (7/58) | 0.016885 |
| test-judge-v2-r1 | corroboration_judge@2 | 0.720 | 0.103 (6/58) | 0.019393 |
| test-judge-v2-r2 | corroboration_judge@2 | 0.707 | 0.103 (6/58) | 0.019394 |
| test-judge-v2-r3 | corroboration_judge@2 | 0.747 | 0.086 (5/58) | 0.019396 |

Means: accuracy v1 0.720, v2 0.724 (tolerance: v2 >= v1 - 0.05); false-support rate v1 0.121,
v2 0.097 (tolerance: v2 <= v1 + 0.05). The regression passes.

Corroboration regression spend: 0.1089 USD

# Extraction dev

## Extraction dev

Model `openai/gpt-6-luna`, set v3 dev split (91 cases). Absence false alarms are `other_match_absence` cases (2 in dev) and injuries dropped by the check were 0 in every run.

| run | F1 | available F1 | false alarm rate | absence false alarms | injuries dropped | cost per run (USD) | change |
|-----|----|--------------|------------------|----------------------|------------------|--------------------|--------|
| dev-v3-luna-i1 | 0.810 | 0.772 | 0.021 | 0/2 | 0 | 0.0224 | prompt v6 as first written |
| dev-v3-luna-i2 | 0.848 | 0.780 | 0.021 | 1/2 | 0 | 0.0242 | rule: `available` is never for team-sheet players or a player hurt in the match |
| dev-v3-luna-i3 | 0.844 | 0.830 | 0.000 | 0/2 | 0 | 0.0249 | rule: `available` needs a statement about the next Premier League match (not cup rest, long-injury training, doubts) |
| dev-v3-luna-i4 | 0.837 | 0.714 | 0.021 | 0/2 | 0 | 0.0258 | rule for name-only LINE-UPS posts; no gain, reverted |
| dev-v3-luna-i5 | 0.819 | 0.808 | 0.000 | 0/2 | 0 | 0.0245 | the i3 prompt again (a variance check, BACKLOG #60) |

The dev F1 stays at 0.81-0.85 and the remaining misses are single slips (a 22-name line-up list
returned empty, name forms, certainty), which the SPEC says not to patch. The iteration limit of
five was reached; the prompt is the i3 text.

Extraction dev spend: 0.1218 USD
