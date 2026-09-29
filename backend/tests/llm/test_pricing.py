from datetime import date

import pytest

from app.llm.pricing import Price, compute_cost, load_prices
from tests.extraction.candidates import CANDIDATES


def test_every_candidate_priced_with_checked_date():
    prices = load_prices()
    for model in CANDIDATES:
        price = prices[model]
        assert price.input_per_million > 0
        assert price.output_per_million > 0
        date.fromisoformat(price.checked)


def test_cost_keyed_by_openrouter_id():
    prices = {"a/b": Price(input_per_million=1.0, output_per_million=2.0, checked="2026-09-29")}
    assert compute_cost("a/b", 1_000_000, 500_000, prices) == pytest.approx(2.0)
    assert compute_cost("other/model", 1_000_000, 500_000, prices) is None
    assert compute_cost("a/b", None, 5, prices) is None


def test_input_only_row_loads_and_costs_input_alone(tmp_path):
    path = tmp_path / "prices.toml"
    path.write_text('["x/embed"]\ninput_per_million = 0.02\nchecked = "2026-09-29"\n')
    prices = load_prices(path)
    assert prices["x/embed"].output_per_million is None
    assert compute_cost("x/embed", 1_000_000, None, prices) == pytest.approx(0.02)
    assert compute_cost("x/embed", 1_000_000, 7, prices) == pytest.approx(0.02)
    assert compute_cost("x/embed", None, None, prices) is None


def test_unknown_model_costs_none():
    assert compute_cost("nope/none", 10, None, load_prices()) is None
    assert compute_cost("nope/none", 10, 10, load_prices()) is None


def test_chat_rows_keep_their_cost():
    prices = load_prices()
    chat = {m: p for m, p in prices.items() if p.output_per_million is not None}
    assert "openai/gpt-6-luna" in chat
    for model, price in chat.items():
        expected = price.input_per_million + price.output_per_million
        assert compute_cost(model, 1_000_000, 1_000_000, prices) == pytest.approx(expected)
    assert compute_cost("openai/gpt-6-luna", 1_000_000, 1_000_000, prices) == pytest.approx(0.60)
    assert compute_cost("openai/gpt-6-luna", 1_000_000, None, prices) is None


def test_embedding_default_is_priced():
    price = load_prices()["openai/text-embedding-3-small"]
    assert price.input_per_million == 0.02
    assert price.output_per_million is None
    date.fromisoformat(price.checked)
