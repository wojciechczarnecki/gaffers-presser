import pytest

from app.extraction.model_settings import DEFAULT_PATH, load_model_settings
from app.extraction.pricing import load_prices
from tests.extraction.candidates import CANDIDATES


def test_every_candidate_has_a_row():
    settings = load_model_settings()
    assert set(CANDIDATES) <= set(settings)


def test_reasoning_effort_is_valid_and_structured_method_defaults():
    for model, row in load_model_settings().items():
        assert row.reasoning_effort in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        assert row.structured_method in {"function_calling", "json_schema"}, model


def test_gpt_6_luna_takes_no_temperature_and_glm_reasons_at_low():
    settings = load_model_settings()
    assert settings["openai/gpt-6-luna"].temperature is False
    assert settings["z-ai/glm-5.3-flash"].reasoning_effort == "low"
    assert settings["google/gemini-3.1-flash-lite"].temperature is True


def test_invalid_reasoning_effort_raises(tmp_path):
    path = tmp_path / "m.toml"
    path.write_text(
        '["a/b"]\nreasoning_effort = "loud"\ntemperature = true\nchecked = "2026-09-29"\n'
    )
    with pytest.raises(ValueError, match="reasoning_effort"):
        load_model_settings(path)


def test_invalid_structured_method_raises(tmp_path):
    path = tmp_path / "m.toml"
    path.write_text(
        '["a/b"]\nreasoning_effort = "low"\ntemperature = true\nchecked = "2026-09-29"\n'
        'structured_method = "magic"\n'
    )
    with pytest.raises(ValueError, match="structured_method"):
        load_model_settings(path)


def test_structured_method_override_is_read(tmp_path):
    path = tmp_path / "m.toml"
    path.write_text(
        '["a/b"]\nreasoning_effort = "low"\ntemperature = false\nchecked = "2026-09-29"\n'
        'structured_method = "json_schema"\n'
    )
    row = load_model_settings(path)["a/b"]
    assert row.structured_method == "json_schema"
    assert row.temperature is False


def test_model_settings_and_prices_have_the_same_models():
    assert DEFAULT_PATH.exists()
    assert set(load_model_settings()) == set(load_prices())
