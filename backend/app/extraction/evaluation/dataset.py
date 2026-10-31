import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.extraction.evaluation.cases import EvalCase, load_cases
from app.extraction.fixtures import FixtureRecord, load_fixtures_snapshot
from app.extraction.linking import PlayerRecord, TeamRecord, load_snapshot

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals" / "extraction"
DEFAULT_SET_DIR = EVALS_DIR / "v3"
DEFAULT_RESULTS_DIR = EVALS_DIR / "results"
CASES_FILE = "cases.jsonl"
PLAYERS_FILE = "players.json"
FIXTURES_FILE = "fixtures.json"
MANIFEST_FILE = "set.json"


class PrelabelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judge: str
    prompt_versions: str
    git_commit: str
    inputs: int
    imported_at: datetime


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int
    quotas: dict[str, int]
    pool: int


class SetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    list_handles: list[str] = Field(default_factory=list)
    prelabels: list[PrelabelEntry] = Field(default_factory=list)
    backlog_cases: dict[str, list[str]] = Field(default_factory=dict)
    backlog_54_lineups: list[str] = Field(default_factory=list)
    selection: Selection | None = None


@dataclass(frozen=True)
class EvalSet:
    directory: Path
    version: str
    cases: list[EvalCase]
    players: list[PlayerRecord]
    teams: list[TeamRecord]
    fixtures: list[FixtureRecord]
    manifest: SetManifest | None


def players_path(directory: Path) -> Path:
    path = directory / PLAYERS_FILE
    if path.exists():
        return path
    legacy = sorted(directory.glob("players-*.json"))
    return legacy[0] if len(legacy) == 1 else path


def load_manifest(directory: Path) -> SetManifest | None:
    path = directory / MANIFEST_FILE
    if not path.exists():
        return None
    return SetManifest.model_validate_json(path.read_text())


def write_manifest(directory: Path, manifest: SetManifest) -> None:
    data = json.loads(manifest.model_dump_json())
    (directory / MANIFEST_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def load_context(
    directory: Path,
) -> tuple[list[PlayerRecord], list[TeamRecord], list[FixtureRecord]]:
    players, teams = load_snapshot(players_path(directory))
    fixtures_path = directory / FIXTURES_FILE
    fixtures = load_fixtures_snapshot(fixtures_path) if fixtures_path.exists() else []
    return players, teams, fixtures


def load_set(directory: Path) -> EvalSet:
    directory = Path(directory)
    cases_path = directory / CASES_FILE
    if not cases_path.exists():
        raise FileNotFoundError(f"{CASES_FILE} not found in {directory}")
    players, teams = load_snapshot(players_path(directory))
    fixtures_path = directory / FIXTURES_FILE
    manifest = load_manifest(directory)
    return EvalSet(
        directory=directory,
        version=manifest.version if manifest else directory.name,
        cases=load_cases(cases_path),
        players=players,
        teams=teams,
        fixtures=load_fixtures_snapshot(fixtures_path) if fixtures_path.exists() else [],
        manifest=manifest,
    )
