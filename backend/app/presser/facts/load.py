from dataclasses import dataclass
from decimal import Decimal

from sqlmodel import Session, col, select

from app.fpl.models import (
    DeadlineSnapshotPlayer,
    Fixture,
    League,
    LeagueMembership,
    Manager,
    ManagerAutoSub,
    ManagerGameweek,
    ManagerPick,
    ManagerTransfer,
    Player,
    PlayerFixtureStat,
    PlayerGameweekResult,
    Team,
)

BENCH_POSITIONS = (12, 13, 14, 15)


@dataclass(frozen=True)
class MemberRow:
    entry_id: int
    gameweek: int
    points: int
    transfers_cost: int
    points_on_bench: int
    active_chip: str | None
    total_points: int
    transfers: int
    overall_rank: int | None = None
    gameweek_rank: int | None = None


@dataclass(frozen=True)
class CaptainRef:
    captain: int | None
    vice: int | None


@dataclass(frozen=True)
class PlayerResult:
    minutes: int
    points: int


@dataclass(frozen=True)
class StatLine:
    value: int
    points: int


@dataclass(frozen=True)
class FixtureStats:
    fixture_id: int
    stats: dict[str, StatLine]

    @property
    def minutes(self) -> int:
        line = self.stats.get("minutes")
        return line.value if line is not None else 0


@dataclass(frozen=True)
class LiveLine:
    expected_goals: Decimal
    expected_assists: Decimal
    expected_goals_conceded: Decimal


@dataclass(frozen=True)
class MatchResult:
    starts: int
    minutes: int
    points: int
    fixtures: tuple[FixtureStats, ...]
    live: LiveLine | None = None


@dataclass(frozen=True)
class FixtureBonus:
    bps: dict[int, int]
    bonus: dict[int, int]


@dataclass(frozen=True)
class PlayerInfo:
    name: str
    position: int
    club_id: int


@dataclass(frozen=True)
class FixtureScore:
    home_id: int
    away_id: int
    home_score: int | None
    away_score: int | None


@dataclass(frozen=True)
class TransferRow:
    entry_id: int
    player_in: int
    player_out: int


@dataclass(frozen=True)
class SquadPick:
    position: int
    player: int
    is_captain: bool
    is_vice: bool


@dataclass(frozen=True)
class AutoSubRow:
    entry_id: int
    player_out: int
    player_in: int


def _member_ids(season: str, league_id: int):
    return select(LeagueMembership.entry_id).where(
        LeagueMembership.season == season, LeagueMembership.league_fpl_id == league_id
    )


def load_league_name(session: Session, season: str, league_id: int) -> str | None:
    return session.exec(
        select(League.name).where(League.season == season, League.fpl_id == league_id)
    ).first()


def load_manager_names(session: Session, season: str, league_id: int) -> dict[int, str]:
    rows = session.exec(
        select(Manager.entry_id, Manager.manager_name).where(
            Manager.season == season, col(Manager.entry_id).in_(_member_ids(season, league_id))
        )
    ).all()
    return dict(rows)


def _known_rank(rank: int | None) -> int | None:
    return rank if rank is not None and rank > 0 else None


def load_member_rows(
    session: Session, season: str, league_id: int, gameweeks: range
) -> list[MemberRow]:
    rows = session.exec(
        select(ManagerGameweek).where(
            ManagerGameweek.season == season,
            col(ManagerGameweek.entry_id).in_(_member_ids(season, league_id)),
            col(ManagerGameweek.gameweek_fpl_id).in_(list(gameweeks)),
            ManagerGameweek.has_team,
            col(ManagerGameweek.points).is_not(None),
            col(ManagerGameweek.total_points).is_not(None),
        )
    ).all()
    return [
        MemberRow(
            entry_id=row.entry_id,
            gameweek=row.gameweek_fpl_id,
            points=row.points,
            transfers_cost=row.event_transfers_cost or 0,
            points_on_bench=row.points_on_bench or 0,
            active_chip=row.active_chip,
            total_points=row.total_points,
            transfers=row.event_transfers or 0,
            overall_rank=_known_rank(row.overall_rank),
            gameweek_rank=_known_rank(row.gameweek_rank),
        )
        for row in rows
    ]


def load_captain_refs(
    session: Session, season: str, league_id: int, gameweeks: range
) -> dict[tuple[int, int], CaptainRef]:
    rows = session.exec(
        select(ManagerPick).where(
            ManagerPick.season == season,
            col(ManagerPick.entry_id).in_(_member_ids(season, league_id)),
            col(ManagerPick.gameweek_fpl_id).in_(list(gameweeks)),
            (ManagerPick.is_captain) | (ManagerPick.is_vice_captain),
        )
    ).all()
    captains: dict[tuple[int, int], int | None] = {}
    vices: dict[tuple[int, int], int | None] = {}
    for row in rows:
        key = (row.entry_id, row.gameweek_fpl_id)
        if row.is_captain:
            captains[key] = row.player_fpl_id
        if row.is_vice_captain:
            vices[key] = row.player_fpl_id
    return {key: CaptainRef(captains.get(key), vices.get(key)) for key in captains.keys()}


