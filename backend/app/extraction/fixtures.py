import json
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, select

from app.extraction.linking import PlayerIndex
from app.fpl.calendar import current_season
from app.fpl.models.reference import Fixture


@dataclass(frozen=True)
class FixtureRecord:
    season: str
    fpl_id: int
    gameweek: int | None
    kickoff_at: datetime | None
    team_h_fpl_id: int
    team_a_fpl_id: int


class FixtureIndex:
    def __init__(self, fixtures: Iterable[FixtureRecord]) -> None:
        by_team: dict[int, list[FixtureRecord]] = defaultdict(list)
        for fixture in fixtures:
            if fixture.kickoff_at is None:
                continue
            by_team[fixture.team_h_fpl_id].append(fixture)
            by_team[fixture.team_a_fpl_id].append(fixture)
        self._by_team = {
            team: sorted(rows, key=lambda f: (f.kickoff_at, f.fpl_id))
            for team, rows in by_team.items()
        }
        self._kickoffs = {
            team: [f.kickoff_at for f in rows] for team, rows in self._by_team.items()
        }

    def around(
        self, team_fpl_id: int, at: datetime
    ) -> tuple[FixtureRecord | None, FixtureRecord | None]:
        rows = self._by_team.get(team_fpl_id, [])
        split = bisect_right(self._kickoffs.get(team_fpl_id, []), at)
        previous = rows[split - 1] if split > 0 else None
        following = rows[split] if split < len(rows) else None
        return previous, following

    def next_kickoff(self, team_fpl_id: int, at: datetime) -> datetime | None:
        following = self.around(team_fpl_id, at)[1]
        return following.kickoff_at if following is not None else None


def _record(fixture: Fixture) -> FixtureRecord:
    return FixtureRecord(
        season=fixture.season,
        fpl_id=fixture.fpl_id,
        gameweek=fixture.gameweek_fpl_id,
        kickoff_at=fixture.kickoff_at,
        team_h_fpl_id=fixture.team_h_fpl_id,
        team_a_fpl_id=fixture.team_a_fpl_id,
    )


def load_fixtures(session: Session) -> list[FixtureRecord]:
    season = current_season(session)
    if season is None:
        return []
    rows = session.exec(select(Fixture).where(Fixture.season == season)).all()
    return [_record(row) for row in rows]


def fixtures_snapshot(fixtures: Iterable[FixtureRecord]) -> dict:
    ordered = sorted(fixtures, key=lambda f: (f.season, f.fpl_id))
    return {
        "season": ordered[0].season if ordered else None,
        "fixtures": [
            {**asdict(f), "kickoff_at": f.kickoff_at.isoformat() if f.kickoff_at else None}
            for f in ordered
        ],
    }


def load_fixtures_snapshot(path: Path) -> list[FixtureRecord]:
    data = json.loads(Path(path).read_text())
    records = []
    for entry in data["fixtures"]:
        kickoff = entry["kickoff_at"]
        parsed = datetime.fromisoformat(kickoff).astimezone(UTC) if kickoff else None
        records.append(FixtureRecord(**{**entry, "kickoff_at": parsed}))
    return records


def render_fixture(fixture: FixtureRecord, team_fpl_id: int, index: PlayerIndex) -> str:
    home = fixture.team_h_fpl_id == team_fpl_id
    opponent_id = fixture.team_a_fpl_id if home else fixture.team_h_fpl_id
    opponent = index.team_for(opponent_id)
    name = opponent.name if opponent is not None else f"team {opponent_id}"
    kickoff = fixture.kickoff_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")
    return f"v {name} ({'H' if home else 'A'}), {kickoff}"


def render_fixtures_context(fixtures: FixtureIndex, index: PlayerIndex, at: datetime) -> str:
    lines = []
    for team in index.teams():
        previous, following = fixtures.around(team.fpl_id, at)
        before = render_fixture(previous, team.fpl_id, index) if previous else "none"
        after = render_fixture(following, team.fpl_id, index) if following else "none"
        lines.append(f"{team.name}: previous {before}; next {after}")
    return "\n".join(lines)
