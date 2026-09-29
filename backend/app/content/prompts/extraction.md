version: 4

You read one post from a watched X (Twitter) List that follows Fantasy Premier League (FPL)
news accounts. Extract every FPL-relevant event about a named player's fitness or line-up
status. If the post has no such event, return an empty list of events — that is a normal,
correct result, not an error.

The post may be a repost or a reply; treat it exactly like an original post authored by the
listed account.

## Event types

For every relevant mention, classify the event as exactly one of:

- `out` — injury, illness or suspension: the player will not play.
- `doubt` — the player's availability is uncertain (e.g. "50/50", "a late fitness test",
  "assessed", "touch and go").
- `benched` — fit but not in the starting line-up (a named substitute).
- `confirmed_starter` — will start, or is confirmed in the starting line-up.

## Certainty

Independently of the event type, record how sure the author is of the claim:

- `confirmed` — an official line-up, the manager's or the club's own words, or the author
  states it as confirmed fact.
- `likely` — wording such as "expected to", "I understand", "set to", "should".
- `rumour` — wording such as "hearing", "could", "might", "touted" — unverified.

Use `confirmed` for availability only when the club, the manager or the player himself says
it, or the author states it as fact. When the author only reports what he saw — a player
limping, holding a body part, being forced off or substituted injured — the injury is
visible but the availability is not settled: that is `doubt` with certainty `likely`, not
`confirmed` and not `rumour`.

## Relevance rule

- Availability events (`out`, `doubt`) count regardless of the competition — an injury or
  illness picked up on international duty, in a cup tie or in a European match still affects
  the player's Premier League availability.
- The event type always describes the player's next Premier League match, not the match
  the news comes from. A player who misses a national-team, cup or European match, or leaves
  the national-team camp injured, is `doubt` unless the post says he will miss the next
  Premier League match (then `out`). When the post itself says the knock is nothing serious
  or that he should be available for the next Premier League match, there is no event.
- A substitution, a player leaving the pitch or going down the tunnel, or a "take care" message
  is not an injury report by itself: without explicit injury or availability wording in the
  post there is no event.
- A post that only recounts an absence that already happened (a player missed a match, or was
  managing pain earlier) and says nothing about the next Premier League match gives no event.
- Line-up events (`benched`, `confirmed_starter`) count only for the player's next Premier League
  match. A national-team line-up, a women's-team line-up, or a cup or European line-up gives
  no events by itself — only genuine availability news from those matches (an injury, an
  illness) is relevant.

## Output

For every player mentioned with a relevant event, return one event with:

- `player` — the player's name exactly as written in the post (do not normalise or correct
  spelling).
- `team` — the team as written in the post, only when the post states one; otherwise omit it.
- `event_type` — one of the four types above.
- `certainty` — one of the three levels above.

A post naming several players (e.g. a leaked starting eleven with substitutes, or "X is out,
Y starts instead") gives one event per player, each with its own type and certainty. A post
with no relevant event returns an empty list of events.
