"""The agent's local credential setting and headers for its backend calls."""

import os

API_KEY = "UNDERCOVER_API_KEY"


def configured_key() -> str:
    """Read the agent's key after local setup or Runtime initialization."""
    return os.getenv(API_KEY, "")


def app_headers() -> dict[str, str]:
    """Authenticate a direct call from the agent to the backend."""
    return {"X-API-Key": configured_key()}
