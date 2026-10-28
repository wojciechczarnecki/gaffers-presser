from datetime import UTC, datetime, timedelta

import pytest

from app.content import load_prompt
from app.extraction.fixtures import FixtureIndex, FixtureRecord
from app.extraction.flow import PROMPT_VERSION, ExtractionOutputError, build_flow
from app.extraction.linking import PlayerIndex, PlayerRecord, TeamRecord
from app.extraction.schemas import (
    Disambiguation,
    ExtractedEvent,
    ExtractionOutput,
    MatchCheck,
    PostInput,
)
from app.llm.chat import ChatModelSpec
from tests.extraction.fakes import FakeChatModel, RecordingExtractionTracer

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

    result = flow.run(POST)

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

    result = flow.run(POST)

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

    result = flow.run(POST)

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

    result = flow.run(POST)

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

    flow.run(repost)

    sent = spec.chat_model.received_messages[0][1].content
    assert "listaccount" in sent
    assert "Repost: True" in sent


def test_unique_match_makes_no_llm_call():
    output = ExtractionOutput(events=[_event("Haaland")])
    flow = build_flow(_spec(output), INDEX)

    result = flow.run(POST)

    assert result.llm_calls == 1


def test_disambiguation_picks_candidate():
    output = ExtractionOutput(events=[_event("Smith")])
    disambiguation = Disambiguation(fpl_id=SMITH_TWO.fpl_id)
    flow = build_flow(_spec(output, disambiguation), INDEX)

    result = flow.run(POST)

    assert result.events[0].player_fpl_id == SMITH_TWO.fpl_id
    assert result.llm_calls == 2


def test_club_of_another_linked_player_skips_disambiguation():
    output = ExtractionOutput(events=[_event("Smith"), _event("Jones")])
    flow = build_flow(_spec(output), INDEX)

    result = flow.run(POST)

    assert [e.player_fpl_id for e in result.events] == [SMITH_ONE.fpl_id, JONES.fpl_id]
    assert result.llm_calls == 1


def test_disambiguation_outside_list_unlinked():
    output = ExtractionOutput(events=[_event("Smith")])
    disambiguation = Disambiguation(fpl_id=999)
    flow = build_flow(_spec(output, disambiguation), INDEX)

    result = flow.run(POST)

    assert result.events[0].player_fpl_id is None
    assert result.events[0].player_season is None
    assert result.events[0].mention == "Smith"


def test_disambiguation_none_unlinked():
    output = ExtractionOutput(events=[_event("Smith")])
    disambiguation = Disambiguation(fpl_id=None)
    flow = build_flow(_spec(output, disambiguation), INDEX)

    result = flow.run(POST)

    assert result.events[0].player_fpl_id is None


def test_disambiguation_error_unlinked():
    output = ExtractionOutput(events=[_event("Smith")])
    flow = build_flow(_spec(output, RuntimeError("boom")), INDEX)

    result = flow.run(POST)

    assert len(result.events) == 1
    assert result.events[0].player_fpl_id is None
    assert result.events[0].mention == "Smith"


def test_no_candidate_unlinked_without_call():
    output = ExtractionOutput(events=[_event("Nobody")])
    flow = build_flow(_spec(output), INDEX)

    result = flow.run(POST)

    assert result.events[0].player_fpl_id is None
    assert result.llm_calls == 1


def test_usage_summed_over_calls():
    output = ExtractionOutput(events=[_event("Smith")])
    disambiguation = Disambiguation(fpl_id=SMITH_ONE.fpl_id)
    flow = build_flow(_spec(output, disambiguation), INDEX)

    result = flow.run(POST)

    assert result.usage.input_tokens == 20
    assert result.usage.output_tokens == 10


def test_invalid_output_raises():
    flow = build_flow(_spec({"events": "not-a-list"}), INDEX)

    with pytest.raises(ExtractionOutputError):
        flow.run(POST)


