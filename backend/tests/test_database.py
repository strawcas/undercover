import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.pool import QueuePool

from backend import database as storage
from backend.database import engine, location, seed
from backend.main import app
from backend.tests.auth_support import AGENT_HEADERS

SUPABASE = "postgresql://postgres.ref:secret@pooler.example.com:5432/db"


def test_postgres_is_used_when_database_url_is_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", SUPABASE)
    db = engine()
    assert db.url.drivername == "postgresql+psycopg"
    assert db.url.query["options"] == "-c search_path=undercover"
    assert isinstance(db.pool, QueuePool)
    assert (db.pool.size(), db.pool._max_overflow, db.pool._pre_ping) == (4, 0, True)
    assert location(db) == "the undercover schema on Postgres"


def test_the_settings_in_database_url_are_kept(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"{SUPABASE}?sslmode=require")
    query = engine().url.query
    assert (query["sslmode"], query["options"]) == (
        "require",
        "-c search_path=undercover",
    )


def test_backend_connections_identify_the_application_process(monkeypatch):
    opened = []
    create = storage.create_engine

    def capture(url, **kwargs):
        opened.append(kwargs)
        return create(url, **kwargs)

    monkeypatch.setattr(storage, "create_engine", capture)
    monkeypatch.setenv("DATABASE_URL", f"{SUPABASE}?application_name=identity-test")
    engine()
    assert opened[0]["connect_args"]["application_name"].startswith(
        "undercover-backend-"
    )


def test_sqlite_is_used_otherwise(database):
    db = engine()
    assert db.url.drivername == "sqlite"
    assert db.url.database == str(database)
    assert location(db) == "undercover.db"


@pytest.mark.parametrize("empty", [False, True])
def test_startup_seeds_a_missing_or_empty_database(database, empty):
    database.unlink()
    if empty:
        database.touch()
    with TestClient(app) as client:
        assert len(client.get("/api/claims", headers=AGENT_HEADERS).json()) == 26


def test_seed_drops_the_retired_table_before_parent_tables():
    with engine().begin() as db:
        db.execute(text("CREATE TABLE todos (run_id TEXT REFERENCES runs(run_id))"))
    seed(engine())
    assert "todos" not in inspect(engine()).get_table_names()
