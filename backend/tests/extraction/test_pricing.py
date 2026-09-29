from datetime import date

import pytest

from app.extraction.pricing import Price, compute_cost, load_prices
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
