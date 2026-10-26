"""Bounded retries for reads and replay-safe backend state operations."""

from collections.abc import Callable
from http import HTTPStatus
from time import sleep

import httpx

from agent.execution import Record, check_attempt

MAX_RETRIES = 2
RETRY_STATUSES = {
    HTTPStatus.TOO_MANY_REQUESTS,
    HTTPStatus.INTERNAL_SERVER_ERROR,
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
}


def request(
    client: httpx.Client,
    method: str,
    path: str,
    body: Record | None = None,
    on_retry: Callable[[int], None] | None = None,
) -> httpx.Response:
    """Retry safe transport operations while leaving financial writes untouched."""
    safe = (
        method in {"GET", "PUT"}
        or path.startswith("/api/agent-runs")
        or path.endswith(("/reference-search", "/reference-reads"))
    )
    limit = MAX_RETRIES if safe else 0
    attempt = 0
    while True:
        check_attempt()
        try:
            response = client.request(method, path, json=body)
        except httpx.TransportError:
            if attempt >= limit:
                raise
        else:
            transient = response.status_code in RETRY_STATUSES
            if not transient or attempt >= limit:
                return response
        if on_retry is not None:
            on_retry(attempt + 1)
        sleep(0.25 * 2**attempt)
        attempt += 1
