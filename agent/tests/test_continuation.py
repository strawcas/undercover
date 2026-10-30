import pytest

from agent import main, queue
from agent.execution import AgentRunError, Execution
from agent.tests.stubs import StubModel, api_client, scripted_model
from backend.claims import Claims


def test_signed_off_claim_continues_after_parent_queue_finished(
    client, repository, monkeypatch
):
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    monkeypatch.setattr(queue, "backend", lambda: api_client(client))
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE claims SET state='handler_review' WHERE id<>'CLM-1001'"
        )
    parent = queue.run_queue(
        {"mode": "all"},
        lambda c, t, *, budget: main.run(
            c, t, scripted_model(payout="1250.00"), budget=budget
        ),
    )
    assert parent.counts.awaiting_sign_off == 1
    before = client.get(f"/api/agent-runs/{parent.run_id}").json()
    signed = client.post(
        "/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"}
    ).json()
    claim = Claims(repository).detail("CLM-1001")
    assert claim["status"] == "in_progress"
    assert claim["payment"] is None
    unused = StubModel(messages=iter(()))
    execution = Execution()
    queue.run_queue(
        {"mode": "all"},
        lambda c, t, *, budget: main.run(c, t, unused, budget=budget),
        execution=execution,
    )
    paid = Claims(repository).detail("CLM-1001")
    assert paid["payment"]["sign_off"] == signed
    assert paid["payment"]["amount"] == "1250.00"
    assert unused.first_messages == []
    assert execution.resumed_count == 1
    assert client.get(f"/api/agent-runs/{parent.run_id}").json() == before
    events = Claims(repository).events("CLM-1001")
    queue.run_queue(
        {"mode": "all"}, lambda c, t, *, budget: main.run(c, t, unused, budget=budget)
    )
    assert Claims(repository).events("CLM-1001") == events


def test_interrupted_capped_queue_retains_scope_and_fences_old_coordinator(
    client, repository, monkeypatch
):
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    monkeypatch.setattr(queue, "backend", lambda: api_client(client))
    execution = Execution()

    def stop(_claim, _thread, *, budget):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        queue.run_queue({"mode": "limited", "max_claims": 2}, stop, execution=execution)
    assert execution.result is not None
    thread = execution.result.attempts[0].thread_id
    with pytest.raises(AgentRunError, match="saved scope"):
        queue.run_queue({"mode": "limited", "max_claims": 1})
    repository.add_claim(
        repository.record("claim", "CLM-1001") | {"claim_id": "TEST-LATER"}
    )

    def resume(claim, identity, *, budget):
        old = client.post(
            f"/api/agent-runs/{execution.run_id}/finish",
            json={"status": "interrupted", "detail": "Old coordinator"},
            headers={"X-UnderCover-Run-Generation": "1"},
        )
        assert old.status_code == 409
        return main.run(claim, identity, scripted_model(), budget=budget)

    result = queue.run_queue({"mode": "all"}, resume)
    assert result.run_id == execution.run_id
    assert result.claim_limit == result.counts.worked == 2
    assert result.counts.assessment_attempts == 2
    assert result.counts.failed_assessments == 0
    assert result.attempts[0].thread_id == thread
    assert Claims(repository).detail("TEST-LATER")["status"] == "waiting"
    assert len(result.snapshot_claim_ids) == 25
