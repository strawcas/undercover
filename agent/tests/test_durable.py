import multiprocessing
import socket
from datetime import UTC, datetime
from time import sleep

import pytest

from agent import queue
from agent.main import run
from agent.tests.crash_worker import CrashCase
from agent.tests.crash_worker import run as crash_worker
from agent.tests.stubs import StubModel, scripted_model
from backend.claims import Claims
from backend.harness_models import ClaimScope
from backend.local import local_backend
from backend.runs import Runs
from backend.tests.conftest import begin_claim


@pytest.mark.parametrize("mode", ["checkpoint", "decision", "payment", "complete"])
def test_killed_process_resumes_same_attempt_and_queue_once(
    repository, monkeypatch, mode
):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("UNDERCOVER_BACKEND_URL", url)
    claims = Claims(repository)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    with local_backend(repository, lambda c, t, *, budget: "unused", url):
        thread = begin_claim(
            repository, scope=ClaimScope(mode="claim", claim_id="CLM-1001")
        )
        original = Runs(repository).current()
        assert original is not None
        process = context.Process(
            target=crash_worker,
            args=(
                CrashCase(mode, "CLM-1001", thread, url, str(repository.path)),
                ready,
            ),
        )
        process.start()
        try:
            assert ready.wait(20), f"Crash worker stopped before {mode} checkpoint"
        finally:
            process.terminate()
            process.join(10)
        assert not process.is_alive()
        before = Runs(repository).current()
        assert before is not None
        lease_until = before["attempts"][0]["lease_until"]
        while lease_until and datetime.now(UTC).isoformat() <= lease_until:
            sleep(0.05)
        repository.add_claim(
            repository.record("claim", "CLM-1001") | {"claim_id": "TEST-LATER"}
        )
        models = []

        def resume(claim_id, thread_id, *, budget):
            assert thread_id == thread
            replies = (
                list(scripted_model().messages)[1:] if mode == "checkpoint" else []
            )
            model = StubModel(messages=iter(replies))
            models.append(model)
            return run(claim_id, thread_id, model, budget=budget)

        result = queue.run_queue({"mode": "all"}, resume)
        assert_saved_run(result, original, thread)
        detail = claims.detail("CLM-1001")
        assert detail["payment"]["amount"] == "420.00"
        assert detail["failed_attempts"] == 0
        events = claims.events("CLM-1001")
        assert sum(e["action"] == "Record decision" for e in events) == 1
        assert sum(e["action"] == "Simulated payment" for e in events) == 1
        assert claims.detail("TEST-LATER")["status"] == "waiting"
        if mode == "checkpoint":
            assert len(models[0].first_messages) > 2
        else:
            assert models[0].first_messages == []


def assert_saved_run(result, original, thread):
    assert result.run_id == original["run_id"]
    assert result.snapshot_claim_ids == original["snapshot_claim_ids"]
    assert result.counts.worked == result.counts.assessment_attempts == 1
    assert result.counts.failed_assessments == 0
    assert result.attempts[0].thread_id == thread
    assert result.attempts[0].attempt_number == 1
    assert result.status == "completed"
