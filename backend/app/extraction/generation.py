import time
from collections.abc import Callable

import httpx
from pydantic import SecretStr

GENERATION_URL = "https://openrouter.ai/api/v1/generation"


def make_host_lookup(
    api_key: SecretStr,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 8,
    delay_seconds: float = 3.0,
) -> Callable[[str], str | None]:
    """Looks up the host that served a generation.

    The response of the chat call does not carry the host (the SDK drops it), and the
    generation record appears about ten seconds after the call, so a 404 is retried.
    """
    http = client or httpx.Client(timeout=30)
    headers = {"Authorization": f"Bearer {api_key.get_secret_value()}"}

    def lookup(generation_id: str) -> str | None:
        for attempt in range(attempts):
            try:
                response = http.get(GENERATION_URL, params={"id": generation_id}, headers=headers)
            except httpx.HTTPError:
                return None
            if response.status_code == 200:
                data = response.json().get("data") or {}
                return data.get("provider_name")
            if response.status_code != 404:
                return None
            if attempt < attempts - 1:
                sleep(delay_seconds)
        return None

    return lookup
