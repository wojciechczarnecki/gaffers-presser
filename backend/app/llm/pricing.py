import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PRICES_PATH = Path(__file__).parent / "prices.toml"


@dataclass(frozen=True)
class Price:
    input_per_million: float
    output_per_million: float | None
    checked: str


def load_prices(path: Path = DEFAULT_PRICES_PATH) -> dict[str, Price]:
    data = tomllib.loads(Path(path).read_text())
    return {
        key: Price(
            input_per_million=entry["input_per_million"],
            output_per_million=entry.get("output_per_million"),
            checked=entry["checked"],
        )
        for key, entry in data.items()
    }


def compute_cost(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    prices: dict[str, Price],
) -> float | None:
    price = prices.get(model)
    if price is None or input_tokens is None:
        return None
    input_cost = input_tokens / 1_000_000 * price.input_per_million
    if price.output_per_million is None:
        return input_cost
    if output_tokens is None:
        return None
    return input_cost + output_tokens / 1_000_000 * price.output_per_million
