import pytest

from app.presser.facts import check_fact_sheet
from app.presser.facts.schema import FactSheet, KeyMoment, empty_sections
from tests.presser.evaluation.factories import sheet as base_sheet


def ranked_sheet(gameweek: int = 5) -> FactSheet:
    value = base_sheet(gameweek).model_copy(deep=True)
    value.winners[0].gameweek_rank = 50_000
    value.flops[0].gameweek_rank = 900_000
    value.season_facts = value.season_facts.model_validate(
        {
            "rows": [row.model_dump() for row in value.season_facts.rows],
            "best_gameweek": [
                {"manager": "Bartas", "gameweek": 3, "gameweek_rank": 40_000, "net_points": 66}
            ],
            "worst_gameweek": [
                {"manager": "Kuba", "gameweek": 2, "gameweek_rank": 1_500_000, "net_points": 31}
            ],
            "personal_bests": [
                {"manager": "Bartas", "gameweek_rank": 50_000, "ranked_gameweeks": 4}
            ],
            "personal_worsts": [],
        }
    )
    value.overall = value.overall.model_validate(
        {
            "rows": [
                {
                    "manager": "Bartas",
                    "overall_rank": 8_000,
                    "previous_overall_rank": 12_000,
                    "movement": 4_000,
                    "entered": [10_000],
                    "left": [],
                    "notable": True,
                },
                {
                    "manager": "Kuba",
                    "overall_rank": 1_900_000,
                    "previous_overall_rank": 900_000,
                    "movement": -1_000_000,
                    "entered": [],
                    "left": [1_000_000],
                    "notable": True,
                },
            ],
            "biggest_climbers": ["Bartas"],
            "biggest_fallers": ["Kuba"],
        }
    )
    value.empty_sections = empty_sections(value)
    return value


def problems_after(mutate, recompute: bool = True) -> list[str]:
    value = ranked_sheet()
    mutate(value)
    if recompute:
        value.empty_sections = empty_sections(value)
    return check_fact_sheet(value)


def test_consistent_ranked_sheet_has_no_problems():
    assert check_fact_sheet(ranked_sheet()) == []


def test_first_gameweek_sheet_with_entered_thresholds_is_consistent():
    value = ranked_sheet(1)
    for row in value.overall.rows:
        row.previous_overall_rank = None
        row.movement = None
    value.overall.rows[0].entered = [10_000]
    value.overall.rows[1].entered = []
    value.overall.rows[1].left = []
    value.overall.rows[1].notable = False
    value.overall.biggest_climbers = []
    value.overall.biggest_fallers = []
    value.overall.rows[0].entered = [1_000_000, 100_000, 10_000]
    value.season_facts.best_gameweek[0].gameweek = 1
    value.season_facts.worst_gameweek[0].gameweek = 1
    value.season_facts.personal_bests = []
    value.empty_sections = empty_sections(value)
    assert check_fact_sheet(value) == []


