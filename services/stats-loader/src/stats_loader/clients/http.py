"""HTTP client for the Sleeper API.

Concrete I/O. The only place HTTP happens in this service.

Retry policy per `docs/references/sleeper-api.md`:
- 429 -> exponential backoff, up to 3 attempts.
- 5xx -> exponential backoff, up to 3 attempts.
- 404 -> no retry; raises ``NotFoundError``.
- Other 4xx -> no retry; raises ``HttpError`` with body in the message.

This module returns *decoded JSON*. Shape validation lives in
``providers/sleeper.py``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Final, Protocol

import httpx

log = logging.getLogger(__name__)

MAX_ATTEMPTS: Final[int] = 3
INITIAL_BACKOFF_SECONDS: Final[float] = 1.0
BACKOFF_MULTIPLIER: Final[float] = 2.0
REQUEST_TIMEOUT_SECONDS: Final[float] = 30.0


class HttpError(RuntimeError):
    """Non-retryable HTTP failure from Sleeper. Aborts the run."""


class NotFoundError(HttpError):
    """Sleeper returned 404. The caller decides whether that's fatal."""


class HttpClient(Protocol):
    """Protocol the core layer accepts.

    Keeps ``core`` testable without httpx.
    """

    def get_json(self, path: str) -> object: ...


class SleeperHttpClient:
    """httpx-backed implementation of HttpClient with bounded retries."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        sleep: Sleeper = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._sleep = sleep

    def get_json(self, path: str) -> object:
        url = f"{self._base_url}{path}"
        backoff = INITIAL_BACKOFF_SECONDS

        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self._client.get(url)
            status = response.status_code

            if 200 <= status < 300:
                try:
                    return response.json()
                except ValueError as exc:
                    raise HttpError(f"{url}: response body not parseable as JSON") from exc

            if status == 404:
                raise NotFoundError(f"{url}: 404")

            retryable = status == 429 or 500 <= status < 600
            if not retryable:
                raise HttpError(
                    f"{url}: {status} (non-retryable) body={_short_body(response)}"
                )

            if attempt == MAX_ATTEMPTS:
                raise HttpError(
                    f"{url}: {status} after {MAX_ATTEMPTS} attempts; giving up."
                )

            log.warning(
                "Sleeper %s returned %d; retrying in %.1fs (attempt %d/%d)",
                url,
                status,
                backoff,
                attempt,
                MAX_ATTEMPTS,
            )
            self._sleep(backoff)
            backoff *= BACKOFF_MULTIPLIER

        # Unreachable: the loop above either returns or raises.
        raise HttpError(f"{url}: unreachable retry loop exit")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SleeperHttpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


Sleeper = Callable[[float], None]


def _short_body(response: httpx.Response) -> str:
    body = response.text
    return body[:200] + ("..." if len(body) > 200 else "")