def load_results(
    session: Session, season: str, gameweeks: range, player_ids: set[int]
) -> dict[tuple[int, int], PlayerResult]:
    if not player_ids:
        return {}
    rows = session.exec(
        select(PlayerGameweekResult).where(
            PlayerGameweekResult.season == season,
            col(PlayerGameweekResult.gameweek_fpl_id).in_(list(gameweeks)),
            col(PlayerGameweekResult.player_fpl_id).in_(player_ids),
        )
    ).all()
    return {
        (row.player_fpl_id, row.gameweek_fpl_id): PlayerResult(row.minutes, row.total_points)
        for row in rows
    }


def load_player_names(session: Session, season: str, player_ids: set[int]) -> dict[int, str]:
    if not player_ids:
        return {}
    rows = session.exec(
        select(Player.fpl_id, Player.web_name).where(
            Player.season == season, col(Player.fpl_id).in_(player_ids)
        )
    ).all()
    return dict(rows)


def load_bench_picks(
    session: Session, season: str, league_id: int, gameweek: int
) -> dict[int, list[int]]:
    rows = session.exec(
        select(ManagerPick.entry_id, ManagerPick.player_fpl_id)
        .where(
            ManagerPick.season == season,
            col(ManagerPick.entry_id).in_(_member_ids(season, league_id)),
            ManagerPick.gameweek_fpl_id == gameweek,
            col(ManagerPick.position).in_(BENCH_POSITIONS),
        )
        .order_by(ManagerPick.entry_id, ManagerPick.position)
    ).all()
    bench: dict[int, list[int]] = {}
    for entry_id, player_id in rows:
        bench.setdefault(entry_id, []).append(player_id)
    return bench


def load_transfers(
    session: Session, season: str, league_id: int, gameweek: int
) -> list[TransferRow]:
    rows = session.exec(
        select(ManagerTransfer).where(
            ManagerTransfer.season == season,
            col(ManagerTransfer.entry_id).in_(_member_ids(season, league_id)),
            ManagerTransfer.gameweek_fpl_id == gameweek,
        )
    ).all()
    return [TransferRow(row.entry_id, row.player_in_fpl_id, row.player_out_fpl_id) for row in rows]


def load_auto_subs(
    session: Session, season: str, league_id: int, gameweek: int
) -> list[AutoSubRow]:
    rows = session.exec(
        select(ManagerAutoSub).where(
            ManagerAutoSub.season == season,
            col(ManagerAutoSub.entry_id).in_(_member_ids(season, league_id)),
            ManagerAutoSub.gameweek_fpl_id == gameweek,
        )
    ).all()
    return [AutoSubRow(row.entry_id, row.player_out_fpl_id, row.player_in_fpl_id) for row in rows]


def load_squads(
    session: Session, season: str, keys: set[tuple[int, int]]
) -> dict[tuple[int, int], list[SquadPick]]:
    """The picks of the given (entry ID, gameweek) pairs, in position order."""
    if not keys:
        return {}
    rows = session.exec(
        select(ManagerPick)
        .where(
            ManagerPick.season == season,
            col(ManagerPick.entry_id).in_({entry_id for entry_id, _ in keys}),
            col(ManagerPick.gameweek_fpl_id).in_({gameweek for _, gameweek in keys}),
        )
        .order_by(ManagerPick.entry_id, ManagerPick.gameweek_fpl_id, ManagerPick.position)
    ).all()
    squads: dict[tuple[int, int], list[SquadPick]] = {}
    for row in rows:
        key = (row.entry_id, row.gameweek_fpl_id)
        if key in keys:
            squads.setdefault(key, []).append(
                SquadPick(row.position, row.player_fpl_id, row.is_captain, row.is_vice_captain)
            )
    return squads


def load_player_positions(session: Session, season: str, player_ids: set[int]) -> dict[int, int]:
    if not player_ids:
        return {}
    rows = session.exec(
        select(Player.fpl_id, Player.position).where(
            Player.season == season, col(Player.fpl_id).in_(player_ids)
        )
    ).all()
    return dict(rows)


LIVE_KEYS = ("expected_goals", "expected_assists", "expected_goals_conceded")


def _live_line(live_stats: dict | None) -> LiveLine | None:
    # a missing field degrades to "no stats" instead of failing the build
    if live_stats is None or any(live_stats.get(key) is None for key in LIVE_KEYS):
        return None
    return LiveLine(*(Decimal(str(live_stats[key])) for key in LIVE_KEYS))