@pytest.mark.parametrize(
    ("expected", "mutate"),
    [
        ("a rank is not positive", lambda v: setattr(v.winners[0], "gameweek_rank", 0)),
        (
            "a rank is not positive",
            lambda v: setattr(v.overall.rows[0], "overall_rank", -5),
        ),
        (
            "overall: a movement does not match the ranks",
            lambda v: setattr(v.overall.rows[0], "movement", 3_999),
        ),
        (
            "overall: entered thresholds do not match the ranks",
            lambda v: setattr(v.overall.rows[0], "entered", []),
        ),
        (
            "overall: left thresholds do not match the ranks",
            lambda v: setattr(v.overall.rows[0], "left", [10_000]),
        ),
        (
            "overall: notable does not match the ranks",
            lambda v: setattr(v.overall.rows[1], "notable", False),
        ),
        (
            "overall: biggest climbers do not match the rows",
            lambda v: setattr(v.overall, "biggest_climbers", ["Kuba"]),
        ),
        (
            "overall: biggest fallers do not match the rows",
            lambda v: setattr(v.overall, "biggest_fallers", []),
        ),
        (
            "records: tied records on different ranks",
            lambda v: v.season_facts.best_gameweek.append(
                v.season_facts.best_gameweek[0].model_copy(
                    update={"gameweek": 4, "gameweek_rank": 41_000}
                )
            ),
        ),
        (
            "records: the best rank is worse than the worst",
            lambda v: setattr(v.season_facts.best_gameweek[0], "gameweek_rank", 2_000_000),
        ),
        (
            "records: a record is from a later gameweek",
            lambda v: setattr(v.season_facts.best_gameweek[0], "gameweek", 6),
        ),
        (
            "records: a gameweek rank lies outside the season records",
            lambda v: setattr(v.winners[0], "gameweek_rank", 39_999),
        ),
        (
            "records: a known gameweek rank without season records",
            lambda v: (
                setattr(v.season_facts, "best_gameweek", []),
                setattr(v.season_facts, "worst_gameweek", []),
            ),
        ),
        (
            "personal ranks: too few ranked gameweeks",
            lambda v: setattr(v.season_facts.personal_bests[0], "ranked_gameweeks", 2),
        ),
        (
            "personal ranks: a personal best beats the league best",
            lambda v: setattr(v.season_facts.personal_bests[0], "gameweek_rank", 39_000),
        ),
        (
            "a section names a manager who is not in the table",
            lambda v: setattr(v.overall.rows[0], "manager", "Obcy"),
        ),
        (
            "personal ranks: a personal worst is below the league worst",
            lambda v: v.season_facts.personal_worsts.append(
                v.season_facts.personal_bests[0].model_copy(
                    update={"manager": "Kuba", "gameweek_rank": 1_600_000}
                )
            ),
        ),
    ],
)
def test_inconsistent_rank_fact_is_reported(expected, mutate):
    assert expected in problems_after(mutate)


CHIP_PLAY = {"manager": "Bartas", "chip": "bboost", "effect": 8}
PITCH_ITEM = {
    "player": "Alpha",
    "club": "Klub Testowy",
    "points": 12,
    "kinds": ["differential_haul"],
    "owners": [{"manager": "Bartas", "multiplier": 1}],
    "global_differential": True,
    "league_differential": True,
}


def add_chip(value):
    value.chips = value.chips.model_validate({"plays": [CHIP_PLAY]})


def add_pitch_item(value):
    value.pitch = value.pitch.model_validate({"items": [PITCH_ITEM]})


HIT = {
    "manager": "Bartas",
    "transfers": 1,
    "cost": 4,
    "transfer_gain": 5,
    "paid_off": True,
    "pairs": [
        {"player_in": "Alpha", "player_in_points": 8, "player_out": "Bravo", "player_out_points": 3}
    ],
}


SWAP = {
    "ahead": "Bartas",
    "behind": "Kuba",
    "ahead_previous_rank": 2,
    "ahead_rank": 1,
    "behind_previous_rank": 1,
    "behind_rank": 2,
    "gap": 20,
    "neighbour_gameweeks": 3,
}
PODIUM = {
    "kind": "new_leader",
    "manager": "Bartas",
    "previous_rank": 2,
    "rank": 1,
    "other": "Kuba",
    "gap": 20,
}


def add_swap(**changes):
    def mutate(value):
        value.table = value.table.model_validate(
            value.table.model_dump() | {"rivalry_swaps": [SWAP | changes]}
        )

    return mutate


def add_podium(**changes):
    def mutate(value):
        value.table = value.table.model_validate(
            value.table.model_dump() | {"podium_changes": [PODIUM | changes]}
        )

    return mutate


MOMENT = {
    "pair": "winner",
    "ahead": "Bartas",
    "behind": "Kuba",
    "margin": 10,
    "kind": "player",
    "players": ["Alpha"],
    "club": "Klub Testowy",
    "ahead_points": 14,
    "behind_points": 0,
    "swing": 14,
}


def add_moments(*moments):
    def mutate(value):
        value.key_moments = [KeyMoment.model_validate(MOMENT | m) for m in moments]

    return mutate


def table_of(*totals: tuple[str, int]):
    """Replace the table with these managers and totals, ranks and top 3 recomputed."""

    def mutate(value):
        points = [total for _, total in totals]
        rows = [
            {
                "rank": 1 + sum(other > total for other in points),
                "manager": manager,
                "team_name": f"Drużyna {manager}",
                "total_points": total,
                "movement": None,
            }
            for manager, total in totals
        ]
        top3 = [
            {"manager": m, "total_points": t, "behind_leader": points[0] - t} for m, t in totals[:3]
        ]
        value.table = value.table.model_validate({"rows": rows, "top3": top3})
        value.managers = len(rows)

    return mutate


