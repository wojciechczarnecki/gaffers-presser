import copy
import json
from pathlib import Path
from typing import Any

PAYLOADS_DIR = Path(__file__).resolve().parent


def load(name: str) -> Any:
    with open(PAYLOADS_DIR / f"{name}.json", encoding="utf-8") as f:
        return copy.deepcopy(json.load(f))
