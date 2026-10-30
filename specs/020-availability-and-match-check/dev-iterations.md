# Dev iterations — spec 020

## Corroboration regression

Judge model `google/gemini-3.1-flash-lite`, set v2 test split (75 cases), committed clean tree.

| run | prompt | accuracy | false-support rate | cost_usd |
|-----|--------|----------|--------------------|----------|
| test-judge-v1-r1 | corroboration_judge@1 | 0.720 | 0.121 (7/58) | 0.016885 |
| test-judge-v1-r2 | corroboration_judge@1 | 0.720 | 0.121 (7/58) | 0.016885 |
| test-judge-v1-r3 | corroboration_judge@1 | 0.720 | 0.121 (7/58) | 0.016885 |

Corroboration regression spend: 0.0507 USD
