from unittest.mock import Mock

import pytest
from sqlalchemy.pool import QueuePool
from sqlalchemy.schema import CreateTable

from backend import config
from backend.database import metadata
from backend.ownership import PostgresOwner
from backend.storage import Store


def test_environment_selection_and_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config, "load_dotenv", Mock(side_effect=AssertionError("No implicit dotenv"))
    )
    monkeypatch.setenv("UNDERCOVER_DB_PATH", str(tmp_path / "claims.db"))
    assert config.database_target() == tmp_path / "claims.db"
    first = "postgresql://postgres:private@localhost:5432/product"
    second = "postgresql://postgres:rotated@localhost:5432/product"
    monkeypatch.setenv("DATABASE_URL", first)
    assert config.database_target() == first
    assert config.database_identity(first) == config.database_identity(second)
    assert config.database_identity(first) != config.database_identity(first + "2")
    pooler = (
        "postgresql://postgres.project_a:private@shared.pooler.example:5432/postgres"
    )
    assert config.database_identity(pooler) != config.database_identity(
        pooler.replace("project_a", "project_b")
    )
    assert config.database_identity(pooler) == config.database_identity(
        pooler.replace("private", "rotated")
    )
    assert config.database_identity(pooler) == config.database_identity(
        pooler.replace("postgresql://", "postgresql+psycopg://")
    )
    for invalid in ["", "sqlite:///other.db"]:
        monkeypatch.setenv("DATABASE_URL", invalid)
        with pytest.raises(ValueError, match="session pooler"):
            config.database_target()


def test_explicit_store_and_parameter_bindings(repository, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unreachable/db")
    assert not repository.postgres
    with repository.connect() as connection:
        with pytest.raises(ValueError, match="parameter count"):
            connection.execute("SELECT ?", ())
        assert (
            connection.execute("SELECT ? AS value", ("quote'; payload",)).fetchone()[
                "value"
            ]
            == "quote'; payload"
        )


def test_postgres_pool_and_schema_ddl_without_network():
    store = Store("postgresql://postgres:private@localhost:5432/product")
    try:
        assert isinstance(store.engine.pool, QueuePool)
        assert store.engine.pool.size() == 4
        assert store.engine.pool._max_overflow == 0
        assert store.engine.hide_parameters
        for definition in metadata.sorted_tables:
            ddl = str(CreateTable(definition).compile(dialect=store.engine.dialect))
            assert "CREATE TABLE" in ddl
        assert "SERIAL" in str(
            CreateTable(metadata.tables["events"]).compile(dialect=store.engine.dialect)
        )
    finally:
        store.dispose()


@pytest.mark.parametrize("maintenance", [False, True])
def test_postgres_ownership_and_release(maintenance):
    engine = Mock()
    connection = engine.connect.return_value
    connection.scalar.return_value = True
    with PostgresOwner(engine, maintenance=maintenance):
        assert connection.scalar.call_count == (1 if maintenance else 2)
    connection.close.assert_called_once()
    assert "pg_advisory_unlock_all" in str(connection.execute.call_args.args[0])


def test_postgres_ownership_failure_releases_session():
    engine = Mock()
    connection = engine.connect.return_value
    connection.scalar.return_value = False
    with pytest.raises(RuntimeError, match="already in use"):
        PostgresOwner(engine).acquire()
    connection.close.assert_called_once()


def test_failed_postgres_acquisition_preserves_primary_and_closes_on_unlock_failure(
    caplog,
):
    engine = Mock()
    connection = engine.connect.return_value
    connection.scalar.return_value = False
    connection.execute.side_effect = ValueError("PRIVATE-UNLOCK")
    connection.close.side_effect = TypeError("PRIVATE-CLOSE")
    with pytest.raises(RuntimeError, match="already in use") as error:
        PostgresOwner(engine).acquire()
    connection.close.assert_called_once()
    assert "Release failed database ownership also failed" in str(error.value.__notes__)
    assert "PRIVATE-" not in caplog.text


def test_postgres_owner_exit_preserves_operation_failure(caplog):
    engine = Mock()
    engine.connect.return_value.scalar.return_value = True
    engine.connect.return_value.execute.side_effect = RuntimeError("PRIVATE-CLEANUP")
    primary = ValueError("Original operation")
    with (
        pytest.raises(ValueError, match="Original operation") as error,
        PostgresOwner(engine),
    ):
        raise primary
    assert error.value is primary
    assert "Release Postgres database ownership also failed" in str(primary.__notes__)
    assert "PRIVATE-" not in caplog.text


def test_backfill_order_of_older_harness_records(repository):
    with repository.transaction() as connection:
        connection.execute("ALTER TABLE agent_runs DROP COLUMN position")
        connection.execute("INSERT INTO agent_runs (id,body) VALUES ('z','{}')")
        connection.execute("INSERT INTO agent_runs (id,body) VALUES ('a','{}')")
    repository.initialize()
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id,position FROM agent_runs ORDER BY position"
        ).fetchall()
    assert [(row["id"], row["position"]) for row in rows] == [("z", 1), ("a", 2)]
