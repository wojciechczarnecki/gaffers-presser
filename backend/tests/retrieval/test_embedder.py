import json
from pathlib import Path

import httpx
import pytest
from openrouter.errors import OpenRouterError
from pydantic import SecretStr

from app.retrieval.embedder import OpenRouterEmbedder

PAYLOAD = json.loads(
    (Path(__file__).parent / "payloads" / "openrouter_embeddings.json").read_text()
)
MODEL = "openai/text-embedding-3-small"


def _embedder(handler) -> tuple[OpenRouterEmbedder, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return OpenRouterEmbedder(SecretStr("dummy-key"), MODEL, client=client), requests


def test_request_body_carries_model_input_and_encoding_format():
    embedder, requests = _embedder(lambda request: httpx.Response(200, json=PAYLOAD))
    embedder.embed(["first", "second"])
    (request,) = requests
    body = json.loads(request.content)
    assert body["model"] == MODEL
    assert body["input"] == ["first", "second"]
    assert body["encoding_format"] == "float"
    assert request.headers["authorization"] == "Bearer dummy-key"


def test_vectors_returned_in_index_order():
    embedder, _ = _embedder(lambda request: httpx.Response(200, json=PAYLOAD))
    result = embedder.embed(["first", "second"])
    assert result.vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


def test_input_tokens_come_from_the_payload():
    embedder, _ = _embedder(lambda request: httpx.Response(200, json=PAYLOAD))
    assert embedder.embed(["first", "second"]).input_tokens == 11


def test_missing_usage_gives_no_tokens():
    payload = {k: v for k, v in PAYLOAD.items() if k != "usage"}
    embedder, _ = _embedder(lambda request: httpx.Response(200, json=payload))
    assert embedder.embed(["first", "second"]).input_tokens is None


def test_count_mismatch_raises_value_error():
    embedder, _ = _embedder(lambda request: httpx.Response(200, json=PAYLOAD))
    with pytest.raises(ValueError):
        embedder.embed(["only one"])


def test_server_error_raises_after_exactly_one_request():
    embedder, requests = _embedder(lambda request: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(OpenRouterError):
        embedder.embed(["first", "second"])
    assert len(requests) == 1