def _fixture_stats(item: dict) -> FixtureStats:
    # FPL's points_modification is a correction to the stat's points
    return FixtureStats(
        item["fixture"],
        {
            stat["identifier"]: StatLine(
                stat["value"], stat["points"] + stat.get("points_modification", 0)
            )
            for stat in item["stats"]
        },
    )


def _match_result(row: PlayerGameweekResult) -> MatchResult:
    fixtures = tuple(_fixture_stats(item) for item in row.explain)
    summed = sum(line.points for fixture in fixtures for line in fixture.stats.values())
    # a breakdown that does not sum to the points is not told: the player has no stats
    live = _live_line(row.live_stats) if summed == row.total_points else None
    return MatchResult(
        starts=row.starts,
        minutes=row.minutes,
        points=row.total_points,
        fixtures=fixtures,
        live=live,
    )


def load_match_results(
    session: Session, season: str, gameweek: int, player_ids: set[int]
) -> dict[int, MatchResult]:
    if not player_ids:
        return {}
    rows = session.exec(
        select(PlayerGameweekResult).where(
            PlayerGameweekResult.season == season,
            PlayerGameweekResult.gameweek_fpl_id == gameweek,
            col(PlayerGameweekResult.player_fpl_id).in_(player_ids),
        )
    ).all()
    return {row.player_fpl_id: _match_result(row) for row in rows}


def load_player_info(session: Session, season: str, ids: set[int]) -> dict[int, PlayerInfo]:
    if not ids:
        return {}
    rows = session.exec(
        select(Player.fpl_id, Player.web_name, Player.position, Player.team_fpl_id).where(
            Player.season == season, col(Player.fpl_id).in_(ids)
        )
    ).all()
    return {fpl_id: PlayerInfo(name, position, club) for fpl_id, name, position, club in rows}


def load_club_names(session: Session, season: str) -> dict[int, str]:
    return dict(session.exec(select(Team.fpl_id, Team.name).where(Team.season == season)).all())


def load_fixtures(session: Session, season: str, fixture_ids: set[int]) -> dict[int, FixtureScore]:
    if not fixture_ids:
        return {}
    rows = session.exec(
        select(Fixture).where(Fixture.season == season, col(Fixture.fpl_id).in_(fixture_ids))
    ).all()
    return {
        row.fpl_id: FixtureScore(
            row.team_h_fpl_id, row.team_a_fpl_id, row.team_h_score, row.team_a_score
        )
        for row in rows
    }


def load_ownership(session: Session, season: str, gameweek: int) -> dict[int, Decimal] | None:
    rows = session.exec(
        select(
            DeadlineSnapshotPlayer.player_fpl_id, DeadlineSnapshotPlayer.selected_by_percent
        ).where(
            DeadlineSnapshotPlayer.season == season,
            DeadlineSnapshotPlayer.gameweek_fpl_id == gameweek,
        )
    ).all()
    return dict(rows) if rows else None


def load_team_names(session: Session, season: str, league_id: int) -> dict[int, str]:
    rows = session.exec(
        select(Manager.entry_id, Manager.team_name).where(
            Manager.season == season, col(Manager.entry_id).in_(_member_ids(season, league_id))
        )
    ).all()
    return dict(rows)


def load_fixture_bonus(
    session: Session, season: str, fixture_ids: set[int]
) -> dict[int, FixtureBonus]:
    if not fixture_ids:
        return {}
    rows = session.exec(
        select(
            PlayerFixtureStat.fixture_fpl_id,
            PlayerFixtureStat.player_fpl_id,
            PlayerFixtureStat.identifier,
            PlayerFixtureStat.value,
        ).where(
            PlayerFixtureStat.season == season,
            col(PlayerFixtureStat.fixture_fpl_id).in_(fixture_ids),
            col(PlayerFixtureStat.identifier).in_(("bps", "bonus")),
        )
    ).all()
    result: dict[int, FixtureBonus] = {}
    for fixture_id, player_id, identifier, value in rows:
        entry = result.setdefault(fixture_id, FixtureBonus(bps={}, bonus={}))
        (entry.bps if identifier == "bps" else entry.bonus)[player_id] = value
    return result


def load_club_fixture_counts(session: Session, season: str, gameweek: int) -> dict[int, int]:
    rows = session.exec(
        select(Fixture.team_h_fpl_id, Fixture.team_a_fpl_id).where(
            Fixture.season == season, Fixture.gameweek_fpl_id == gameweek
        )
    ).all()
    counts: dict[int, int] = {}
    for home, away in rows:
        counts[home] = counts.get(home, 0) + 1
        counts[away] = counts.get(away, 0) + 1
    return counts
