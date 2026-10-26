# Dev iterations of extraction prompt v5 (spec 018, step 12)

All runs: `evaluate --set evals/extraction/v2 --split dev` (72 cases, 140 expected events), prompt
`extraction@5+link_disambiguation@1+match_check@1`, two models. The result files are in
`backend/evals/extraction/results/v2/dev/` (gitignored). Dev spend of the spec so far: 0.20 USD.

| Run | F1 | Precision | Recall | Linking | False alarms | Other-match false alarms | Line-ups dropped by the check | Change made before the run |
|-----|----|-----------|--------|---------|--------------|--------------------------|-------------------------------|----------------------------|
| dev-v2-luna-i0 | 0.264 | 0.676 | 0.164 | 0.929 | 0.000 | 0 of 14 | 9 | none (prompt as frozen in step 9) |
| dev-v2-gemini-i0 | 0.800 | 0.848 | 0.757 | 0.949 | 0.043 | 0 of 14 | 9 | none |
| dev-v2-luna-i1 | 0.831 | 0.874 | 0.793 | 0.950 | 0.000 | 0 of 14 | 9 | owner's labelling rules (left out of squad is `out`; returning player and fatigue remarks give no event; fake leak after the fact gives no event) and a line-up rule: a post with two clubs' elevens or "starts / bench" is a Premier League line-up unless it names a cup or Europe; newly promoted clubs (Coventry, Ipswich, Hull) play in the Premier League |
| dev-v2-gemini-i1 | 0.809 | 0.850 | 0.771 | 0.950 | 0.043 | 0 of 14 | 9 | same |
| dev-v2-luna-i2 | 0.845 | 0.896 | 0.800 | 0.950 | 0.000 | 0 of 14 | 9 | a quote from the manager or club is `confirmed`; an analyst's prediction is `likely`; a knock that keeps a player out of a European or cup squad is `doubt` |
| dev-v2-gemini-i2 | 0.822 | 0.854 | 0.793 | 0.951 | 0.064 | 0 of 14 | 9 | same |
| dev-v2-luna-i3 | 0.847 | 0.910 | 0.793 | 0.949 | 0.000 | 0 of 14 | 9 | examples for "take care", an absence that already happened, and a fantasy manager's own team |
| dev-v2-gemini-i3 | 0.821 | 0.859 | 0.786 | 0.951 | 0.021 | 0 of 14 | 9 | same |

## Reading

- i0 for luna: the model took the "LINE-UPS | #CHEBOU" posts of promoted clubs (Hull, Coventry) for
  cup ties and returned nothing for four 22-player line-ups. The line-up rule of i1 fixed it.
- Neither model passes the F1 threshold on dev, and the prompt cannot lift it much further:
  - the 9 line-up events of three synthetic cases (syn-004, syn-012, syn-016, dated 2026-09-27
    between GW5 and GW6, with no Premier League match for 13 days) are dropped by the match check
    in every run, which is its designed behaviour (6.4 % of the dev events; 12 of 301 test events
    are in the same position);
  - the linking misses (Ben White, Guimarães, Nico González, Andy Robertson, Pedro, Thomas) are
    the linker's, BACKLOG #57, outside this spec.
- Only 1 of the 14 other-match cases had a raw event in any run, so the 60 h filter caught it;
  the match check was not under pressure on dev.
- Stopped after three prompt iterations: the remaining misses are single-case wording noise
  (certainty of one post, a prediction post's extra players) and an edit would fit dev.
