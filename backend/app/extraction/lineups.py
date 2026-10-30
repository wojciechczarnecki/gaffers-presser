from datetime import datetime, timedelta

# A confirmed line-up this long before the club's next Premier League kickoff is about another
# match (a European or cup tie, or one already played): the managers' own team news comes up to
# about 54 hours before kickoff, the stray line-ups from 67 hours (GW6-GW8 data, BACKLOG #54).
LINEUP_MAX_LEAD = timedelta(hours=60)
LINEUP_EVENTS = ("confirmed_starter", "benched")
MATCH_CHECKED_EVENTS = ("out", "doubt", "benched", "confirmed_starter")


def is_stale_lineup(
    event_type: str, certainty: str, created_at: datetime, next_kickoff: datetime | None
) -> bool:
    return (
        event_type in LINEUP_EVENTS
        and certainty == "confirmed"
        and next_kickoff is not None
        and next_kickoff - created_at > LINEUP_MAX_LEAD
    )


def kept_at_read(
    event_type: str,
    certainty: str,
    created_at: datetime,
    next_kickoff: datetime | None,
    for_next_pl_match: bool | None,
) -> bool:
    if event_type not in MATCH_CHECKED_EVENTS:
        return True
    if for_next_pl_match is False:
        return False
    return not is_stale_lineup(event_type, certainty, created_at, next_kickoff)
