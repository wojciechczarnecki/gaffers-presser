import random
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from app.core.errors import CollectorError
from app.fpl.models import League, LeagueMembership, Manager
from app.presser.evaluation.cases import HistoryEntry, PresserCase
from app.presser.facts import FactSheet, NoFactsError, build_fact_sheet
from app.presser.writer import PreviousPresser

MIN_WORD_LENGTH = 3


class PseudonymisationError(CollectorError):
    pass


@dataclass(frozen=True)
class Pseudonyms:
    managers: tuple[str, ...]
    leagues: tuple[str, ...]


@dataclass(frozen=True)
class BuiltCases:
    cases: list[PresserCase]
    missing_history: list[tuple[str, int]]


def load_pseudonyms(path: Path) -> Pseudonyms:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return Pseudonyms(tuple(data["managers"]), tuple(data["leagues"]))


def assign_pseudonyms(
    entry_ids: Sequence[int], pool: Sequence[str], seed: int, alias: str
) -> dict[int, str]:
    if len(pool) < len(entry_ids):
        raise PseudonymisationError("the pseudonym pool is smaller than the league")
    shuffled = list(pool)
    random.Random(f"{seed}-{alias}").shuffle(shuffled)
    return dict(zip(sorted(entry_ids), shuffled, strict=False))


def _manager_names(facts: FactSheet) -> set[str]:
    names: set[str] = set()
    names |= {score.manager for score in facts.winners + facts.flops}
    names |= {pick.manager for pick in facts.captaincy.picks}
    names |= set(facts.captaincy.best) | set(facts.captaincy.worst)
    extras = facts.bench_transfers_chips
    for group in (
        extras.bench,
        extras.hits,
        extras.transfer_misses,
        extras.chips,
        extras.chip_squad_changes,
        extras.auto_subs,
    ):
        names |= {item.manager for item in group}
    names |= {row.manager for row in facts.table.rows}
    names |= {top.manager for top in facts.table.top3}
    names |= set(facts.table.climbers) | set(facts.table.fallers)
    names |= {row.manager for row in facts.season_facts.rows}
    names |= {
        record.manager
        for record in facts.season_facts.best_gameweek + facts.season_facts.worst_gameweek
    }
    return names


def _whole(text: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(text) + r"(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class RealNames:
    full: tuple[str, ...]
    words: tuple[str, ...]


def _real_names(session: Session, season: str, league_ids: Sequence[int]) -> RealNames:
    managers = session.exec(
        select(Manager.manager_name, Manager.team_name).where(
            Manager.season == season,
            col(Manager.entry_id).in_(
                select(LeagueMembership.entry_id).where(
                    LeagueMembership.season == season,
                    col(LeagueMembership.league_fpl_id).in_(list(league_ids)),
                )
            ),
        )
    ).all()
    leagues = session.exec(
        select(League.name).where(League.season == season, col(League.fpl_id).in_(list(league_ids)))
    ).all()
    full = {name.strip() for pair in managers for name in pair if name.strip()}
    full |= {name.strip() for name in leagues if name.strip()}
    word_sources = [manager_name for manager_name, _ in managers] + list(leagues)
    words = {
        word
        for source in word_sources
        for word in re.findall(r"\w+", source)
        if len(word) >= MIN_WORD_LENGTH
    }
    return RealNames(tuple(sorted(full)), tuple(sorted(words)))


def check_for_leaks(case: PresserCase, real: RealNames, pseudonyms: Pseudonyms) -> None:
    pool = {name.casefold() for name in pseudonyms.managers}
    league_pool = {name.casefold() for name in pseudonyms.leagues}
    blob = case.model_dump_json() + " " + case.id
    for name in real.full:
        if name.casefold() in pool or name.casefold() in league_pool:
            continue
        if _whole(name).search(blob):
            raise PseudonymisationError(f"a real name was left in case {case.id}")
    if case.facts.league.casefold() not in league_pool:
        raise PseudonymisationError(f"the league name of case {case.id} is not a pseudonym")
    if any(name.casefold() not in pool for name in _manager_names(case.facts)):
        raise PseudonymisationError(f"a manager name of case {case.id} is not a pseudonym")
    previous_text = "\n".join(entry.text for entry in case.previous)
    pool_words = {
        word.casefold()
        for name in (*pseudonyms.managers, *pseudonyms.leagues)
        for word in re.findall(r"\w+", name)
    }
    for word in real.words:
        if (
            word.casefold() in pool
            or word.casefold() in league_pool
            or word.casefold() in pool_words
        ):
            continue
        if _whole(word).search(previous_text):
            raise PseudonymisationError(f"a real name was left in the history of case {case.id}")


def _previous(history: Sequence[HistoryEntry], alias: str, gameweek: int) -> list[PreviousPresser]:
    earlier = sorted(
        (entry for entry in history if entry.league == alias and entry.gameweek < gameweek),
        key=lambda entry: entry.gameweek,
    )[-2:]
    return [PreviousPresser(entry.gameweek, entry.text) for entry in earlier]


def _missing_history(
    history: Sequence[HistoryEntry], alias: str, gameweek: int
) -> list[tuple[str, int]]:
    have = {entry.gameweek for entry in history if entry.league == alias}
    wanted = [number for number in (gameweek - 1, gameweek - 2) if number >= 1]
    return [(alias, number) for number in wanted if number not in have]


def build_real_cases(
    engine: Engine,
    season: str,
    gameweeks: Sequence[int],
    pseudonyms: Pseudonyms,
    history: Sequence[HistoryEntry],
    seed: int,
) -> BuiltCases:
    cases: list[PresserCase] = []
    missing: list[tuple[str, int]] = []
    with Session(engine) as session:
        leagues = session.exec(
            select(League).where(League.season == season).order_by(col(League.fpl_id))
        ).all()
        league_ids = [league.fpl_id for league in leagues]
        real = _real_names(session, season, league_ids)
        for index, league in enumerate(leagues):
            alias = f"l{index + 1}"
            entry_ids = list(
                session.exec(
                    select(LeagueMembership.entry_id).where(
                        LeagueMembership.season == season,
                        LeagueMembership.league_fpl_id == league.fpl_id,
                    )
                )
            )
            nicknames = assign_pseudonyms(entry_ids, pseudonyms.managers, seed, alias)
            league_pseudonym = pseudonyms.leagues[index % len(pseudonyms.leagues)]
            for gameweek in gameweeks:
                try:
                    facts = build_fact_sheet(session, season, league.fpl_id, gameweek, nicknames)
                except NoFactsError:
                    continue
                cases.append(
                    PresserCase(
                        id=f"real-{alias}-gw{gameweek}",
                        split="test" if (gameweek + index) % 2 else "dev",
                        source="real",
                        tags=["first_gameweek"] if gameweek == 1 else [],
                        facts=facts.model_copy(update={"league": league_pseudonym}),
                        previous=_previous(history, alias, gameweek),
                    )
                )
                missing.extend(_missing_history(history, alias, gameweek))
    for case in cases:
        check_for_leaks(case, real, pseudonyms)
    return BuiltCases(cases, sorted(set(missing)))
