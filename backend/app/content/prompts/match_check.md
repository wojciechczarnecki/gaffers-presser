version: 1

A post gives, or reports, a Premier League line-up or a bench for one club. Decide whether that
line-up is for the club's next Premier League match, or for some other match.

You get the post's text and time, the club (taken from the Fantasy Premier League data, not from
the post), and the club's previous and next Premier League fixture around the time of the post,
each as `v <opponent> (H|A), <kickoff in UTC>`. Fixtures of other competitions are not listed.

Answer `for_next_pl_match`:

- true: the line-up is for the club's next Premier League match, the one listed as next. The post
  does not have to name the match: team news posted in the days before that kickoff is about it.
- false: the line-up is for a different match. That is a Champions League, Europa League,
  Conference League, domestic cup or other European tie; a match of a national team; a friendly;
  a women's or youth match; or a Premier League match that has already been played (it was
  posted after the previous fixture's kickoff and describes that match).

If the post names an opponent or a competition, compare it with the fixtures: a line-up against
an opponent that is not the next Premier League opponent is false. If the post does not name the
match, use the timing: a line-up published days before the next kickoff, with a midweek match
in between that the fixtures do not list, is more likely for that other match; a line-up
published in the last day or two before the next kickoff is for it.
