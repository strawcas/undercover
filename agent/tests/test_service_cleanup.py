from unittest.mock import Mock

import pytest

from agent import local_process
from backend.diagnostics import preserve_primary


def test_process_cleanup_still_terminates_and_closes_after_join_failure(caplog):
    process, stopped = Mock(), Mock()
    primary = ValueError("Original operation")
    process.join.side_effect = TypeError("PRIVATE-JOIN")
    process.terminate.side_effect = RuntimeError("PRIVATE-TERMINATE")
    process.close.side_effect = OSError("PRIVATE-CLOSE")

    def fail_and_cleanup():
        try:
            raise primary
        finally:
            with preserve_primary("Stop test service"):
                local_process.stop_process(process, stopped)

    with pytest.raises(ValueError, match="Original operation") as error:
        fail_and_cleanup()
    assert error.value is primary
    process.terminate.assert_called_once()
    process.close.assert_called_once()
    assert "Stop test service also failed" in str(primary.__notes__)
    assert "PRIVATE-" not in caplog.text


def test_cleanup_only_error_remains_visible_and_process_closes():
    process, stopped = Mock(), Mock()
    process.is_alive.return_value = False
    process.join.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        local_process.stop_process(process, stopped)
    process.close.assert_called_once()


def test_unrelated_agent_health_cannot_be_attached(monkeypatch):
    response = Mock(is_success=True, headers={"X-UnderCover-Database": "same"})
    monkeypatch.setattr(local_process.httpx, "get", Mock(return_value=response))
    with pytest.raises(RuntimeError, match="another service or database"):
        local_process.available(
            local_process.Settings("agent", "http://127.0.0.1:8002"), "same"
        )
