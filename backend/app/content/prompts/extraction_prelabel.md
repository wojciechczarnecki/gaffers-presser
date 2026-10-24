version: 1

You are labelling one post for an evaluation set. Apply the extraction rules above exactly as
the extraction model must apply them, and return what the correct extraction of this post is.

The input holds the post, its author and time, and the Premier League fixtures around the time
of the post: for every club, its previous and next Premier League match (`v <opponent> (H|A),
<kickoff in UTC>`). Fixtures of other competitions are not listed. Use them to judge a line-up:
a `benched` or `confirmed_starter` event is correct only when the line-up is for the player's
club's next Premier League match, the one listed as next. A line-up for a Champions League,
Europa League, Conference League or cup tie, for a national team, for a women's or youth team,
or for a Premier League match already played, gives no `benched` and no `confirmed_starter`
event, even when the post lists Premier League players and does not name the match.

Besides the events, set `other_match_lineup` to true when the post gives, or reports, a
starting line-up or a bench with Premier League players for any match other than their club's
next Premier League match (the cases in the paragraph above), whether or not the post names the
match. Otherwise set it to false. The flag does not change the events: a post with such a
line-up may still hold genuine availability events (`out`, `doubt`) for other players.
