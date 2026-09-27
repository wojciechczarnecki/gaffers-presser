import copy
import gzip
import json
from pathlib import Path
from typing import Any

PAYLOADS_DIR = Path(__file__).resolve().parent
_cache: dict[str, Any] = {}


def load(name: str) -> Any:
    if name not in _cache:
        with gzip.open(PAYLOADS_DIR / f"{name}.json.gz", "rt", encoding="utf-8") as f:
            _cache[name] = json.load(f)
    return copy.deepcopy(_cache[name])
