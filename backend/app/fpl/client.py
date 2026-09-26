import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

USER_AGENT = "gaffers-presser/0.1 (+https://github.com/wojciechczarnecki/gaffers-presser)"


class FplClient:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://fantasy.premierleague.com/api/",
        min_interval: float = 0.5,
        max_attempts: int = 5,
        backoff_base: float = 1.0,
        timeout: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = httpx.Client(
            transport=transport,
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
        self._min_interval = min_interval
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            elapsed = self._monotonic() - self._last_request_at
            remaining = self._min_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._monotonic()

    def _get_json(self, endpoint_template: str, path: str, params: dict | None = None) -> Any:
        self._throttle()
        response = self._client.get(path, params=params)
        return response.json()
