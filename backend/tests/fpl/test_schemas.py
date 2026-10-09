import pytest

from app.fpl.errors import PayloadError
from app.fpl.schemas import Bootstrap, Fixture, Live, Picks, parse, parse_list
from tests.fpl.payloads import load


def test_bootstrap_parses():
    parse(Bootstrap, "bootstrap-static", load("bootstrap-static"))


def test_fixtures_parse():
    parse_list(Fixture, "fixtures", load("fixtures"))


@pytest.mark.parametrize("gw", [1, 2, 3])
def test_live_parses(gw):
    parse(Live, "event/{gw}/live", load(f"event-{gw}-live"))


def test_missing_field_names_endpoint_and_field():
    payload = load("bootstrap-static")
    del payload["elements"][0]["status"]
    with pytest.raises(PayloadError) as exc_info:
        parse(Bootstrap, "bootstrap-static", payload)
    assert exc_info.value.endpoint == "bootstrap-static"
    assert exc_info.value.field == "elements.0.status"


def test_missing_nullable_field_fails():
    payload = load("bootstrap-static")
    del payload["elements"][0]["chance_of_playing_this_round"]
    with pytest.raises(PayloadError) as exc_info:
        parse(Bootstrap, "bootstrap-static", payload)
    assert exc_info.value.field == "elements.0.chance_of_playing_this_round"


def test_unknown_fields_ignored():
    payload = load("bootstrap-static")
    payload["elements"][0]["brand_new"] = 1
    parse(Bootstrap, "bootstrap-static", payload)


def _synthetic_picks(entry_id: int = 987000001) -> dict:
    return {
        "active_chip": None,
        "automatic_subs": [],
        "entry_history": {
            "points": 60,
            "total_points": 60,
            "event_transfers": 0,
            "event_transfers_cost": 0,
            "points_on_bench": 0,
            "value": 1000,
            "overall_rank": 1000,
        },
        "picks": [],
    }


def test_missing_entry_history_field_names_endpoint_and_field():
    payload = _synthetic_picks()
    with pytest.raises(PayloadError) as exc_info:
        parse(Picks, "entry/{entry_id}/event/{gw}/picks", payload)
    assert exc_info.value.endpoint == "entry/{entry_id}/event/{gw}/picks"
    assert exc_info.value.field == "entry_history.bank"
    assert "987000001" not in str(exc_info.value)


def test_entry_history_rank_optional():
    payload = _synthetic_picks()
    payload["entry_history"]["bank"] = 5
    assert parse(Picks, "p", payload).entry_history.rank is None
    payload["entry_history"]["rank"] = 12345
    assert parse(Picks, "p", payload).entry_history.rank == 12345
    payload["entry_history"]["rank"] = None
    assert parse(Picks, "p", payload).entry_history.rank is None
