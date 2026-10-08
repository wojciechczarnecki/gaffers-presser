# Tweet source payloads

The payloads here are synthetic, shaped on the documented response schema of each source
(twitterapi.io, X API v2), or sanitised recordings (twscrape). No handle, name or text is
real; account IDs and names are fabricated (`synthetic_leaker_*`).

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
- `twscrape-page-conversation.json.gz`, `twscrape-page-conversation-2.json.gz` — recorded:
  one live `ListLatestTweetsTimeline` page (2026-10-08) trimmed to thirteen entries and split
  in two, newer entries first. Page 1 holds a member's repost of an off-list post, a quote of
  an older post (embedded through `quoted_status_result`), a quote of a post that is also its
  own entry, a `list-conversation-…` module of two member posts, a module whose first item is
  an off-list post followed by a member's reply to a post absent from the page and the
  member's reply to that reply, and plain posts; page 2 holds older entries, including a
  member's quote of an off-list post inside a module, a long post (`note_tweet`) and a module
  whose first item is older than the page. Live pages carry no `in_reply_to_status_result`:
  a reply parent reaches the page only as a module item, and a reply whose parent is not in
  a module comes without it. `tests/tweets/payloads/__init__.py` names the posts the tests
  use. Sanitised under the rules above: handles are `synthetic_leaker_*` (List members) and
  `synthetic_offlist_*` (not members), display names, descriptions and texts are synthetic
  (one text per post, so a post and its quoted copy agree), `lang` is `en`, IDs of eight
  digits or more are mapped to synthetic `9999…` values in their original order (also inside
  base64 node IDs, entry IDs and media keys), post dates are replaced by a synthetic sequence
  seven minutes apart in their original order, every URL points at `example.com`, each page
  has its own cursor entries and synthetic cursor values, counters are 1, account creation
  dates are fixed; cards, `editable_until_msecs`, profile extras and other fields the adapter
  does not read are removed.
- `twscrape-list-members.json.gz` — synthetic, shaped on a `ListMembers` response
  (`data.list.members_timeline.timeline.instructions[*].entries[*]` with `user-<id>`
  `TimelineUser` entries and a bottom cursor); three members `Synthetic_Leaker_1..3` in mixed
  case. Every ID is synthetic.
