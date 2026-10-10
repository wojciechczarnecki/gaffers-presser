import httpx
import pytest
from langchain_core.messages import HumanMessage
from langchain_openrouter import ChatOpenRouter
from pydantic import SecretStr

from app.llm.chat import LlmConfig, build_chat_model
from app.llm.models import ModelSettings

DUMMY_KEY = SecretStr("dummy-key")


def _row(effort="none", temperature=True, method="function_calling") -> ModelSettings:
    return ModelSettings(
        reasoning_effort=effort, temperature=temperature, structured_method=method, checked="x"
    )


def _config(model="a/primary", row=None, fallback=None, fallback_row=None) -> LlmConfig:
    return LlmConfig(
        model=model,
        fallback_model=fallback,
        api_key=DUMMY_KEY,
        settings=row or _row(),
        fallback_settings=fallback_row,
    )


def test_builds_chat_openrouter():
    spec = build_chat_model(_config())
    assert isinstance(spec.chat_model, ChatOpenRouter)
    assert spec.chat_model.model_name == "a/primary"
    assert (spec.provider, spec.model) == ("openrouter", "a/primary")
    assert spec.structured_kwargs == {"method": "function_calling"}


def test_structured_method_comes_from_the_catalogue_row():
    spec = build_chat_model(_config(row=_row(method="json_schema")))
    assert spec.structured_kwargs == {"method": "json_schema"}


def test_timeout_is_sixty_seconds():
    assert build_chat_model(_config()).chat_model.request_timeout == 60_000


@pytest.mark.parametrize(
    "respond",
    [
        lambda request: httpx.Response(503, json={"error": {"message": "overloaded"}}),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused", request=request)),
    ],
    ids=["http-503", "connection-error"],
)
def test_sdk_client_sends_exactly_one_request(respond):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) > 1:
            raise RuntimeError("retried")  # fail fast instead of waiting out the SDK backoff
        return respond(request)

    chat_model = build_chat_model(_config()).chat_model
    chat_model.client.sdk_configuration.client = httpx.Client(
        transport=httpx.MockTransport(handler)
    )

    with pytest.raises(Exception):  # noqa: B017 - the SDK error class is not the point here
        chat_model.invoke([HumanMessage("Haaland is out.")])

    assert len(requests) == 1


def test_reasoning_effort_from_catalogue():
    for effort in ("none", "low"):
        chat_model = build_chat_model(_config(row=_row(effort=effort))).chat_model
        assert chat_model._default_params["reasoning"] == {"effort": effort}


def test_temperature_only_where_listed():
    assert build_chat_model(_config()).chat_model._default_params["temperature"] == 0
    params = build_chat_model(_config(row=_row(temperature=False))).chat_model._default_params
    assert "temperature" not in params


def test_temperature_argument_sent_only_where_accepted():
    assert build_chat_model(_config(), 0.8).chat_model._default_params["temperature"] == 0.8
    refusing = build_chat_model(_config(row=_row(temperature=False)), 0.8)
    assert "temperature" not in refusing.chat_model._default_params
    pair = _config(fallback="b/f", fallback_row=_row(temperature=False))
    assert "temperature" not in build_chat_model(pair, 0.8).chat_model._default_params


def test_require_parameters_sent():
    params = build_chat_model(_config()).chat_model._default_params
    assert params["provider"] == {"require_parameters": True}


def test_fallback_sent_as_models_list():
    with_fallback = build_chat_model(
        _config(fallback="b/fallback", fallback_row=_row())
    ).chat_model._default_params
    assert with_fallback["models"] == ["a/primary", "b/fallback"]
    assert with_fallback["model"] == "a/primary"
    assert "route" not in with_fallback
    assert "models" not in build_chat_model(_config()).chat_model._default_params


def test_pair_temperature_only_when_both_allow():
    both = _config(fallback="b/f", fallback_row=_row(temperature=True))
    assert build_chat_model(both).chat_model._default_params["temperature"] == 0
    for row, fallback_row in ((_row(), _row(temperature=False)), (_row(temperature=False), _row())):
        config = _config(row=row, fallback="b/f", fallback_row=fallback_row)
        assert "temperature" not in build_chat_model(config).chat_model._default_params
