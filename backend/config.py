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