def test_reasoning_tokens_summed():
    output = ExtractionOutput(events=[_event("Smith")])
    disambiguation = Disambiguation(fpl_id=SMITH_TWO.fpl_id)
    fake = FakeChatModel(responses=[output, disambiguation], reasoning_tokens=3, reported_cost=0.5)
    flow = build_flow(ChatModelSpec(provider="fake", model="fake", chat_model=fake), INDEX)

    result = flow.run(POST)

    assert result.llm_calls == 2
    assert result.usage.reasoning_tokens == 6
    assert result.usage.reported_cost_usd == 1.0
    assert result.usage.input_tokens == 20


def test_no_reasoning_tokens_stays_none():
    flow = build_flow(_spec(ExtractionOutput(events=[])), INDEX)

    result = flow.run(POST)

    assert result.usage.reasoning_tokens is None
    assert result.usage.reported_cost_usd is None


def test_answered_model_and_host_from_extraction_call():
    fake = FakeChatModel(
        responses=[ExtractionOutput(events=[])], response_model="b/fallback", host="DeepInfra"
    )
    flow = build_flow(ChatModelSpec(provider="fake", model="a/primary", chat_model=fake), INDEX)

    result = flow.run(POST)

    assert result.answered_model == "b/fallback"
    assert result.host == "DeepInfra"


TEAM_THREE = TeamRecord(season=SEASON, fpl_id=3, name="Team Three", short_name="THR")
KICKOFF = POST.created_at


def _fixture(fpl_id, kickoff, home, away) -> FixtureRecord:
    return FixtureRecord(
        season=SEASON,
        fpl_id=fpl_id,
        gameweek=fpl_id,
        kickoff_at=kickoff,
        team_h_fpl_id=home,
        team_a_fpl_id=away,
    )


FIXTURES = FixtureIndex(
    [
        _fixture(1, KICKOFF - timedelta(days=5), home=3, away=1),
        _fixture(2, KICKOFF + timedelta(days=2), home=1, away=3),
        _fixture(3, KICKOFF - timedelta(days=6), home=2, away=3),
        _fixture(4, KICKOFF + timedelta(days=3), home=3, away=2),
    ]
)
CHECK_INDEX = PlayerIndex([HAALAND, SMITH_ONE, SMITH_TWO, JONES], [TEAM_ONE, TEAM_TWO, TEAM_THREE])


def _checked_flow(*responses, fixtures=FIXTURES):
    spec = _spec(*responses)
    return build_flow(spec, CHECK_INDEX, fixtures=fixtures), spec.chat_model


def _lineup(*players, event_type="confirmed_starter") -> ExtractionOutput:
    return ExtractionOutput(events=[_event(p, event_type) for p in players])


def test_match_check_one_call_per_club():
    flow, chat = _checked_flow(
        _lineup("Haaland", "Jones", "John Smith"), MatchCheck(for_next_pl_match=False)
    )

    result = flow.run(POST)

    assert result.llm_calls == 2
    assert len(chat.received_messages) == 2
    assert [e.for_next_pl_match for e in result.events] == [False, False, False]


def test_no_match_check_without_lineup_events():
    flow, chat = _checked_flow(_lineup("Haaland", "Jones", event_type="out"))

    result = flow.run(POST)

    assert result.llm_calls == 1
    assert len(chat.received_messages) == 1
    assert [e.for_next_pl_match for e in result.events] == [None, None]


def test_match_check_two_clubs_two_calls():
    flow, chat = _checked_flow(
        _lineup("Haaland", "J.Smith", "Jones"),
        MatchCheck(for_next_pl_match=False),
        MatchCheck(for_next_pl_match=True),
    )

    result = flow.run(POST)

    assert result.llm_calls == 3
    assert len(chat.received_messages) == 3
    assert [e.for_next_pl_match for e in result.events] == [False, True, False]


