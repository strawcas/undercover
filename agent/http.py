"""Bounded retries for reads and replay-safe backend state operations."""

from http import HTTPStatus
from time import sleep

import httpx

from agent.execution import AttemptBudget

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
    body: dict[str, object] | None = None,
    *,
    budget: AttemptBudget | None = None,
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
        if budget is not None:
            budget.check()
        try:
            response = client.request(method, path, json=body)
        except httpx.TransportError:
            if attempt >= limit:
                raise
        else:
            transient = response.status_code in RETRY_STATUSES
            if not transient or attempt >= limit:
                response.extensions["undercover_retries"] = attempt
                return response
        sleep(0.25 * 2**attempt)
        attempt += 1
