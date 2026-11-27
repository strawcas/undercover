"""Configuration shared by both ends of the agent's backend connection."""

import os

ENV_NAME = "UNDERCOVER_API_KEY"
HEADER = "X-API-Key"


def configured_key() -> str:
    """Read the key after the process has loaded its environment."""
    return os.getenv(ENV_NAME, "")


def request_headers() -> dict[str, str]:
    """Authenticate an internal request between the backend and agent service."""
    return {HEADER: configured_key()}