def test_match_check_input_from_fixture_data():
    output = ExtractionOutput(
        events=[
            ExtractedEvent(
                player="Haaland", team="Real Madrid", event_type="benched", certainty="likely"
            )
        ]
    )
    flow, chat = _checked_flow(output, MatchCheck(for_next_pl_match=True))

    flow.run(POST)

    system, human = chat.received_messages[1]
    assert system.content == load_prompt("match_check").text
    sent = human.content
    assert "Post: Haaland starts today." in sent
    assert "Posted at: 2026-09-28T12:00:00+00:00" in sent
    assert "Club: Team One" in sent
    assert "Previous PL fixture: v Team Three (A), 2026-09-23T12:00Z" in sent
    assert "Next PL fixture: v Team Three (H), 2026-09-30T12:00Z" in sent
    assert "Real Madrid" not in sent


def test_match_check_answer_on_that_clubs_lineup_events_only():
    output = ExtractionOutput(
        events=[
            _event("Haaland", "benched"),
            _event("Jones", "out"),
            _event("J.Smith", "confirmed_starter"),
        ]
    )
    flow, _ = _checked_flow(
        output, MatchCheck(for_next_pl_match=False), MatchCheck(for_next_pl_match=True)
    )

    result = flow.run(POST)

    assert [(e.mention, e.for_next_pl_match) for e in result.events] == [
        ("Haaland", False),
        ("Jones", None),
        ("J.Smith", True),
    ]


def test_no_call_for_unlinked_or_club_without_next_fixture():
    past_only = FixtureIndex([_fixture(1, KICKOFF - timedelta(days=5), home=3, away=1)])
    flow, chat = _checked_flow(_lineup("Haaland"), fixtures=past_only)
    result = flow.run(POST)
    assert len(chat.received_messages) == 1
    assert [e.for_next_pl_match for e in result.events] == [None]

    flow, chat = _checked_flow(_lineup("Nobody"))
    result = flow.run(POST)
    assert len(chat.received_messages) == 1
    assert [e.for_next_pl_match for e in result.events] == [None]

    flow = build_flow(_spec(_lineup("Haaland")), CHECK_INDEX)
    assert [e.for_next_pl_match for e in flow.run(POST).events] == [None]


def test_match_check_failure_is_fail_open():
    tracer = RecordingExtractionTracer()
    spec = _spec(_lineup("Haaland"), RuntimeError("boom"))
    flow = build_flow(spec, CHECK_INDEX, tracer, fixtures=FIXTURES)

    result = flow.run(POST)

    assert [e.for_next_pl_match for e in result.events] == [None]
    assert result.llm_calls == 1
    assert tracer.generations[-1]["error_class"] == "RuntimeError"

    tracer = RecordingExtractionTracer()
    spec = _spec(_lineup("Haaland"), {"for_next_pl_match": "not a bool"})
    flow = build_flow(spec, CHECK_INDEX, tracer, fixtures=FIXTURES)

    result = flow.run(POST)

    assert [e.for_next_pl_match for e in result.events] == [None]
    assert tracer.generations[-1]["error_class"] is not None


def test_prompt_version_names_three_prompts():
    assert PROMPT_VERSION.count("@") == 3
    assert "extraction@" in PROMPT_VERSION
    assert "+link_disambiguation@" in PROMPT_VERSION
    assert PROMPT_VERSION.endswith(f"+match_check@{load_prompt('match_check').version}")


def test_every_call_uses_the_flow_model():
    fake = FakeChatModel(
        responses=[
            ExtractionOutput(events=[_event("Smith", "benched")]),
            Disambiguation(fpl_id=SMITH_ONE.fpl_id),
            MatchCheck(for_next_pl_match=True),
        ]
    )
    spec = ChatModelSpec(provider="fake", model="fake", chat_model=fake)
    flow = build_flow(spec, CHECK_INDEX, fixtures=FIXTURES)

    result = flow.run(POST)

    assert result.llm_calls == 3
    assert len(fake.received_messages) == 3
    assert [e.for_next_pl_match for e in result.events] == [True]