FOUR = table_of(("Bartas", 120), ("Kuba", 100), ("Ola", 90), ("Dawid", 80))


def add_hit(**changes):
    def mutate(value):
        value.bench_transfers = value.bench_transfers.model_validate({"hits": [HIT | changes]})

    return mutate


@pytest.mark.parametrize(
    ("expected", "mutate"),
    [
        ("empty_sections does not match the sections", add_chip),
        (
            "a section names a manager who is not in the table",
            lambda v: v.pitch.items.append(
                v.pitch.model_validate(
                    {"items": [PITCH_ITEM | {"owners": [{"manager": "Obcy", "multiplier": 1}]}]}
                ).items[0]
            ),
        ),
        (
            "pitch: more than 8 items",
            lambda v: v.pitch.items.extend(
                v.pitch.model_validate({"items": [PITCH_ITEM] * 9}).items
            ),
        ),
        ("table: a rivalry swap does not match the table", add_swap(ahead_rank=2)),
        ("table: a rivalry swap does not match the table", add_swap(behind_rank=1)),
        ("table: a rivalry swap does not match the table", add_swap(gap=19)),
        ("table: a rivalry swap does not match the table", add_swap(neighbour_gameweeks=2)),
        (
            "table: a rivalry swap does not match the table",
            lambda v: (add_swap()(v), setattr(v, "gameweek", 3)),
        ),
        ("table: a podium change does not match the top 3", add_podium(rank=2)),
        ("table: a podium change does not match the top 3", add_podium(gap=19)),
        ("table: a podium change does not match the top 3", add_podium(kind="left")),
        (
            "table: a podium change does not match the top 3",
            add_podium(kind="new_leader", manager="Kuba", other="Bartas", rank=2, gap=-20),
        ),
        (
            "table: a podium change does not match the top 3",
            lambda v: (add_podium()(v), setattr(v, "gameweek", 1)),
        ),
        ("key moments: a swing is below its margin", add_moments({"margin": 15})),
        ("key moments: a swing does not match the points", add_moments({"swing": 13})),
        (
            "key moments: a swing does not match the points",
            add_moments({"ahead_points": 0, "behind_points": 14, "swing": -14, "margin": -20}),
        ),
        (
            "key moments: a pair is not a winner, a swap or a podium change",
            add_moments({"pair": "podium"}),
        ),
        (
            "key moments: a pair is not a winner, a swap or a podium change",
            add_moments({"ahead": "Kuba", "behind": "Bartas"}),
        ),
        (
            "key moments: a pair is not a winner, a swap or a podium change",
            add_moments({"pair": "rivalry"}),
        ),
        (
            "key moments: a pair is not a winner, a swap or a podium change",
            lambda v: (add_swap()(v), add_moments({"pair": "rivalry", "margin": 12})(v)),
        ),
        (
            "key moments: a pair is not a winner, a swap or a podium change",
            lambda v: (add_podium()(v), add_moments({"pair": "podium", "margin": 12})(v)),
        ),
        ("key moments: a margin is not positive", add_moments({"margin": 0})),
        (
            "key moments: winner pairs on different margins",
            add_moments({}, {"players": ["Beta"], "margin": 12}),
        ),
        (
            "table: a rivalry swap does not match the table",
            lambda v: (
                table_of(("Bartas", 120), ("Kuba", 120))(v),
                add_swap(ahead_rank=1, behind_rank=1, gap=0)(v),
            ),
        ),
        (
            "table: a podium change does not match the top 3",
            lambda v: (
                FOUR(v),
                add_podium(kind="entered", manager="Dawid", previous_rank=3, rank=4, gap=-20)(v),
            ),
        ),
        (
            "table: a podium change does not match the top 3",
            lambda v: (
                FOUR(v),
                add_podium(kind="swap", manager="Dawid", previous_rank=3, rank=4, gap=-20)(v),
            ),
        ),
        ("key moments: more than 5", add_moments(*[{}] * 6)),
        ("hits: a transfer gain does not match its transfers", add_hit(transfer_gain=6)),
        ("hits: paid_off does not match the gain and the cost", add_hit(paid_off=False)),
        ("hits: paid_off does not match the gain and the cost", add_hit(cost=5)),
        ("empty_sections does not match the sections", add_pitch_item),
    ],
)
def test_inconsistent_v3_fact_is_reported(expected, mutate):
    assert expected in problems_after(mutate, recompute=False)


