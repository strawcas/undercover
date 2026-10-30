from unittest.mock import Mock

import pytest

from agent import checkpoints


def test_postgres_saver_is_synchronous_and_pool_is_bounded(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:stub@localhost/product")
    pool = Mock()
    factory = Mock(return_value=pool)
    saver = Mock()
    monkeypatch.setattr(checkpoints, "ConnectionPool", factory)
    monkeypatch.setattr(checkpoints, "PostgresSaver", Mock(return_value=saver))
    with checkpoints.checkpointer() as selected:
        assert selected is saver
    settings = factory.call_args.kwargs
    assert settings["max_size"] == 4
    assert settings["kwargs"]["autocommit"] is True
    assert settings["kwargs"]["prepare_threshold"] == 0
    assert settings["kwargs"]["options"] == "-c search_path=undercover"
    saver.setup.assert_called_once()
    pool.close.assert_called_once()


def test_checkpointer_cleanup_preserves_primary_failure(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("UNDERCOVER_DB_PATH", str(tmp_path / "claims.db"))
    connection = Mock()
    connection.close.side_effect = TypeError("PRIVATE-CLEANUP")
    monkeypatch.setattr(checkpoints.sqlite3, "connect", Mock(return_value=connection))
    monkeypatch.setattr(checkpoints, "SqliteSaver", Mock())
    primary = ValueError("PRIVATE-OPERATION")
    with (
        pytest.raises(ValueError, match="PRIVATE-OPERATION") as error,
        checkpoints.checkpointer(),
    ):
        raise primary
    assert error.value is primary
    assert "Close claim checkpointer also failed" in str(primary.__notes__)
    assert "PRIVATE-" not in caplog.text


def test_checkpointer_cleanup_failure_alone_is_visible(monkeypatch, tmp_path):
    monkeypatch.setenv("UNDERCOVER_DB_PATH", str(tmp_path / "claims.db"))
    connection = Mock()
    connection.close.side_effect = TypeError("Cleanup failed")
    monkeypatch.setattr(checkpoints.sqlite3, "connect", Mock(return_value=connection))
    monkeypatch.setattr(checkpoints, "SqliteSaver", Mock())
    with pytest.raises(TypeError, match="Cleanup failed"), checkpoints.checkpointer():
        pass


def test_checkpoint_connection_rejects_active_reset():
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = {
        "pg_try_advisory_lock_shared": False
    }
    with pytest.raises(RuntimeError, match="being rebuilt"):
        checkpoints.configure(connection)
