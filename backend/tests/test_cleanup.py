from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from agent.execution import AgentRunError
from backend import batch, reset_db, worker
from backend.claims import Claims
from backend.diagnostics import diagnose, preserve_primary
from backend.main import create_app
from backend.ownership import DatabaseOwner
from backend.runs import Runs
from backend.storage import Store


def test_startup_failure_skips_run_recovery_and_releases_ownership(
    repository, monkeypatch
):
    primary = ValueError("Invalid source data")
    recovery = Mock(side_effect=RuntimeError("Shutdown recovery must not run"))
    monkeypatch.setattr(repository, "initialize", Mock(side_effect=primary))
    monkeypatch.setattr(Runs, "recover", recovery)
    with (
        pytest.raises(ValueError, match="Invalid source data") as error,
        TestClient(create_app(repository)),
    ):
        pass
    assert error.value is primary
    recovery.assert_not_called()
    with DatabaseOwner(repository.path):
        pass


def test_transaction_rollback_failure_preserves_the_write_error(
    tmp_path, monkeypatch, caplog
):
    connection = Mock()
    connection.rollback.side_effect = RuntimeError("PRIVATE-ROLLBACK-DETAIL")
    store = Store(tmp_path / "unused.db")

    @contextmanager
    def connected():
        yield connection

    monkeypatch.setattr(store, "connect", connected)
    primary = ValueError("Original write failure")
    with (
        pytest.raises(ValueError, match="Original write failure") as error,
        store.transaction(),
    ):
        raise primary
    assert error.value is primary
    assert "Roll back database transaction also failed" in str(primary.__notes__)
    connection.commit.assert_not_called()
    assert "PRIVATE-" not in caplog.text


def test_rebuild_cleanup_preserves_validation_failure(tmp_path, monkeypatch):
    primary = ValueError("Invalid seed")
    monkeypatch.setattr(Store, "initialize", Mock(side_effect=primary))
    monkeypatch.setattr(reset_db.Path, "unlink", Mock(side_effect=OSError("Cleanup")))
    with pytest.raises(ValueError, match="Invalid seed") as error:
        reset_db.rebuild(tmp_path / "selected.db")
    assert error.value is primary
    assert "Remove temporary rebuild database also failed" in str(primary.__notes__)


@pytest.mark.parametrize("interrupt", [False, True])
def test_cli_reconciliation_preserves_the_original_exit_code(
    repository, monkeypatch, capsys, interrupt
):
    @contextmanager
    def local(*_args):
        yield Claims(repository)

    primary = KeyboardInterrupt() if interrupt else AgentRunError("Original failure")
    monkeypatch.setattr(batch, "agent_backend", local)
    monkeypatch.setattr(batch, "run_queue", Mock(side_effect=primary))
    monkeypatch.setattr(
        batch, "reconcile_execution", Mock(side_effect=RuntimeError("Cleanup failure"))
    )
    assert batch.main() == (130 if interrupt else 1)
    captured = capsys.readouterr()
    assert ("Interrupted" if interrupt else "Original failure") in captured.err
    assert "Cleanup failure" not in captured.err


def test_cleanup_failure_is_not_silenced_without_a_primary_error():
    with (
        pytest.raises(RuntimeError, match="Cleanup failed"),
        preserve_primary("Test cleanup"),
    ):
        raise RuntimeError("Cleanup failed")


def test_diagnostics_include_safe_chained_locations_without_messages(caplog):
    try:
        try:
            raise ValueError("PRIVATE-CUSTOMER-DATA")
        except ValueError as cause:
            raise TypeError("PRIVATE-PROVIDER-KEY") from cause
    except TypeError as error:
        reference = diagnose("Test safe diagnostics", error)
    assert reference in caplog.text
    assert "ValueError" in caplog.text
    assert "TypeError" in caplog.text
    assert "test_cleanup.py" in caplog.text
    assert "PRIVATE-" not in caplog.text


def test_direct_assessment_cleanup_keeps_the_original_cause(repository, monkeypatch):
    claims = Claims(repository)
    thread = claims.start("CLM-1001")
    primary = TypeError("PRIVATE-HELPER-DETAIL")

    def helper(_claim, _thread, *, budget):
        raise primary

    monkeypatch.setattr(claims, "finish", Mock(side_effect=RuntimeError("Cleanup")))
    with pytest.raises(AgentRunError, match="TypeError.*Diagnostic") as error:
        worker.execute(claims, "CLM-1001", thread, helper)
    assert error.value.__cause__ is primary
    assert "Assessment cleanup also failed" in str(primary.__notes__)
