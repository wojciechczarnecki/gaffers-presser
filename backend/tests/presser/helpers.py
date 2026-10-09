from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app.fpl.models import (
    Gameweek,
    League,
    LeagueMembership,
    Manager,
    ManagerAutoSub,
    ManagerGameweek,
    ManagerPick,
    ManagerTransfer,
    Player,
    PlayerGameweekResult,
    Season,
    Team,
)

SEASON = "2026/27"
LEAGUE_ID = 100
START = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


class World:
    def __init__(
        self,
        engine,
        gameweeks: int = 5,
        league_id: int = LEAGUE_ID,
        league_name: str = "Synthetic League",
        season: str = SEASON,
        finished: int | None = None,
    ):
        self.engine = engine
        self.season = season
        self.league_id = league_id
        self.totals: dict[int, int] = {}
        finished = gameweeks if finished is None else finished
        with Session(engine) as session:
            session.add(Season(label=season))
            session.flush()
            session.add(Team(season=season, fpl_id=1, name="Synthetic FC", short_name="SYN"))
            for number in range(1, gameweeks + 1):
                session.add(
                    Gameweek(
                        season=season,
                        fpl_id=number,
                        name=f"Gameweek {number}",
                        deadline_at=START + timedelta(days=7 * number),
                        finished=number <= finished,
                        data_checked=number <= finished,
                    )
                )
            session.flush()
            session.add(League(season=season, fpl_id=league_id, name=league_name))
            session.commit()

    def add_league(self, league_id: int, name: str) -> None:
        with Session(self.engine) as session:
            session.add(League(season=self.season, fpl_id=league_id, name=name))
            session.commit()

    def player(
        self,
        fpl_id: int,
        name: str,
        results: dict[int, tuple[int, int]] | None = None,
        position: int = 3,
    ):
        with Session(self.engine) as session:
            session.add(
                Player(
                    season=self.season,
                    fpl_id=fpl_id,
                    web_name=name,
                    first_name=name,
                    second_name=name,
                    team_fpl_id=1,
                    position=position,
                )
            )
            session.commit()
        for gameweek, (minutes, points) in (results or {}).items():
            self.result(fpl_id, gameweek, minutes, points)

    def result(self, player_id: int, gameweek: int, minutes: int, points: int) -> None:
        with Session(self.engine) as session:
            session.add(
                PlayerGameweekResult(
                    season=self.season,
                    player_fpl_id=player_id,
                    gameweek_fpl_id=gameweek,
                    starts=1 if minutes else 0,
                    minutes=minutes,
                    total_points=points,
                    explain=[],
                )
            )
            session.commit()

    def manager(
        self,
        entry_id: int,
        manager_name: str,
        team_name: str | None = None,
        league_id: int | None = None,
    ) -> None:
        with Session(self.engine) as session:
            if session.get(Manager, (self.season, entry_id)) is None:
                session.add(
                    Manager(
                        season=self.season,
                        entry_id=entry_id,
                        team_name=team_name or f"Synthetic XI {entry_id}",
                        manager_name=manager_name,
                    )
                )
                session.flush()
            session.add(
                LeagueMembership(
                    season=self.season,
                    league_fpl_id=league_id or self.league_id,
                    entry_id=entry_id,
                )
            )
            session.commit()

    def gw(
        self,
        entry_id: int,
        gameweek: int,
        points: int,
        *,
        cost: int = 0,
        bench: int = 0,
        chip: str | None = None,
        has_team: bool = True,
        captain: int | None = None,
        vice: int | None = None,
        bench_picks: tuple[int, ...] = (),
        transfers: int | None = None,
    ) -> None:
        with Session(self.engine) as session:
            if not has_team:
                session.add(
                    ManagerGameweek(
                        season=self.season,
                        entry_id=entry_id,
                        gameweek_fpl_id=gameweek,
                        has_team=False,
                    )
                )
                session.commit()
                return
            self.totals[entry_id] = self.totals.get(entry_id, 0) + points - cost
            session.add(
                ManagerGameweek(
                    season=self.season,
                    entry_id=entry_id,
                    gameweek_fpl_id=gameweek,
                    has_team=True,
                    active_chip=chip,
                    points=points,
                    total_points=self.totals[entry_id],
                    event_transfers=transfers if transfers is not None else cost // 4,
                    event_transfers_cost=cost,
                    points_on_bench=bench,
                )
            )
            session.flush()
            if captain is not None:
                session.add(self._pick(entry_id, gameweek, 1, captain, True, False))
            if vice is not None:
                session.add(self._pick(entry_id, gameweek, 2, vice, False, True))
            for offset, player_id in enumerate(bench_picks):
                session.add(self._pick(entry_id, gameweek, 12 + offset, player_id, False, False))
            session.commit()

    def squad(
        self,
        entry_id: int,
        gameweek: int,
        starters: list[int],
        bench: list[int] = (),
        *,
        captain: int | None = None,
        vice: int | None = None,
    ) -> None:
        """Picks at positions 1..n for the starters and 12.. for the bench."""
        placed = [(index + 1, player) for index, player in enumerate(starters)]
        placed += [(12 + index, player) for index, player in enumerate(bench)]
        with Session(self.engine) as session:
            for position, player_id in placed:
                session.add(
                    self._pick(
                        entry_id,
                        gameweek,
                        position,
                        player_id,
                        player_id == captain,
                        player_id == vice,
                    )
                )
            session.commit()

    def _pick(self, entry_id, gameweek, position, player_id, is_captain, is_vice):
        return ManagerPick(
            season=self.season,
            entry_id=entry_id,
            gameweek_fpl_id=gameweek,
            position=position,
            player_fpl_id=player_id,
            multiplier=2 if is_captain else 1,
            is_captain=is_captain,
            is_vice_captain=is_vice,
        )

    def transfer(self, entry_id: int, gameweek: int, player_in: int, player_out: int) -> None:
        with Session(self.engine) as session:
            session.add(
                ManagerTransfer(
                    season=self.season,
                    entry_id=entry_id,
                    made_at=START + timedelta(days=7 * gameweek - 1, minutes=player_in),
                    player_in_fpl_id=player_in,
                    gameweek_fpl_id=gameweek,
                    player_out_fpl_id=player_out,
                    player_in_cost=50,
                    player_out_cost=50,
                )
            )
            session.commit()

    def auto_sub(self, entry_id: int, gameweek: int, player_out: int, player_in: int) -> None:
        with Session(self.engine) as session:
            session.add(
                ManagerAutoSub(
                    season=self.season,
                    entry_id=entry_id,
                    gameweek_fpl_id=gameweek,
                    player_out_fpl_id=player_out,
                    player_in_fpl_id=player_in,
                )
            )
            session.commit()

    def session(self) -> Session:
        return Session(self.engine)
