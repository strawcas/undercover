"""Shared local settings for the backend and terminal commands."""

import os
from hashlib import sha256
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def agent_owner_path(path: Path) -> Path:
    return path.with_name(path.name + ".agent.db")


def checkpoint_owner_path(path: Path) -> Path:
    return path.with_name(path.name + ".checkpointer.db")


def database_identity(path: Path | str) -> str:
    """Identify the configured local database without exposing its path over HTTP."""
    if isinstance(path, str):
        url = make_url(path.replace("postgres://", "postgresql://", 1))
        identity = str((url.host, url.port or 5432, url.database, "undercover"))
    else:
        identity = os.path.normcase(str(path.resolve()))
    return sha256(identity.encode()).hexdigest()


def load_environment() -> None:
    """Load developer settings only at an explicit terminal entry point."""
    load_dotenv(ROOT / ".env")


def database_target() -> Path | str:
    """Use Postgres only when explicitly present in this process environment."""
    if "DATABASE_URL" not in os.environ:
        return database_path()
    value = os.environ["DATABASE_URL"]
    if not value.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        raise ValueError("DATABASE_URL must be a Postgres session pooler URL.")
    return value


def database_path() -> Path:
    """Resolve the same database for every local execution mode."""
    return Path(os.getenv("UNDERCOVER_DB_PATH", ROOT / "undercover.db")).resolve()


def backend_url() -> str:
    """Read the local HTTP address used by agent tools."""
    return os.getenv("UNDERCOVER_BACKEND_URL", "http://localhost:8000").rstrip("/")


def agent_url() -> str:
    """Read the agent service address used by notifications and commands."""
    return os.getenv("UNDERCOVER_AGENT", "http://127.0.0.1:8002").rstrip("/")


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
