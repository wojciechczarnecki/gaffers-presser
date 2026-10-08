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
- `twscrape-page-conversation.json.gz`, `twscrape-page-conversation-2.json.gz` — synthetic,
  built from the tweet result of `twscrape-page-1` as a template (not from a live response).
  Page 1 holds, in X's GraphQL entry shapes: a repost by a member of an off-list post
  (`retweeted_status_result`), a member's reply to an off-list post shown as a reply parent
  (`in_reply_to_status_result`, a placement made up for the tests), a member's quote of a
  2019 off-list post (`quoted_status_result`), a `list-conversation-…` module with a member
  post, an off-list reply and the member's reply back, and cursor entries. Page 2 is an older
  page holding the post used as `since_id`. Handles are `synthetic_leaker_*` (members) and
  `synthetic_offlist_*` (not members). Because these pages are not recorded, the live page
  shape is confirmed by the owner's manual check of spec 010 (one worker poll); BACKLOG #30
  replaces them with a sanitised live page after that check.
- `twscrape-list-members.json.gz` — synthetic, shaped on a `ListMembers` response
  (`data.list.members_timeline.timeline.instructions[*].entries[*]` with `user-<id>`
  `TimelineUser` entries and a bottom cursor); three members `Synthetic_Leaker_1..3` in mixed
  case. Every ID is synthetic.
