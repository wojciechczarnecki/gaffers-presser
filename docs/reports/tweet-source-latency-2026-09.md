# Tweet-source detection latency — 2026-09-28

The pre-merge measurement required by spec 003 (AC18): detection latency of each tweet
source, from a post's `created_at` on X to the moment our system first fetches it. The
target is p95 ≤ 60 s (detection only — see DECISIONS, 2026-09-28).

## Method

- Command: `uv run python -m app.tweets measure --source twscrape --source twitterapi_io
  --interval-seconds 20 --duration-minutes 45`, run locally by the owner; both sources polled
  the watched X List side by side, each in its own thread.
- Window: 2026-09-28, about 20:00–20:45 UTC; the window interval of 20 s, as in production.
- Controlled posts: 20 posts published from the dedicated account `@GafferPresser`, which is
  on the watched list, between 20:01:56 and 20:44:08 UTC, roughly every two minutes. No real
  deadline window was needed.
- Latency = `first_fetched_at − created_at`. X gives `created_at` with one-second
  resolution, so every value carries up to 1 s of rounding.
- The official X API was not measured: no credits were bought (AC18 makes it optional).

## Results

All posts seen by each source (output of `summary --markdown`):

| source | posts | p50 | p95 | max | polls | failed polls |
|---|---|---|---|---|---|---|
| twitterapi_io | 3 | 19.0 | 22.8 | 22.8 | 130 | 95 |
| twscrape | 27 | 19.9 | 26.8 | 28.4 | 125 | 0 |

Controlled posts only (`@GafferPresser`), latency in seconds:

| source | posts detected | p50 | p95 | max | min |
|---|---|---|---|---|---|
| twscrape | 20 / 20 | 19.9 | 25.9 | 26.8 | 6.4 |
| twitterapi_io | 0 / 20 | – | – | – | – |

The other 7 posts twscrape saw came from real accounts on the list (p50 17.4 s, max 28.4 s).

## Findings

- **twscrape meets the target with a wide margin**: every controlled post was detected,
  p95 25.9 s against 60 s, with no failed poll in 45 minutes. The latency is bounded by the
  poll interval: 20 s between polls plus the request time.
- **twitterapi.io could not be compared.** 95 of its 130 polls failed once the free credits
  ran out in the first minutes of the run, so it never saw a controlled post. Its three
  successful detections (17.1–22.8 s) are in line with twscrape, but three posts say nothing
  about p95. The owner chose not to repeat the run on paid credits.
- **twscrape missed two posts that twitterapi.io returned** — both from `@FPL_Harry`
  (created 19:59:55 and 20:05:00 UTC); the one post both sources saw (`@City_Xtra`) was
  detected at 17.1 s and 17.4 s. The cause is not known yet (a reply or quote excluded from
  the List timeline is the first suspect); the follow-up found replies in other people's
  conversations, which do not affect leaks
  ([twscrape-list-replies-2026-10.md](twscrape-list-replies-2026-10.md)).

## Outcome

twscrape becomes the source we configure (`TWEET_SOURCE=twscrape`) — [ADR 0005](../adr/0005-default-tweet-source-twscrape.md).
