from app.presser.evaluation.cases import EDGE_TAGS, PresserCase
from app.presser.facts import FactSheet, check_fact_sheet
from app.presser.facts.schema import empty_sections
from app.presser.writer import PreviousPresser


def sheet(gameweek: int = 5, league: str = "League One") -> FactSheet:
    value = FactSheet.model_validate(
        {
            "league": league,
            "season": "2026/27",
            "gameweek": gameweek,
            "managers": 2,
            "average_points": 55.0,
            "winners": [
                {
                    "manager": "Bartas",
                    "points": 60,
                    "transfers_cost": 0,
                    "net_points": 60,
                    "nth_of_season": 1,
                }
            ],
            "flops": [
                {
                    "manager": "Kuba",
                    "points": 50,
                    "transfers_cost": 0,
                    "net_points": 50,
                    "nth_of_season": 1,
                }
            ],
            "table": {
                "rows": [
                    {"rank": 1, "manager": "Bartas", "total_points": 120, "movement": None},
                    {"rank": 2, "manager": "Kuba", "total_points": 100, "movement": None},
                ],
                "top3": [
                    {"manager": "Bartas", "total_points": 120, "behind_leader": 0},
                    {"manager": "Kuba", "total_points": 100, "behind_leader": 20},
                ],
            },
            "season_facts": {
                "rows": [
                    {
                        "manager": "Bartas",
                        "gameweek_wins_to_date": 1,
                        "gameweek_flops_to_date": 0,
                        "win_streak": 1,
                        "flop_streak": 0,
                        "captain_blank_streak": 0,
                    },
                    {
                        "manager": "Kuba",
                        "gameweek_wins_to_date": 0,
                        "gameweek_flops_to_date": 1,
                        "win_streak": 0,
                        "flop_streak": 1,
                        "captain_blank_streak": 0,
                    },
                ]
            },
        }
    )
    value.empty_sections = empty_sections(value)
    assert check_fact_sheet(value) == []
    return value


def case(
    case_id: str,
    split: str = "dev",
    source: str = "real",
    tags: list[str] | None = None,
    gameweek: int = 5,
    previous: list[PreviousPresser] | None = None,
) -> PresserCase:
    return PresserCase(
        id=case_id,
        split=split,
        source=source,
        tags=tags or [],
        facts=sheet(gameweek),
        previous=previous or [],
    )


def valid_set() -> list[PresserCase]:
    cases = [
        case(f"real-{n}", "dev" if n < 5 else "test", "real", gameweek=n % 5 + 1) for n in range(10)
    ]
    for n, tag in enumerate(EDGE_TAGS):
        cases.append(case(f"syn-{tag}", "dev" if n < 3 else "test", "synthetic", [tag]))
    return cases
