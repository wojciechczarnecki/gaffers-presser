from datetime import UTC, datetime
from decimal import Decimal

from app.alerts.schemas import AlertDeadline, ListedPlayer, ManagerRef, PlayerReport
from app.corroboration.schemas import (
    Citation,
    Claim,
    Corroboration,
    Grade,
    PlayerRef,
    PostRef,
    RetrievalReport,
)

SEASON = "2026/27"
DEADLINE_AT = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)
AS_OF = datetime(2026, 10, 4, 14, 0, tzinfo=UTC)
POST_TIME = datetime(2026, 10, 4, 12, 30, tzinfo=UTC)


def deadline(rehearsal: bool = False, gameweek: int | None = 6) -> AlertDeadline:
    if rehearsal:
        return AlertDeadline("rehearsal:2026-10-04T16:00Z", DEADLINE_AT, True, None, None)
    return AlertDeadline(f"{SEASON}:gw{gameweek}", DEADLINE_AT, False, SEASON, gameweek)


def player_ref(fpl_id: int = 1, name: str = "Saka", team: str | None = "Arsenal") -> PlayerRef:
    return PlayerRef(SEASON, fpl_id, name, team)


def post_ref(x_id: int, author: str = "reporter", created_at: datetime = POST_TIME) -> PostRef:
    return PostRef(x_id, author, None, False, created_at, f"text {x_id}")


def citation(
    x_id: int,
    author: str,
    label: str = "supports",
    created_at: datetime = POST_TIME,
    reposted_author: str | None = None,
) -> Citation:
    return Citation(
        x_id=x_id,
        url=f"https://x.com/{author}/status/{x_id}",
        author_handle=author,
        reposted_author_handle=reposted_author,
        created_at=created_at,
        certainty="likely",
        origin="sql",
        label=label,
        freshness="new",
        event_type="doubt",
        text=f"text {x_id}",
    )


def corroboration(
    player: PlayerRef,
    anchor_x_id: int = 100,
    supporting: list[Citation] | None = None,
    contradicting: list[Citation] | None = None,
    reversal: bool = False,
    event_type: str = "out",
    certainty: str = "likely",
    level: str = "medium",
    failure: str | None = None,
) -> Corroboration:
    return Corroboration(
        player=player,
        as_of=AS_OF,
        window_start=datetime(2026, 9, 27, 16, 0, tzinfo=UTC),
        new_since=datetime(2026, 9, 27, 16, 0, tzinfo=UTC),
        anchor=Claim(post_ref(anchor_x_id, "anchoracct"), event_type, certainty),
        supporting=supporting or [],
        contradicting=contradicting or [],
        reversal=reversal,
        grade=Grade(level, ("reason",)),
        retrieval=RetrievalReport("ran", failure=failure),
    )


def listed(
    player: PlayerRef,
    percent: str | None = "23.4",
    managers: tuple[ManagerRef, ...] = (),
    widely_owned: bool = False,
    trending: int | None = None,
    claims: tuple[int, ...] = (100,),
) -> ListedPlayer:
    return ListedPlayer(
        player=player,
        selected_by_percent=Decimal(percent) if percent is not None else None,
        managers=managers,
        widely_owned=widely_owned,
        trending_accounts=trending,
        claim_x_ids=claims,
    )


def report(
    listed_player: ListedPlayer,
    corroboration_value: Corroboration | None = None,
    new_x_ids: frozenset[int] = frozenset(),
    search_failed: bool = False,
) -> PlayerReport:
    return PlayerReport(
        listed=listed_player,
        corroboration=corroboration_value or corroboration(listed_player.player),
        new_x_ids=new_x_ids,
        search_failed=search_failed,
    )


SAKA = player_ref(1, "Saka", "Arsenal")


def full_report():
    value = corroboration(
        SAKA,
        anchor_x_id=100,
        supporting=[citation(101, "second"), citation(102, "third", reposted_author="origin")],
        contradicting=[citation(103, "doubter", label="contradicts")],
        reversal=True,
        failure="retrieval failed: TimeoutError",
    )
    return report(
        listed(
            SAKA,
            percent="23.4",
            managers=(
                ManagerRef("Jan Kowalski", "Kowalski FC"),
                ManagerRef("Ewa Nowak", "Nowak XI"),
            ),
            widely_owned=True,
            trending=4,
            claims=(100, 101, 102, 103),
        ),
        value,
        new_x_ids=frozenset({100, 102}),
    )


def set_ownership(engine, percents: dict[int, str | None]) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        for fpl_id, percent in percents.items():
            conn.execute(
                text(
                    "UPDATE player SET selected_by_percent = :percent"
                    " WHERE season = :season AND fpl_id = :fpl_id"
                ),
                {"percent": percent, "season": SEASON, "fpl_id": fpl_id},
            )


def seed_league(
    engine,
    league_id: int,
    managers: dict[int, tuple[str, str]],
    picks: dict[int, dict[int, list[int] | dict[int, int]]],
) -> None:
    from sqlmodel import Session

    from app.fpl.models.leagues import (
        League,
        LeagueMembership,
        Manager,
        ManagerGameweek,
        ManagerPick,
    )

    with Session(engine) as session, session.begin():
        session.add(League(season=SEASON, fpl_id=league_id, name=f"League {league_id}"))
        session.flush()
        for entry_id, (manager_name, team_name) in managers.items():
            session.add(
                Manager(
                    season=SEASON, entry_id=entry_id, team_name=team_name, manager_name=manager_name
                )
            )
            session.flush()
            session.add(LeagueMembership(season=SEASON, league_fpl_id=league_id, entry_id=entry_id))
        session.flush()
        for entry_id, by_gameweek in picks.items():
            for gameweek, players in by_gameweek.items():
                session.add(
                    ManagerGameweek(
                        season=SEASON, entry_id=entry_id, gameweek_fpl_id=gameweek, has_team=True
                    )
                )
                session.flush()
                numbered = players if isinstance(players, dict) else dict(enumerate(players, 1))
                for position, fpl_id in numbered.items():
                    session.add(
                        ManagerPick(
                            season=SEASON,
                            entry_id=entry_id,
                            gameweek_fpl_id=gameweek,
                            position=position,
                            player_fpl_id=fpl_id,
                            multiplier=1,
                            is_captain=False,
                            is_vice_captain=False,
                        )
                    )


def set_raw(engine, x_id: int, raw: dict) -> None:
    import json

    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tweet SET raw = CAST(:raw AS jsonb) WHERE x_id = :x_id"),
            {"raw": json.dumps(raw), "x_id": x_id},
        )
