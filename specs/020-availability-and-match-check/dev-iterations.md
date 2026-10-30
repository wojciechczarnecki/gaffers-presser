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
