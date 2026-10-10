import pytest

from app.presser.facts import check_fact_sheet
from app.presser.facts.schema import FactSheet, empty_sections
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


def problems_after(mutate) -> list[str]:
    value = ranked_sheet()
    mutate(value)
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