CAPTAIN_PICK = {
    "manager": "Bartas",
    "player": "Alpha",
    "base_points": 12,
    "multiplier": 2,
    "points": 24,
    "triple_captain": False,
    "vice_stepped_in": False,
    "blank": False,
}


def add_pitch(**changes):
    def mutate(value):
        value.pitch = value.pitch.model_validate({"items": [PITCH_ITEM | changes]})

    return mutate


def add_captain(**changes):
    def mutate(value):
        value.captaincy = value.captaincy.model_validate({"picks": [CAPTAIN_PICK | changes]})

    return mutate


def breakdown_of(*entries):
    return [{"identifier": i, "value": v, "points": p} for i, v, p in entries]


@pytest.mark.parametrize(
    ("expected", "mutate"),
    [
        (
            "breakdown: does not sum to the player's points",
            add_pitch(breakdown=breakdown_of(("minutes", 90, 2), ("goals_scored", 1, 9))),
        ),
        (
            "breakdown: does not sum to the player's points",
            add_captain(breakdown=breakdown_of(("minutes", 90, 2), ("goals_scored", 1, 9))),
        ),
        (
            "xG: a flag does not match the thresholds",
            add_pitch(
                points=3,
                breakdown=breakdown_of(("minutes", 90, 3)),
                expected_goals="0.90",
                big_miss=True,
                goal_from_nothing=False,
            ),
        ),
        (
            "xG: a flag does not match the thresholds",
            add_pitch(
                points=7,
                breakdown=breakdown_of(("minutes", 90, 2), ("goals_scored", 1, 5)),
                expected_goals="0.15",
                big_miss=False,
                goal_from_nothing=True,
            ),
        ),
        ("xG: a flag does not match the thresholds", add_pitch(big_miss=True)),
        ("pitch: a hat-trick without 3 goals", add_pitch(kinds=["hat_trick"])),
        (
            "pitch: a hat-trick without 3 goals",
            add_pitch(
                kinds=["hat_trick"],
                points=12,
                breakdown=breakdown_of(("minutes", 90, 2), ("goals_scored", 2, 10)),
            ),
        ),
        ("pitch: a keeper wall without 2 save points", add_pitch(kinds=["keeper_wall"])),
        (
            "pitch: a keeper wall without 2 save points",
            add_pitch(
                kinds=["keeper_wall"],
                points=3,
                breakdown=breakdown_of(("minutes", 90, 2), ("saves", 5, 1)),
            ),
        ),
        (
            "xG: a flag does not match the thresholds",
            add_captain(expected_goals="0.80", big_miss=True, goal_from_nothing=False),
        ),
    ],
)
def test_inconsistent_v4_fact_is_reported(expected, mutate):
    assert expected in problems_after(mutate, recompute=False)


def moment_entries(*entries):
    return [
        {"identifier": i, "value": v, "points": p, "swing": w, "decisive": d}
        for i, v, p, w, d in entries
    ]


BREAKDOWN_MOMENT = {
    "ahead_multiplier": 1,
    "behind_multiplier": 0,
    "breakdown": moment_entries(("minutes", 90, 2, 2, False), ("goals_scored", 1, 12, 12, True)),
}


def with_entry(index: int, **changes):
    entries = [dict(entry) for entry in BREAKDOWN_MOMENT["breakdown"]]
    entries[index] |= changes
    return BREAKDOWN_MOMENT | {"breakdown": entries}


RED_ENTRY_MOMENT = {
    "margin": 2,
    "ahead_multiplier": 0,
    "behind_multiplier": 1,
    "ahead_points": 0,
    "behind_points": -2,
    "swing": 2,
}


