"""Shared local settings for the backend and terminal commands."""

import os
from hashlib import sha256
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def database_identity(path: Path) -> str:
    """Identify the configured local database without exposing its path over HTTP."""
    return sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()


def database_path() -> Path:
    """Resolve the same database for every local execution mode."""
    load_dotenv(ROOT / ".env")
    return Path(os.getenv("UNDERCOVER_DB_PATH", ROOT / "undercover.db")).resolve()


def backend_url() -> str:
    """Read the local HTTP address used by agent tools."""
    load_dotenv(ROOT / ".env")
    return os.getenv("UNDERCOVER_BACKEND_URL", "http://localhost:8000").rstrip("/")


def run_scope(max_claims: int | None = None, claim_id: str | None = None) -> dict:
    """Resolve only the scope explicitly selected by the command."""
    if claim_id is not None:
        if not claim_id.strip():
            raise ValueError("A named claim ID must not be empty.")
        return {"mode": "claim", "claim_id": claim_id.strip()}
    if max_claims is None:
        return {"mode": "all"}
    if max_claims <= 0:
        raise ValueError("--cap must be a positive integer.")
    return {"mode": "limited", "max_claims": max_claims}
