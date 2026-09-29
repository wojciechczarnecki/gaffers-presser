import time
from collections.abc import Callable

import httpx
from pydantic import SecretStr

GENERATION_URL = "https://openrouter.ai/api/v1/generation"


class HostLookup:
    # The chat response does not carry the serving host (the SDK drops it), and the generation
    # record appears about ten seconds after the call, so a 404 is retried within a wait budget
    # shared by all lookups of one run.
    def __init__(
        self,
        api_key: SecretStr,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        attempts: int = 8,
        delay_seconds: float = 3.0,
        max_total_wait_seconds: float = 60.0,
    ) -> None:
        self._owns_client = client is None
        self._http = client or httpx.Client(timeout=30)
        self._headers = {"Authorization": f"Bearer {api_key.get_secret_value()}"}
        self._sleep = sleep
        self._attempts = attempts
        self._delay_seconds = delay_seconds
        self._wait_left = max_total_wait_seconds

    def __call__(self, generation_id: str) -> str | None:
        for attempt in range(self._attempts):
            try:
                response = self._http.get(
                    GENERATION_URL, params={"id": generation_id}, headers=self._headers
                )
            except httpx.HTTPError:
                return None
            if response.status_code == 200:
                return _provider_name(response)
            if response.status_code != 404:
                return None
            last_attempt = attempt == self._attempts - 1
            if last_attempt or self._wait_left < self._delay_seconds:
                return None
            self._wait_left -= self._delay_seconds
            self._sleep(self._delay_seconds)
        return None

    def close(self) -> None:
        if self._owns_client:
            self._http.close()


def _provider_name(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return None
    name = data.get("provider_name")
    return name if isinstance(name, str) else None


def make_host_lookup(api_key: SecretStr, **kwargs) -> HostLookup:
    return HostLookup(api_key, **kwargs)
