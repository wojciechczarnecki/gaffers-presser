import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.errors import ConfigError
from app.extraction.evaluation.cases import EvalCase
from app.extraction.linking import (
    PlayerAlias,
    PlayerIndex,
    PlayerRecord,
    TeamAlias,
    TeamRecord,
    normalise,
)

MAX_FOUND = 20
EDITOR_FALLBACKS = ("nano", "vi")
RULE = "─" * 72


class PlayerDirectory:
    """The snapshot's players, looked up by id for display and by name for the `f` action."""

    def __init__(
        self,
        players: list[PlayerRecord],
        teams: list[TeamRecord],
        player_aliases: list[PlayerAlias] = (),
        team_aliases: list[TeamAlias] = (),
    ) -> None:
        self._index = PlayerIndex(players, teams, player_aliases, team_aliases)
        self._players = sorted(players, key=lambda p: p.fpl_id)
        self._by_id = {p.fpl_id: p for p in players}
        self._teams = {t.fpl_id: t for t in teams}

    def get(self, fpl_id: int) -> PlayerRecord | None:
        return self._by_id.get(fpl_id)

    def describe(self, player: PlayerRecord) -> str:
        team = self._teams.get(player.team_fpl_id)
        club = team.short_name if team is not None else f"team {player.team_fpl_id}"
        return f"{player.web_name} ({player.first_name} {player.second_name}, {club})"

    def find(self, query: str, club: str | None = None) -> list[PlayerRecord]:
        """Linking's own lookup first; failing that, an accent-insensitive substring search."""
        needle = normalise(query)
        if not needle:
            return []
        found = self._index.resolve(query, club or None)
        if not found:
            found = [
                p
                for p in self._players
                if needle in normalise(p.web_name)
                or needle in normalise(f"{p.first_name} {p.second_name}")
            ]
            if club and normalise(club):
                wanted = normalise(club)
                narrowed = [p for p in found if self._club_matches(p, wanted)]
                found = narrowed or found
        return found[:MAX_FOUND]

    def _club_matches(self, player: PlayerRecord, wanted: str) -> bool:
        team = self._teams.get(player.team_fpl_id)
        if team is None:
            return False
        return wanted in normalise(team.name) or wanted == normalise(team.short_name)


def render_case(case: EvalCase, directory: PlayerDirectory, progress: str) -> str:
    created = case.created_at.strftime("%Y-%m-%d %H:%M %Z").strip()
    lines = [
        RULE,
        progress,
        f"id: {case.id}  split: {case.split}  synthetic: {_yes_no(case.synthetic)}",
        f"author: @{case.author_handle}  created: {created}  "
        f"repost: {_yes_no(case.is_repost)}  reply: {_yes_no(case.is_reply)}",
        f"tags: {', '.join(case.tags) if case.tags else '-'}",
        "",
        *(f"  {line}" for line in case.text.splitlines() or [""]),
        "",
    ]
    if not case.expected_events:
        lines.append("expected events: no events")
    else:
        lines.append("expected events:")
        for number, event in enumerate(case.expected_events, start=1):
            if event.fpl_id is None:
                player = "unlinked (fpl_id null)"
            else:
                record = directory.get(event.fpl_id)
                player = (
                    f"{event.fpl_id} {directory.describe(record)}"
                    if record is not None
                    else f"{event.fpl_id} !! NOT IN SNAPSHOT !!"
                )
            lines.append(
                f'  {number}. "{event.mention}" -> {player}  {event.event_type} / {event.certainty}'
            )
    return "\n".join(lines)


def render_player(player: PlayerRecord, directory: PlayerDirectory) -> str:
    return f"  {player.fpl_id:>4}  {directory.describe(player)}"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def editor_command() -> list[str]:
    editor = os.environ.get("EDITOR", "").strip()
    if editor:
        return shlex.split(editor)
    for fallback in EDITOR_FALLBACKS:
        if shutil.which(fallback):
            return [fallback]
    raise ConfigError("set EDITOR: neither nano nor vi was found")


def case_to_json(case: EvalCase) -> str:
    return json.dumps(case.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def run_editor(command: list[str], text: str) -> str:
    """Opens `text` in the editor and returns what was saved."""
    fd, name = tempfile.mkstemp(prefix="eval-case-", suffix=".json")
    path = Path(name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        completed = subprocess.run([*command, str(path)], check=False)
        if completed.returncode != 0:
            raise ValueError(f"the editor exited with code {completed.returncode}")
        return path.read_text()
    finally:
        path.unlink(missing_ok=True)


def parse_edited(text: str, case_id: str) -> EvalCase:
    """Validates the edited JSON; raises ValueError (a ValidationError is one) when it is wrong."""
    edited = EvalCase.model_validate_json(text)
    if edited.id != case_id:
        raise ValueError(f"the case id must stay {case_id!r}, got {edited.id!r}")
    return edited
