version: 2

A post gives or reports news about players of one club: a line-up or a bench, an absence (out,
injured, ill, suspended, rested, left out of a squad), or a doubt. Decide whether that news
concerns the club's next Premier League match, or only some other match.

You get the post's text and time, the club (taken from the Fantasy Premier League data, not from
the post), the club's previous and next Premier League fixture around the time of the post, each
as `v <opponent> (H|A), <kickoff in UTC>`, and the events extracted from the post for that club
(`- <player>: <event type>`). Fixtures of other competitions are not listed.

Answer `for_next_pl_match`:

- true: the news is about the club's next Premier League match, the one listed as next. The post
  does not have to name the match: team news posted in the days before that kickoff is about it.
  An injury or illness is always true, whichever match the player got hurt in or missed: it
  carries over to the next Premier League match. An absence with no other match in sight (a
  player out, no cup or European tie mentioned) is true too. When the post holds both an injury
  and a line-up of another match, answer true.
- false: the news concerns only a different match. That is a line-up for a Champions League,
  Europa League, Conference League, domestic cup or other European tie, a match of a national
  team, a friendly, a women's or youth match, or a Premier League match that has already been
  played (posted after the previous fixture's kickoff and describing that match). It is also an
  absence that concerns only such a match: a player rested or rotated for a cup, European or
  national-team match, or left out of such a squad with no injury given, or a suspension served
  in another competition (a domestic cup or European ban).

If the post names an opponent or a competition, compare it with the fixtures: news about an
opponent that is not the next Premier League opponent is false. If the post does not name the
match, use the timing: a line-up published days before the next kickoff, with a midweek match
in between that the fixtures do not list, is more likely for that other match; a line-up
published in the last day or two before the next kickoff is for it. A player "not in the squad"
or "rested" on the eve of a cup tie, with no injury mentioned, is false.
