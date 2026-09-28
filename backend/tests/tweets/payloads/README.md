# Tweet source payloads

All payloads here are synthetic, shaped on the documented response schema of each source
(twitterapi.io, X API v2). No handle, name or text is real; account IDs and names are
fabricated (`synthetic_leaker_*`).

- `twitterapi_io-page-1.json.gz`, `twitterapi_io-page-2.json.gz` — shaped on
  `GET /twitter/list/tweets` (twitterapi.io reference, checked 2026-09-28).
- `x_api-page-1.json.gz`, `x_api-page-2.json.gz` — shaped on `GET /2/lists/:id/tweets`
  (X API v2 reference, checked 2026-09-28).
- `twscrape-page-1.json.gz`, `twscrape-page-2.json.gz` — derived from twscrape 0.20.1's
  own MIT-licensed test fixture `tests/mocked-data/raw_list_timeline.json` (sdist on PyPI),
  trimmed to a handful of entries with every handle, display name and text replaced by
  synthetic values. User, tweet and cursor IDs of eight digits or more are replaced by
  synthetic `9999…` values (also inside base64 node IDs), image URLs and `t.co` links point
  at `example.com`, locations are empty, account creation dates and user counters are
  fixed; `tests/tweets/test_payloads.py` enforces this. MIT License, twscrape contributors.
