import json
import runpy
import socket
from threading import Thread
from urllib.parse import urlsplit

import pytest
import uvicorn

from agent import main as agent_main
from agent.main import run
from agent.tests.stubs import scripted_model
from backend import batch
from backend.claims import Claims
from backend.local import local_address, wait_started
from backend.main import create_app
from backend.models import DecisionInput
from backend.ownership import DatabaseOwner
from backend.reset_db import rebuild
from backend.tests.conftest import decide_fixture, decision_payload


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


def test_full_rebuild_drain_repeat_and_rebuild(terminal, capsys):
    count = rebuild(terminal.path)
    claims = Claims(terminal)
    ordered = [row["claim_id"] for row in claims.queue()]
    assert batch.main(scripted_runner) == 0
    output = capsys.readouterr().out
    printed = [
        line.split()[0] for line in output.splitlines() if line.startswith("CLM-")
    ]
    assert printed == ordered
    assert f"Attempted: {count} | Decided: {count}" in output
    assert "Failed runs: 0 | Awaiting sign-off: 1" in output
    assert all(row["outcome"] is not None for row in claims.queue())
    assert sum(row["payment_status"] == "paid" for row in claims.queue()) == count - 3
    assert batch.main(scripted_runner) == 0
    assert "Attempted: 0 | Decided: 0" in capsys.readouterr().out
    assert rebuild(terminal.path) == count
    assert [row["claim_id"] for row in claims.queue()] == ordered
    assert {row["status"] for row in claims.queue()} == {"waiting"}
    assert all(claims.events(claim_id) == [] for claim_id in ordered)


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
    assert "Failed runs: 3" in output
    assert "Sensitive" not in output
    assert Claims(terminal).detail("CLM-1001")["status"] == "handler_review"


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
        decide_fixture(
            claims, claim_id, DecisionInput.model_validate(decision_payload(thread_id))
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
    monkeypatch.setattr(batch, "main", lambda **_kwargs: original(scripted_runner))
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


@pytest.mark.parametrize("name", ["qwen3:8b", "llama3.1:8b"])
def test_terminal_uses_selected_ollama_model_without_openrouter_key(
    terminal, monkeypatch, capsys, name
):
    limit_queue(terminal, 1)
    monkeypatch.setenv("MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("MODEL_NAME", name)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    seen = []

    def configured_model(**kwargs):
        seen.append(kwargs["model"])
        return scripted_model()

    monkeypatch.setattr(agent_main, "ChatOllama", configured_model)
    assert batch.main() == 0
    assert seen == [name]
    assert "Attempted: 1 | Decided: 1 | Approved: 1" in capsys.readouterr().out
    assert Claims(terminal).detail("CLM-1001")["payment"]["amount"] == "420.00"


@pytest.mark.parametrize(
    "settings",
    [("ollama", "", "Set MODEL_NAME"), ("unknown", "test", "MODEL_PROVIDER")],
)
def test_invalid_provider_configuration_leaves_queue_unattempted(
    terminal, monkeypatch, capsys, settings
):
    provider, name, message = settings
    monkeypatch.setenv("MODEL_PROVIDER", provider)
    monkeypatch.setenv("MODEL_NAME", name)
    assert batch.main() == 1
    captured = capsys.readouterr()
    assert message in captured.err
    assert "Attempted: 0" in captured.out
    assert Claims(terminal).events("CLM-1001") == []


def test_invalid_ollama_context_leaves_queue_unattempted(terminal, monkeypatch, capsys):
    monkeypatch.setenv("MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("MODEL_NAME", "gemma4:e4b")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "invalid")
    assert batch.main() == 1
    captured = capsys.readouterr()
    assert "OLLAMA_NUM_CTX must be a positive integer" in captured.err
    assert "Attempted: 0" in captured.out
    assert Claims(terminal).events("CLM-1001") == []


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


def test_terminal_distinct_cap_and_named_claim(terminal, monkeypatch, capsys):
    monkeypatch.setenv("UNDERCOVER_MAX_CLAIMS", "1")
    assert batch.main(scripted_runner, argv=["--max-claims", "5"]) == 0
    output = capsys.readouterr().out
    assert "Attempted: 5 | Decided: 5" in output
    assert "Assessment attempts: 5" in output
    assert sum(row["status"] == "waiting" for row in Claims(terminal).queue()) == 20
    monkeypatch.setenv("UNDERCOVER_MAX_CLAIMS", "invalid")
    assert batch.main(scripted_runner, argv=["--claim-id", "CLM-1010"]) == 0
    assert "Attempted: 1 | Decided: 1" in capsys.readouterr().out
    assert sum(row["status"] == "waiting" for row in Claims(terminal).queue()) == 19
    assert batch.main(scripted_runner, argv=["--claim-id", "CLM-1010"]) == 1
    assert "not eligible" in capsys.readouterr().err
    assert batch.main(scripted_runner, argv=["--claim-id", "missing"]) == 1
    assert "Claim not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--max-claims", "0"],
        ["--max-claims", "-1"],
        ["--max-claims", "1.5"],
        ["--max-claims", "five"],
        ["--claim-id", ""],
        ["--max-claims", "5", "--claim-id", "CLM-1001"],
    ],
)
def test_invalid_terminal_scope_never_attempts_a_claim(terminal, capsys, argv):
    assert batch.main(scripted_runner, argv=argv) == 1
    assert "Attempted: 0" in capsys.readouterr().out
    assert Claims(terminal).events("CLM-1001") == []
