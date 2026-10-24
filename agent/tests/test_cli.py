import json
import runpy
import socket
from threading import Thread
from urllib.parse import urlsplit

import pytest
import uvicorn

from agent.main import run
from agent.tests.stubs import scripted_model
from backend import batch
from backend.claims import Claims
from backend.local import local_address, wait_started
from backend.main import create_app
from backend.models import DecisionInput
from backend.ownership import DatabaseOwner
from backend.tests.conftest import decision_payload


@pytest.fixture
def terminal(repository, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    monkeypatch.setenv("UNDERCOVER_DB_PATH", str(repository.path))
    monkeypatch.setenv("UNDERCOVER_BACKEND_URL", f"http://127.0.0.1:{port}")
    return repository


def limit_queue(repository, count):
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE claims SET state='decided', decision=? WHERE queue_position > ?",
            (json.dumps({"outcome": "refer"}), count),
        )


def scripted_runner(claim_id, thread_id):
    outcomes = {
        "CLM-1002": ("decline", "0"),
        "CLM-1003": ("approve", "1250.00"),
        "CLM-1004": ("refer", "0"),
    }
    outcome, payout = outcomes.get(claim_id, ("approve", "420.00"))
    return run(claim_id, thread_id, scripted_model(outcome, payout))


def test_terminal_drains_in_order_and_repeat_is_empty(terminal, capsys):
    limit_queue(terminal, 4)
    assert batch.main(scripted_runner) == 0
    output = capsys.readouterr().out
    ids = [f"CLM-{1001 + i}" for i in range(4)]
    assert [output.index(claim_id) for claim_id in ids] == sorted(
        output.index(claim_id) for claim_id in ids
    )
    assert (
        "Attempted: 4 | Decided: 4 | Approved: 2 | Declined: 1 | Referred: 1" in output
    )
    assert "Failed runs: 0 | Awaiting sign-off: 1" in output
    assert "Explanation:" in output
    assert "Policy sections: 3, 10, 11" in output
    assert "Waiting for sign-off" in output
    claims = Claims(terminal)
    assert claims.detail("CLM-1001")["payment"]["amount"] == "420.00"
    assert claims.detail("CLM-1003")["payment"] is None
    events = claims.events("CLM-1001")
    assert batch.main(scripted_runner) == 0
    assert "Attempted: 0 | Decided: 0" in capsys.readouterr().out
    assert claims.events("CLM-1001") == events


def test_failures_before_and_after_saved_decisions(terminal, capsys):
    limit_queue(terminal, 3)

    def runner(claim_id, thread_id):
        if claim_id == "CLM-1001":
            raise RuntimeError("Sensitive provider message")
        reply = scripted_runner(claim_id, thread_id)
        if claim_id == "CLM-1002":
            raise RuntimeError("After decision")
        return reply

    assert batch.main(runner) == 1
    output = capsys.readouterr().out
    assert "CLM-1001 FAILED" in output
    assert "CLM-1002 DECLINE" in output
    assert "CLM-1003 APPROVE" in output
    assert "Attempted: 3 | Decided: 2" in output
    assert "Failed runs: 2" in output
    assert "Sensitive" not in output
    assert Claims(terminal).detail("CLM-1001")["status"] == "waiting"


def test_ctrl_c_prints_partial_totals_and_retries_next_session(terminal, capsys):
    limit_queue(terminal, 3)

    def runner(claim_id, thread_id):
        if claim_id == "CLM-1002":
            raise KeyboardInterrupt
        return scripted_runner(claim_id, thread_id)

    assert batch.main(runner) == 130
    captured = capsys.readouterr()
    assert "Interrupted" in captured.err
    assert "Attempted: 2 | Decided: 1" in captured.out
    claims = Claims(terminal)
    assert claims.events("CLM-1003") == []
    first_events = claims.events("CLM-1001")
    assert batch.main(scripted_runner) == 0
    assert "Attempted: 2 | Decided: 2" in capsys.readouterr().out
    assert claims.events("CLM-1001") == first_events


def test_interruption_after_decision_is_counted_and_preserved(terminal, capsys):
    limit_queue(terminal, 1)
    claims = Claims(terminal)

    def runner(claim_id, thread_id):
        claims.decide(
            claim_id, DecisionInput.model_validate(decision_payload(thread_id))
        )
        raise KeyboardInterrupt

    assert batch.main(runner) == 130
    output = capsys.readouterr().out
    assert "Attempted: 1 | Decided: 1 | Approved: 1" in output
    assert "Failed runs: 1 | Awaiting sign-off: 1" in output
    assert claims.detail("CLM-1001")["decision"] is not None


def test_package_entry_point_uses_queue_command(terminal, monkeypatch, capsys):
    limit_queue(terminal, 1)
    original = batch.main
    monkeypatch.setattr(batch, "main", lambda: original(scripted_runner))
    with pytest.raises(SystemExit) as ended:
        runpy.run_module("agent", run_name="__main__")
    assert ended.value.code == 0
    assert "CLM-1001 APPROVE" in capsys.readouterr().out


def test_missing_key_is_a_setup_error_but_empty_queue_needs_no_key(
    terminal, monkeypatch, capsys
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    assert batch.main() == 1
    captured = capsys.readouterr()
    assert "Set OPENROUTER_API_KEY" in captured.err
    assert "Attempted: 0" in captured.out
    assert Claims(terminal).events("CLM-1001") == []
    limit_queue(terminal, 0)
    assert batch.main() == 0
    assert "Attempted: 0" in capsys.readouterr().out


def test_occupied_address_leaves_database_unchanged(terminal, monkeypatch, capsys):
    parsed = urlsplit(batch.backend_url())
    assert parsed.port is not None
    before = terminal.path.read_bytes()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", parsed.port))
        occupied.listen()
        assert batch.main(scripted_runner) == 1
    assert "Queue processing failed" in capsys.readouterr().err
    assert terminal.path.read_bytes() == before


def test_active_database_owner_is_not_taken_over(terminal, capsys):
    before = terminal.path.read_bytes()
    with DatabaseOwner(terminal.path):
        assert batch.main(scripted_runner) == 1
    assert "database ownership" in capsys.readouterr().err
    assert terminal.path.read_bytes() == before


@pytest.mark.parametrize(
    "url", ["https://localhost:8000", "http://example.com", "http://localhost/path"]
)
def test_only_local_backend_addresses_are_accepted(url):
    with pytest.raises(ValueError, match="local HTTP"):
        local_address(url)
    assert local_address("http://localhost:8765") == ("localhost", 8765)


def test_failed_server_start_is_reported(terminal):
    server = uvicorn.Server(uvicorn.Config(create_app(terminal, auto_run=False)))
    thread = Thread(target=lambda: None)
    thread.start()
    thread.join()
    with pytest.raises(RuntimeError, match="could not start"):
        wait_started(server, thread)
