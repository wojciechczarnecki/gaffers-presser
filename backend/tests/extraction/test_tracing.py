from datetime import UTC, datetime

from pydantic import SecretStr

from app.extraction.config import TracingConfig
from app.extraction.flow import build_flow
from app.extraction.linking import PlayerIndex, PlayerRecord, TeamRecord
from app.extraction.providers import ChatModelSpec
from app.extraction.schemas import Disambiguation, ExtractedEvent, ExtractionOutput, PostInput
from app.extraction.tracing import flush, make_handler, run_config
from tests.extraction.fakes import FakeChatModel, RecordingHandler

SEASON = "2026/27"

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
TEAM_ONE = TeamRecord(season=SEASON, fpl_id=1, name="Team One", short_name="ONE")
TEAM_TWO = TeamRecord(season=SEASON, fpl_id=2, name="Team Two", short_name="TWO")
INDEX = PlayerIndex([SMITH_ONE, SMITH_TWO], [TEAM_ONE, TEAM_TWO])

POST = PostInput(
    x_id=42,
    author_handle="reporter",
    text="Smith starts today.",
    created_at=datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
    is_repost=False,
    is_reply=False,
)


def _ambiguous_flow() -> tuple[RecordingHandler, object]:
    output = ExtractionOutput(
        events=[
            ExtractedEvent(
                player="Smith", team=None, event_type="confirmed_starter", certainty="confirmed"
            )
        ]
    )
    disambiguation = Disambiguation(fpl_id=SMITH_TWO.fpl_id)
    spec = ChatModelSpec(
        provider="fake", model="fake", chat_model=FakeChatModel(responses=[output, disambiguation])
    )
    return RecordingHandler(), build_flow(spec, INDEX)


def test_one_trace_holds_extraction_and_disambiguation_calls():
    handler, flow = _ambiguous_flow()
    config = run_config(
        x_id=POST.x_id,
        prompt_version="extraction@1+link_disambiguation@1",
        provider="fake",
        model="fake",
        handler=handler,
    )

    result = flow.run(POST, config=config)

    assert result.events[0].player_fpl_id == SMITH_TWO.fpl_id
    assert len(handler.chat_model_starts) == 2
    assert len(handler.llm_ends) == 2
    for start in handler.chat_model_starts:
        assert start["metadata"]["x_id"] == POST.x_id
        assert start["metadata"]["prompt_version"] == "extraction@1+link_disambiguation@1"
    roots = {handler.root_run_id(start["run_id"]) for start in handler.chat_model_starts}
    assert len(roots) == 1
    for end in handler.llm_ends:
        assert end["usage"]["input_tokens"] == 10
        assert end["usage"]["output_tokens"] == 5


def test_no_handler_without_keys():
    assert make_handler(None) is None


def test_handler_built_offline():
    tracing = TracingConfig(
        public_key=SecretStr("pk-fake"), secret_key=SecretStr("sk-fake"), host="http://127.0.0.1:9"
    )

    handler = make_handler(tracing)

    assert handler is not None
    flush(handler)