@pytest.mark.parametrize(
    ("expected", "mutate"),
    [
        (
            "breakdown: does not sum to the player's points",
            add_moments(BREAKDOWN_MOMENT | {"ahead_points": 15, "swing": 15}),
        ),
        (
            "breakdown: does not sum to the player's points",
            add_moments(BREAKDOWN_MOMENT | {"behind_multiplier": 1}),
        ),
        (
            "key moments: a breakdown swing does not match its points and multipliers",
            add_moments(with_entry(1, swing=11)),
        ),
        (
            "key moments: decisive does not match the swing and the margin",
            add_moments(with_entry(0, decisive=True)),
        ),
        (
            "key moments: decisive does not match the swing and the margin",
            add_moments(with_entry(1, decisive=False)),
        ),
        (
            "key moments: decisive does not match the swing and the margin",
            add_moments(
                RED_ENTRY_MOMENT
                | {
                    "breakdown": moment_entries(
                        ("minutes", 40, 1, -1, False), ("red_cards", 1, -3, 3, True)
                    )
                }
            ),
        ),
        (
            "key moments: a swing is below its margin",
            add_moments(with_entry(1, decisive=False) | {"margin": 15}),
        ),
        (
            "key moments: breakdown or multipliers on a kind that has none",
            add_moments(BREAKDOWN_MOMENT | {"kind": "red_card"}),
        ),
        (
            "key moments: breakdown or multipliers on a kind that has none",
            add_moments({"kind": "clean_sheet_lost", "ahead_multiplier": 1}),
        ),
    ],
)
def test_inconsistent_v4_moment_is_reported(expected, mutate):
    assert expected in problems_after(mutate, recompute=False)


MISSED_MOMENT = {
    "kind": "bonus_missed_by_one",
    "margin": 1,
    "ahead_multiplier": 0,
    "behind_multiplier": 2,
    "ahead_points": 0,
    "behind_points": 2,
    "swing": 2,
    "missed_bonus": 1,
}


@pytest.mark.parametrize(
    ("expected", "mutate"),
    [
        (
            "key moments: a missed bonus swing is below its margin",
            add_moments(MISSED_MOMENT | {"margin": 3}),
        ),
        (
            "key moments: a swing does not match the points",
            add_moments(MISSED_MOMENT | {"swing": 1}),
        ),
        (
            "key moments: a swing does not match the points",
            add_moments(MISSED_MOMENT | {"missed_bonus": 2}),
        ),
        (
            "key moments: a swing does not match the points",
            add_moments(MISSED_MOMENT | {"missed_bonus": None}),
        ),
        (
            "key moments: missed_bonus on a kind that has none",
            add_moments({"missed_bonus": 1}),
        ),
        (
            "key moments: expected goals conceded outside a lost clean sheet",
            add_moments({"expected_goals_conceded": "1.20"}),
        ),
    ],
)
def test_inconsistent_missed_bonus_is_reported(expected, mutate):
    assert expected in problems_after(mutate, recompute=False)


def test_lost_clean_sheet_with_xgc_is_consistent():
    moment = {
        "kind": "clean_sheet_lost",
        "margin": 4,
        "ahead_points": 0,
        "behind_points": -4,
        "swing": 4,
        "expected_goals_conceded": "1.40",
    }
    assert problems_after(add_moments(moment)) == []


def test_consistent_missed_bonus_has_no_problems():
    assert problems_after(add_moments(MISSED_MOMENT)) == []


def test_negative_swing_moment_with_a_decisive_stat_is_consistent():
    moment = {
        "margin": 1,
        "ahead_multiplier": 0,
        "behind_multiplier": 2,
        "ahead_points": 0,
        "behind_points": 2,
        "swing": -2,
        "breakdown": moment_entries(
            ("minutes", 90, 2, -4, False), ("yellow_cards", 1, -1, 2, True)
        ),
    }
    assert problems_after(add_moments(moment)) == []


def test_consistent_v4_stats_have_no_problems():
    full = breakdown_of(("minutes", 90, 2), ("goals_scored", 1, 10))
    assert (
        problems_after(
            lambda v: (
                add_pitch(
                    breakdown=full,
                    expected_goals="0.10",
                    expected_assists="0.00",
                    big_miss=False,
                    goal_from_nothing=True,
                )(v),
                add_captain(
                    breakdown=full,
                    expected_goals="0.10",
                    big_miss=False,
                    goal_from_nothing=True,
                )(v),
            )
        )
        == []
    )


def test_chips_and_pitch_sections_consistent_when_listed():
    assert (
        problems_after(
            lambda v: (
                add_chip(v),
                add_pitch_item(v),
                add_hit()(v),
                add_swap()(v),
                add_podium()(v),
                add_moments({})(v),
            )
        )
        == []
    )
