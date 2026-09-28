version: 1

A post mentions a player by name, and more than one Fantasy Premier League (FPL) player
matches that name. Given the post's text, the mention, the team named in the post (if any)
and the list of candidate players, decide which candidate, if any, the post is about.

Each candidate is given as `fpl_id — full name (web name), team`.

Return the `fpl_id` of the single candidate the post refers to. If you cannot tell which
candidate it is, or none of the candidates fits, return no `fpl_id` (null) — do not guess.
Never return an `fpl_id` that is not among the given candidates.
