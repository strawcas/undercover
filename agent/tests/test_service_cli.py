import socket

import httpx
import pytest

from agent.local_process import Settings, available, local_service
from agent.tests.queue_stubs import ScriptedQueueModel
from agent.tests.test_cli import scripted_runner
from backend import batch
from backend.claims import Claims
from backend.config import agent_url, backend_url, database_identity
from backend.harness_models import RunFinishInput
from backend.models import DecisionInput
from backend.runs import Runs
from backend.tests.conftest import begin_claim, decide_fixture, decision_payload


def unused_url():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{listener.getsockname()[1]}"


@pytest.fixture
def addresses(repository, monkeypatch):
    monkeypatch.setenv("UNDERCOVER_BACKEND_URL", unused_url())
    monkeypatch.setenv("UNDERCOVER_AGENT", unused_url())
    return repository


def test_service_cli_standalone_owns_no_parent_pool_and_releases_children(
    addresses, capsys
):
    assert (
        batch.main(
            scripted_runner,
            ["--cap", "1"],
            service=True,
            model_factory=ScriptedQueueModel,
        )
        == 0
    )
    assert "Attempted: 1 | Decided: 1" in capsys.readouterr().out
    assert Claims(addresses).detail("CLM-1001")["payment"]["amount"] == "420.00"
    identity = database_identity(addresses.path)
    assert not available(Settings("backend", backend_url()), identity)
    assert not available(Settings("agent", agent_url()), identity)


def test_service_cli_attaches_and_preserves_idle_app(addresses, capsys):
    identity = database_identity(addresses.path)
    with (
        local_service(Settings("backend", backend_url()), identity),
        local_service(
            Settings("agent", agent_url(), scripted_runner, ScriptedQueueModel),
            identity,
        ),
    ):
        assert Runs(addresses).current() is None
        assert batch.main(argv=["--claim-id", "CLM-1001"], service=True) == 0
        assert "Attempted: 1 | Decided: 1" in capsys.readouterr().out
        assert available(Settings("agent", agent_url()), identity)
        with httpx.Client(base_url=agent_url()) as client:
            assert client.get("/health").json() == {"status": "ok"}
        assert Claims(addresses).detail("CLM-1002")["status"] == "waiting"


def test_service_cli_refuses_another_database_without_admission(
    addresses, monkeypatch, capsys
):
    identity = database_identity(addresses.path)
    with local_service(Settings("backend", backend_url()), identity):
        monkeypatch.setenv(
            "UNDERCOVER_DB_PATH", str(addresses.path.with_name("other.db"))
        )
        assert batch.main(argv=["--cap", "1"], service=True) == 1
    assert "another service or database" in capsys.readouterr().err
    assert Runs(addresses).current() is None


def test_cli_reports_signed_off_continuation_outside_new_scope(addresses, capsys):
    thread = begin_claim(addresses)
    claims = Claims(addresses)
    decide_fixture(
        claims, "CLM-1001", DecisionInput.model_validate(decision_payload(thread))
    )
    with addresses.transaction() as connection:
        connection.execute(
            "UPDATE claims SET state='handler_review' WHERE id<>'CLM-1001'"
        )
    run_id = claims.detail("CLM-1001")["run_id"]
    runs = Runs(addresses)
    runs.finish_attempt(run_id, thread, "")
    runs.finish(run_id, RunFinishInput(status="completed", detail=""))
    claims.sign_off("CLM-1001", "HND-01")
    assert (
        batch.main(scripted_runner, service=True, model_factory=ScriptedQueueModel) == 0
    )
    output = capsys.readouterr().out
    assert "CLM-1001 APPROVE" in output
    assert "Simulated payment recorded" in output
    assert "Resumed saved assessments: 1" in output
    assert "Attempted: 0 | Decided: 0" in output
    assert claims.detail("CLM-1001")["thread_id"] == thread
    assert claims.detail("CLM-1001")["failed_attempts"] == 0
