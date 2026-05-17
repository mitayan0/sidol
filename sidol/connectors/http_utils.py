"""Shared HTTP helpers for connectors."""

from __future__ import annotations

import time
from typing import Any

import httpx

_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 503})
_MAX_RETRIES: int = 3
_BASE_DELAY: float = 1.0


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    max_retries: int = _MAX_RETRIES,
    **kwargs: Any,
) -> httpx.Response:
    """Send an HTTP request, retrying on 429 and 503 with exponential backoff.

    Respects the ``Retry-After`` response header when present.
    """
    for attempt in range(max_retries):
        response = client.request(method, url, **kwargs)
        if response.status_code not in _RETRYABLE_STATUS:
            return response
        if attempt == max_retries - 1:
            # Final attempt exhausted — return the error response to the caller.
            return response
        time.sleep(_retry_delay(response, attempt))
    return response  # unreachable; satisfies type checker


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Return seconds to wait before the next retry attempt.

    Uses the ``Retry-After`` header value when present; otherwise falls back
    to exponential backoff (1 s, 2 s, 4 s, …).
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return _BASE_DELAY * (2.0 ** attempt)
