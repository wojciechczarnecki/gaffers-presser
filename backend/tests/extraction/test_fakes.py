from app.extraction.schemas import ExtractedEvent, ExtractionOutput
from tests.extraction.fakes import FakeChatModel


def test_fake_returns_parsed_and_usage_through_structured_output():
    output = ExtractionOutput(
        events=[
            ExtractedEvent(player="Haaland", team=None, event_type="out", certainty="confirmed")
        ]
    )
    fake = FakeChatModel(responses=[output])
    structured = fake.with_structured_output(ExtractionOutput, include_raw=True)

    result = structured.invoke("some post text")

    assert result["parsed"] == output
    assert result["parsing_error"] is None
    assert result["raw"].usage_metadata["input_tokens"] == 10
    assert result["raw"].usage_metadata["output_tokens"] == 5


def test_fake_records_received_messages():
    output = ExtractionOutput(events=[])
    fake = FakeChatModel(responses=[output])
    structured = fake.with_structured_output(ExtractionOutput, include_raw=True)

    structured.invoke("post about nothing relevant")

    assert len(fake.received_messages) == 1
    assert "post about nothing relevant" in fake.received_messages[0][0].content


def test_malformed_script_entry_gives_parsing_error():
    fake = FakeChatModel(responses=[{"events": "not-a-list"}])
    structured = fake.with_structured_output(ExtractionOutput, include_raw=True)

    result = structured.invoke("post")

    assert result["parsed"] is None
    assert result["parsing_error"] is not None
