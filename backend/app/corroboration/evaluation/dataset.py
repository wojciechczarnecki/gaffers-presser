import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

CASES_FILE = "cases.jsonl"
MANIFEST_FILE = "set.json"


class PrelabelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judge: str
    prompt_version: str
    git_commit: str
    inputs: int
    imported_at: datetime


class BuildWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gameweek: int
    start: datetime
    end: datetime


class BuildInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus: str
    windows: list[BuildWindow]
    embedding_model: str
    extraction_model: str
    extraction_prompt_version: str
    embeddings_cost_usd: float
    extraction_cost_usd: float
    query_embeddings_cost_usd: float
    candidates: int
    judge_calls_per_month: float


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int
    size: int
    pool: int
    min_contradicts: int


class SetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    list_handles: list[str] = Field(default_factory=list)
    prelabels: list[PrelabelEntry] = Field(default_factory=list)
    build: BuildInfo | None = None
    selection: Selection | None = None


def load_manifest(directory: Path) -> SetManifest | None:
    path = directory / MANIFEST_FILE
    if not path.exists():
        return None
    return SetManifest.model_validate_json(path.read_text())


def write_manifest(directory: Path, manifest: SetManifest) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    data = json.loads(manifest.model_dump_json())
    (directory / MANIFEST_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
