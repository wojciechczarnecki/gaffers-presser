from datetime import UTC, datetime, timedelta

from app.extraction import lineups, store
from app.extraction.evaluation import runner
from app.extraction.evaluation.cases import EvalCase, ExpectedEvent
from app.extraction.evaluation.runner import run_evaluation
from app.extraction.fixtures import FixtureIndex, FixtureRecord
from app.extraction.linking import PlayerIndex, PlayerRecord, TeamRecord
from app.extraction.schemas import ExtractedEvent, ExtractionOutput, MatchCheck
from app.extraction.tracing import NULL_EXTRACTION_TRACER
from app.llm.chat import ChatModelSpec
from tests.extraction.fakes import FakeChatModel

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
SEASON = "2026/27"
PLAYERS = [
    PlayerRecord(
        season=SEASON,
        fpl_id=5,
        web_name="Haaland",
        first_name="Erling",
        second_name="Haaland",
        team_fpl_id=1,
    )
]
TEAMS = [
    TeamRecord(season=SEASON, fpl_id=1, name="Man City", short_name="MCI"),
    TeamRecord(season=SEASON, fpl_id=2, name="Arsenal", short_name="ARS"),
]


class _Clock:
    def now(self):
        return NOW

    def sleep(self, seconds):
        pass


def _case(tags=(), events=()) -> EvalCase:
    return EvalCase(
        id="1",
        author_handle="reporter",
        text="Haaland is on the bench",
        created_at=NOW,
        is_repost=False,
        is_reply=False,
        split="test",
        synthetic=False,
        reviewed=True,
        tags=list(tags),
        expected_events=list(events),
    )


def _fixtures(lead: timedelta) -> FixtureIndex:
    return FixtureIndex(
        [
            FixtureRecord(
                season=SEASON,
                fpl_id=1,
                gameweek=1,
                kickoff_at=NOW + lead,
                team_h_fpl_id=1,
                team_a_fpl_id=2,
            )
        ]
    )


def _run(case, fixtures, *responses, certainty="likely"):
    spec = ChatModelSpec(
        provider="fake", model="fake", chat_model=FakeChatModel(responses=list(responses))
    )
    return run_evaluation(
        [case],
        spec,
        PlayerIndex(PLAYERS, TEAMS),
        NULL_EXTRACTION_TRACER,
        run_name="r",
        split="test",
        clock=_Clock(),
        posts_per_month=1000,
        usd_pln_rate=4.0,
        fixtures=fixtures,
    )


def _benched(certainty="likely") -> ExtractionOutput:
    return ExtractionOutput(
        events=[
            ExtractedEvent(player="Haaland", team=None, event_type="benched", certainty=certainty)
        ]
    )


def test_scores_the_events_kept_at_read():
    report = _run(
        _case(tags=["other_match_lineup"]),
        _fixtures(timedelta(hours=10)),
        _benched(),
        MatchCheck(for_next_pl_match=False),
    )

    [outcome] = report.outcomes
    assert outcome.result.predicted == []
    [raw] = outcome.result.raw_predicted
    assert (raw.for_next_pl_match, raw.kept, raw.kept_by_lead_only) == (False, False, True)
    assert report.metrics.other_match_cases == 1
    assert report.metrics.other_match_false_alarms == 0
    assert report.metrics.other_match_with_raw_events == 1
    assert report.metrics.other_match_caught_by_lead_only == 0


def test_an_out_event_answered_false_is_not_predicted():
    out = ExtractionOutput(
        events=[
            ExtractedEvent(player="Haaland", team=None, event_type="out", certainty="confirmed")
        ]
    )
    report = _run(_case(), _fixtures(timedelta(hours=10)), out, MatchCheck(for_next_pl_match=False))

    [outcome] = report.outcomes
    assert outcome.result.predicted == []
    [raw] = outcome.result.raw_predicted
    assert (raw.event_type, raw.for_next_pl_match, raw.kept) == ("out", False, False)


def test_lead_only_filter_reported():
    report = _run(
        _case(tags=["other_match_lineup"]),
        _fixtures(timedelta(hours=70)),
        _benched("confirmed"),
        MatchCheck(for_next_pl_match=True),
    )

    [outcome] = report.outcomes
    assert outcome.result.predicted == []
    [raw] = outcome.result.raw_predicted
    assert (raw.kept, raw.kept_by_lead_only) == (False, False)
    assert report.metrics.other_match_caught_by_lead_only == 1


def test_a_kept_lineup_is_scored_and_a_dropped_expected_one_is_counted():
    expected = ExpectedEvent(mention="Haaland", fpl_id=5, event_type="benched", certainty="likely")
    kept = _run(
        _case(events=[expected]),
        _fixtures(timedelta(hours=10)),
        _benched(),
        MatchCheck(for_next_pl_match=True),
    )
    assert [e.event_type for e in kept.outcomes[0].result.predicted] == ["benched"]
    assert kept.metrics.lineups_dropped_by_check == 0

    dropped = _run(
        _case(events=[expected]),
        _fixtures(timedelta(hours=10)),
        _benched(),
        MatchCheck(for_next_pl_match=False),
    )
    assert dropped.metrics.lineups_dropped_by_check == 1
    assert dropped.metrics.recall == 0.0


def test_runner_and_store_share_the_predicate():
    assert runner.kept_at_read is store.kept_at_read is lineups.kept_at_read
