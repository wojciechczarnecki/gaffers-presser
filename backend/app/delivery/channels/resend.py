import logging

import httpx

from app.delivery.channels.base import (
    ChannelPayloadError,
    ChannelRateLimitedError,
    ChannelRejectedError,
    ChannelUnavailableError,
    Message,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


class ResendChannel:
    name = "resend"

    def __init__(
        self,
        api_key: str,
        email_from: str,
        email_to: str,
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://api.resend.com/",
        timeout: float = 10.0,
    ) -> None:
        self._from = email_from
        self._to = email_to
        self._client = httpx.Client(
            transport=transport,
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def close(self) -> None:
        self._client.close()

    def send(self, message: Message) -> str | None:
        payload = {
            "from": self._from,
            "to": [self._to],
            "subject": message.title,
            "text": message.text,
        }
        if message.html is not None:
            payload["html"] = message.html
        try:
            response = self._client.post("emails", json=payload)
        except httpx.TransportError:
            raise ChannelUnavailableError("resend: request failed") from None

        status = response.status_code
        if status == 429:
            raise ChannelRateLimitedError("resend: rate limited", _retry_after(response))
        if status >= 500:
            raise ChannelUnavailableError(f"resend: server error {status}", status)
        if not 200 <= status < 300:
            raise ChannelRejectedError(f"resend: rejected with status {status}", status)
        try:
            body = response.json()
        except ValueError:
            raise ChannelPayloadError("resend: malformed response body", status) from None
        message_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise ChannelPayloadError("resend: response carries no message id", status)
        return message_id
