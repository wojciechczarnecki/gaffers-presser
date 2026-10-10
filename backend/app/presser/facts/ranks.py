from collections.abc import Iterable
from fractions import Fraction

THRESHOLDS = (1_000_000, 100_000, 10_000)
TOP_TIER = 10_000
MIN_RANKED_GAMEWEEKS = 3


def thresholds_entered(now: int, before: int | None, first_gameweek: bool) -> list[int]:
    if before is None:
        return [t for t in THRESHOLDS if now <= t] if first_gameweek else []
    return [t for t in THRESHOLDS if now <= t < before]


def thresholds_left(now: int, before: int | None) -> list[int]:
    if before is None:
        return []
    return [t for t in THRESHOLDS if before <= t < now]


def is_notable(now: int, before: int | None, first_gameweek: bool) -> bool:
    if thresholds_entered(now, before, first_gameweek) or thresholds_left(now, before):
        return True
    if before is None:
        return False
    if now < before and now <= TOP_TIER:
        return True
    return 2 * now <= before or now >= 2 * before


def move_ratio(now: int, before: int) -> Fraction:
    if now <= before:
        return Fraction(before, now)
    return Fraction(now, before)


def leaders(moves: Iterable[tuple[str, int, int]], rising: bool) -> list[str]:
    moved = [(name, now, before) for name, now, before in moves if (now < before) == rising]
    moved = [(name, now, before) for name, now, before in moved if now != before]
    if not moved:
        return []
    best = max(move_ratio(now, before) for _, now, before in moved)
    return sorted(name for name, now, before in moved if move_ratio(now, before) == best)
