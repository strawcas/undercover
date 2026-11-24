"""Shared backend transport; claim and improver operations stay in separate clients."""

import os
from typing import Any

import httpx

from agent.credentials import app_headers

BACKEND_URL = os.getenv("UNDERCOVER_API", "http://127.0.0.1:8000")


class Refused(Exception):
    """The backend refused an operation with a status code and explanation."""

    def __init__(self, status_code: int, detail: str | list[dict[str, Any]]) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code, self.detail = status_code, detail


def client() -> httpx.Client:
    """Create a client with the shared backend address and finish timeout."""
    return httpx.Client(base_url=f"{BACKEND_URL}/api", timeout=30)


def call(backend: httpx.Client, method: str, path: str, **request: Any) -> Any:
    """Send a typed client's request and preserve backend refusals."""
    response = backend.request(method, path, headers=app_headers(), **request)
    if response.is_error:
        try:
            detail = response.json()["detail"]
        except (ValueError, TypeError, KeyError):
            detail = response.reason_phrase
        raise Refused(response.status_code, detail)
    return response.json()
