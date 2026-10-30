from app.corroboration.config import DEFAULT_JUDGE_MODEL, CorroborationSettings, judge_model
from app.corroboration.runtime import build_runtime
from app.llm.chat import DEFAULT_MODEL
from tests.retrieval.helpers import FixedClock


def _clock() -> FixedClock:
    return FixedClock()


def test_the_judge_runs_on_its_default_model():
    settings = CorroborationSettings(_env_file=None, openrouter_api_key="dummy-key")
    runtime = build_runtime(settings, _clock())
    assert judge_model(settings) == DEFAULT_JUDGE_MODEL
    assert runtime.judge is not None and runtime.judge.model == DEFAULT_JUDGE_MODEL


def test_llm_judge_model_overrides_and_extraction_keeps_llm_model():
    from app.llm.chat import resolve_llm

    settings = CorroborationSettings(
        _env_file=None,
        openrouter_api_key="dummy-key",
        llm_judge_model="google/gemini-3.1-flash-lite",
        llm_model="deepseek/deepseek-v4-flash",
    )
    runtime = build_runtime(settings, _clock())
    assert runtime.judge is not None and runtime.judge.model == "google/gemini-3.1-flash-lite"
    llm = resolve_llm(settings)
    assert llm is not None and llm.model == "deepseek/deepseek-v4-flash"


def test_the_judge_default_is_not_the_extraction_default():
    # the rule picked a judge other than the extraction default (ADR 0011)
    assert DEFAULT_JUDGE_MODEL != DEFAULT_MODEL
