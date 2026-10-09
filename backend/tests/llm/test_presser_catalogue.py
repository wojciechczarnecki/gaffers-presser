import pytest

from app.llm.models import load_model_settings
from app.llm.pricing import load_prices

PRESSER_PRICES = {
    "openai/gpt-6-luna": (0.10, 0.50),
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
    "deepseek/deepseek-v4.1-flash": (0.30, 1.20),
    "google/gemini-3.8-flash": (0.75, 3.75),
    "mistralai/mistral-large-4-0": (0.68, 2.09),
    "openai/gpt-6.1-sol": (2.00, 10.00),
}


@pytest.mark.parametrize("model", PRESSER_PRICES)
def test_presser_models_in_both_catalogues(model):
    assert model in load_model_settings()
    price = load_prices()[model]
    assert (price.input_per_million, price.output_per_million) == PRESSER_PRICES[model]
    assert price.checked == "2026-10-09"
