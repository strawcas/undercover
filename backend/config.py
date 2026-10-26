"""Shared local settings for the backend and terminal commands."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def database_path() -> Path:
    """Resolve the same database for every local execution mode."""
    load_dotenv(ROOT / ".env")
    return Path(os.getenv("UNDERCOVER_DB_PATH", ROOT / "undercover.db")).resolve()


def backend_url() -> str:
    """Read the local HTTP address used by agent tools."""
    load_dotenv(ROOT / ".env")
    return os.getenv("UNDERCOVER_BACKEND_URL", "http://localhost:8000").rstrip("/")


def run_scope(max_claims: int | None = None, claim_id: str | None = None) -> dict:
    """Resolve explicit CLI scope before the optional startup cap."""
    if claim_id is not None:
        if not claim_id.strip():
            raise ValueError("A named claim ID must not be empty.")
        return {"mode": "claim", "claim_id": claim_id.strip()}
    raw = (
        str(max_claims)
        if max_claims is not None
        else os.getenv("UNDERCOVER_MAX_CLAIMS", "")
    )
    if not raw.strip():
        return {"mode": "all"}
    try:
        limit = int(raw)
        if limit <= 0:
            raise ValueError
    except ValueError as failure:
        raise ValueError(
            "UNDERCOVER_MAX_CLAIMS / --max-claims must be a positive integer."
        ) from failure
    return {"mode": "limited", "max_claims": limit}
