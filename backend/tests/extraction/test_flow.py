from datetime import UTC, datetime

import pytest

from app.extraction.flow import ExtractionOutputError, build_flow
from app.extraction.linking import PlayerIndex, PlayerRecord, TeamRecord
from app.extraction.schemas import Disambiguation, ExtractedEvent, ExtractionOutput, PostInput
from app.llm.chat import ChatModelSpec
from tests.extraction.fakes import FakeChatModel

SEASON = "2026/27"

HAALAND = PlayerRecord(
    season=SEASON,
    fpl_id=1,
    web_name="Haaland",
    first_name="Erling",
    second_name="Haaland",
    team_fpl_id=1,
)
SMITH_ONE = PlayerRecord(
    season=SEASON, fpl_id=2, web_name="Smith", first_name="John", second_name="Smith", team_fpl_id=1
)
SMITH_TWO = PlayerRecord(
    season=SEASON,
    fpl_id=3,
    web_name="J.Smith",
    first_name="Jake",
    second_name="Smith",
    team_fpl_id=2,
)
JONES = PlayerRecord(
    season=SEASON, fpl_id=4, web_name="Jones", first_name="Joe", second_name="Jones", team_fpl_id=1
)

TEAM_ONE = TeamRecord(season=SEASON, fpl_id=1, name="Team One", short_name="ONE")
TEAM_TWO = TeamRecord(season=SEASON, fpl_id=2, name="Team Two", short_name="TWO")

INDEX = PlayerIndex(
    [HAALAND, SMITH_ONE, SMITH_TWO, JONES],
    [TEAM_ONE, TEAM_TWO],
)

POST = PostInput(
    x_id=1,
    author_handle="reporter",
    text="Haaland starts today.",
    created_at=datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
    is_repost=False,
    is_reply=False,
)


def _spec(*responses) -> ChatModelSpec:
    return ChatModelSpec(
        provider="fake", model="fake", chat_model=FakeChatModel(responses=list(responses))
    )


def _event(
    player: str, event_type: str = "confirmed_starter", team: str | None = None
) -> ExtractedEvent:
    return ExtractedEvent(player=player, team=team, event_type=event_type, certainty="confirmed")


def test_typed_result():
    output = ExtractionOutput(events=[_event("Haaland")])
    flow = build_flow(_spec(output), INDEX)

    result = flow.run(POST, config={})

    assert len(result.events) == 1
    event = result.events[0]
    assert event.mention == "Haaland"
    assert event.player_season == SEASON
    assert event.player_fpl_id == HAALAND.fpl_id
    assert event.event_type == "confirmed_starter"
    assert event.certainty == "confirmed"


def test_no_events_is_empty_result():
    output = ExtractionOutput(events=[])
    flow = build_flow(_spec(output), INDEX)

    result = flow.run(POST, config={})

    assert result.events == []
    assert result.llm_calls == 1


def test_leaked_xi_gives_starters_and_benched():
    output = ExtractionOutput(
        events=[
            _event("Haaland", "confirmed_starter"),
            _event("Jones", "benched"),
        ]
    )
    flow = build_flow(_spec(output), INDEX)

    result = flow.run(POST, config={})

    by_mention = {e.mention: e for e in result.events}
    assert by_mention["Haaland"].event_type == "confirmed_starter"
    assert by_mention["Jones"].event_type == "benched"
    assert by_mention["Haaland"].player_fpl_id == HAALAND.fpl_id
    assert by_mention["Jones"].player_fpl_id == JONES.fpl_id


def test_out_and_starts_gives_two_events():
    output = ExtractionOutput(
        events=[
            _event("Haaland", "out"),
            _event("Jones", "confirmed_starter"),
        ]
    )
    flow = build_flow(_spec(output), INDEX)

    result = flow.run(POST, config={})

    assert len(result.events) == 2


def test_repost_is_extracted_with_author():
    output = ExtractionOutput(events=[])
    spec = _spec(output)
    flow = build_flow(spec, INDEX)
    repost = PostInput(
        x_id=2,
        author_handle="listaccount",
        text="some news",
        created_at=POST.created_at,
        is_repost=True,
        is_reply=False,
    )

    flow.run(repost, config={})

    sent = spec.chat_model.received_messages[0][1].content
    assert "listaccount" in sent
    assert "Repost: True" in sent


