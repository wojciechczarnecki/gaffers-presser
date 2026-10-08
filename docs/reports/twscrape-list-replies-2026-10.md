# twscrape and the List timeline: replies and quotes (2026-10)

Closes BACKLOG #11 (spec 010). The latency measurement
([tweet-source-latency-2026-09.md](tweet-source-latency-2026-09.md)) found two posts that
twitterapi.io returned and twscrape did not. This note records what they were and whether
it matters for leaks.

## The two posts

Both are from `@FPL_Harry`, fetched afterwards with twscrape's `tweet_details`:

| x_id | Kind | Replies to |
|---|---|---|
| 2104662407054266709 | reply | `@FPL_TomHadley` |
| 2104663688330486065 | reply | `@FPL_TomHadley` |

`@FPL_TomHadley` is not on the List, and the conversation was started by another account.
Both posts are advice on defender picks and carry no news.

## What the List timeline shows

- All 8 replies stored locally at the time are replies to List members or self-threads.
- A raw List timeline page (fetched 2026-10-07) has single `tweet-<id>` entries and
  `list-conversation-…` modules. One module held a member's post, an off-list account's
  reply to it (`@Teamnewsandtix`) and the member's reply back. So the List does show a
  member's reply inside a conversation the member started, with the off-list account's
  reply as an item of the module.
- A member's reply in a conversation that a non-member started, with a non-member, is left
  out of the timeline. That is what happened to the two posts above.

## Conclusion

Leaks are posted as top-level posts or as the author's own threads, and the List keeps
both. The posts the timeline drops are replies in other people's conversations, which in
the observed cases carried advice and no news, so the loss does not affect leaks. It is
accepted.

The same observation showed a second problem, which spec 010 fixes: the response also holds
off-list posts (quoted posts, reply parents and other accounts' posts inside a module), and
they were stored as list posts. Spec 010 classifies posts by the List's membership.

## When to revisit

A GW6 (or later) leak found only as a member's reply in someone else's conversation. Then
fetch the replies the timeline leaves out (a per-member timeline or `tweet_details` for
conversations the List shows only in part).