def test_unique_match_makes_no_llm_call():
    output = ExtractionOutput(events=[_event("Haaland")])
    flow = build_flow(_spec(output), INDEX)

    result = flow.run(POST, config={})

    assert result.llm_calls == 1


def test_disambiguation_picks_candidate():
    output = ExtractionOutput(events=[_event("Smith")])
    disambiguation = Disambiguation(fpl_id=SMITH_TWO.fpl_id)
    flow = build_flow(_spec(output, disambiguation), INDEX)

    result = flow.run(POST, config={})

    assert result.events[0].player_fpl_id == SMITH_TWO.fpl_id
    assert result.llm_calls == 2


def test_disambiguation_outside_list_unlinked():
    output = ExtractionOutput(events=[_event("Smith")])
    disambiguation = Disambiguation(fpl_id=999)
    flow = build_flow(_spec(output, disambiguation), INDEX)

    result = flow.run(POST, config={})

    assert result.events[0].player_fpl_id is None
    assert result.events[0].player_season is None
    assert result.events[0].mention == "Smith"


def test_disambiguation_none_unlinked():
    output = ExtractionOutput(events=[_event("Smith")])
    disambiguation = Disambiguation(fpl_id=None)
    flow = build_flow(_spec(output, disambiguation), INDEX)

    result = flow.run(POST, config={})

    assert result.events[0].player_fpl_id is None


def test_disambiguation_error_unlinked():
    output = ExtractionOutput(events=[_event("Smith")])
    flow = build_flow(_spec(output, RuntimeError("boom")), INDEX)

    result = flow.run(POST, config={})

    assert len(result.events) == 1
    assert result.events[0].player_fpl_id is None
    assert result.events[0].mention == "Smith"


def test_no_candidate_unlinked_without_call():
    output = ExtractionOutput(events=[_event("Nobody")])
    flow = build_flow(_spec(output), INDEX)

    result = flow.run(POST, config={})

    assert result.events[0].player_fpl_id is None
    assert result.llm_calls == 1


def test_usage_summed_over_calls():
    output = ExtractionOutput(events=[_event("Smith")])
    disambiguation = Disambiguation(fpl_id=SMITH_ONE.fpl_id)
    flow = build_flow(_spec(output, disambiguation), INDEX)

    result = flow.run(POST, config={})

    assert result.usage.input_tokens == 20
    assert result.usage.output_tokens == 10


def test_invalid_output_raises():
    flow = build_flow(_spec({"events": "not-a-list"}), INDEX)

    with pytest.raises(ExtractionOutputError):
        flow.run(POST, config={})


def test_reasoning_tokens_summed():
    output = ExtractionOutput(events=[_event("Smith")])
    disambiguation = Disambiguation(fpl_id=SMITH_TWO.fpl_id)
    fake = FakeChatModel(responses=[output, disambiguation], reasoning_tokens=3, reported_cost=0.5)
    flow = build_flow(ChatModelSpec(provider="fake", model="fake", chat_model=fake), INDEX)

    result = flow.run(POST, config={})

    assert result.llm_calls == 2
    assert result.usage.reasoning_tokens == 6
    assert result.usage.reported_cost_usd == 1.0
    assert result.usage.input_tokens == 20


def test_no_reasoning_tokens_stays_none():
    flow = build_flow(_spec(ExtractionOutput(events=[])), INDEX)

    result = flow.run(POST, config={})

    assert result.usage.reasoning_tokens is None
    assert result.usage.reported_cost_usd is None


def test_answered_model_and_host_from_extraction_call():
    fake = FakeChatModel(
        responses=[ExtractionOutput(events=[])], response_model="b/fallback", host="DeepInfra"
    )
    flow = build_flow(ChatModelSpec(provider="fake", model="a/primary", chat_model=fake), INDEX)

    result = flow.run(POST, config={})

    assert result.answered_model == "b/fallback"
    assert result.host == "DeepInfra"
