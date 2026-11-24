import pytest
from fastapi import BackgroundTasks
from langgraph.checkpoint.memory import InMemorySaver

from agent import harness, service
from agent.backend_client import Refused
from agent.tests.records import claim_of, steps_of
from agent.tests.runtime_support import invoke, runtime_client, wait_for_idle
from agent.tests.stubs import decide, graph_on, stub


@pytest.fixture
def serve(monkeypatch, runtime_defaults):
    """Run the agent's service, without its lifespan, on a given claim graph."""

    def with_graph(graph):
        monkeypatch.setattr(
            harness, "execution_graph", lambda version=None, **options: graph
        )
        return runtime_client()

    return with_graph


def resume(agent, claim_id):
    response = invoke(agent, "resume_claim", claim_id=claim_id)
    wait_for_idle(agent)
    return response


@pytest.mark.usefixtures("runtime_defaults")
def test_health():
    assert invoke(runtime_client({}), "health").json() == {"healthy": True}


@pytest.mark.parametrize(
    ("operation", "function", "inputs"),
    [
        ("work_run", "schedule_run", {"run_id": "run"}),
        ("work_round", "schedule_round", {"round_id": "round"}),
        ("carry_round_on", "schedule_round", {"round_id": "round", "proposal_id": "p"}),
        ("resume_claim", "resume_claim", {"claim_id": "CLM-1001"}),
    ],
)
@pytest.mark.usefixtures("runtime_defaults")
def test_invocation_accepts_and_passes_on_refusal(
    monkeypatch, operation, function, inputs
):
    monkeypatch.setattr(service, function, lambda *args: None)
    agent = runtime_client()
    assert invoke(agent, operation, **inputs).json() == {"accepted": True}

    def refuse(*args):
        raise Refused(409, "The work is unavailable")

    monkeypatch.setattr(service, function, refuse)
    assert invoke(agent, operation, **inputs).json() == {
        "refused": "The work is unavailable"
    }


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_round_work_refuses_unauthenticated_requests(monkeypatch, headers):
    def unexpected(*args):
        pytest.fail("The service attempted unauthenticated round work")

    monkeypatch.setattr(service, "schedule_round", unexpected)
    agent = runtime_client(headers)
    assert invoke(agent, "work_round", round_id="round").status_code == 401
    assert (
        invoke(agent, "carry_round_on", round_id="round", proposal_id="p").status_code
        == 401
    )


def test_a_paid_sign_off_can_finish_the_saved_checkpoint(
    client, serve, agent_calls, traces, monkeypatch
):
    model = stub(*decide("approve", 1350.0))
    saver = InMemorySaver()
    graph = graph_on(model, saver)
    harness.work_claim("CLM-1002", graph)
    signed = client.post("/api/claims/CLM-1002/sign-off", json={"handler_id": "HND-01"})
    assert agent_calls == [{"operation": "resume_claim", "claim_id": "CLM-1002"}]
    assert signed.json()["payment"]["amount"] == 1350

    def unexpected_payment(*args):
        pytest.fail("A resumed sign-off should never call payment")

    monkeypatch.setattr(harness.api, "pay_claim", unexpected_payment)

    agent = serve(graph_on(stub(), saver))
    response = resume(agent, "CLM-1002")
    assert response.json() == {"accepted": True}
    claim = claim_of(client, "CLM-1002")
    assert claim["status"] == "decided"
    assert claim["payment"]["amount"] == 1350.0
    assert claim["payment"]["signed_off_by"] == "Dana Whitfield"
    signed_off_at = signed.json()["decision"]["signed_off_at"]
    assert claim["payment"]["signed_off_at"] == signed_off_at
    assert len(model.prompts) == 3
    session = f"CLM-1002:{claim['thread_id']}"
    assert [(t["langfuse_session_id"], t["langfuse_trace_name"]) for t in traces] == [
        (session, "Work claim"),
        (session, "Carry on"),
    ]

    assert "refused" in resume(agent, "CLM-1002").json()
    assert claim_of(client, "CLM-1002")["payment"] == claim["payment"]


def test_a_claim_waiting_for_sign_off_is_not_picked_up(client, serve):
    graph = graph_on(stub(*decide("approve", 1350.0)))
    harness.work_claim("CLM-1002", graph)
    before = steps_of(client, "CLM-1002")
    response = resume(serve(graph), "CLM-1002")
    assert response.status_code == 200
    assert response.json()["refused"] == (
        "Claim CLM-1002 has no paid sign-off to resume"
    )
    assert claim_of(client, "CLM-1002")["status"] == "waiting_for_sign_off"
    assert steps_of(client, "CLM-1002") == before


def test_a_claim_in_progress_without_a_sign_off_is_left_to_its_agent(client, serve):
    client.post("/api/claims/CLM-1001/start", json={"thread_id": "working"})
    response = resume(serve(graph_on(stub())), "CLM-1001")
    assert "refused" in response.json()
    claim = claim_of(client, "CLM-1001")
    assert (claim["status"], claim["attempts"]) == ("in_progress", 1)
    assert steps_of(client, "CLM-1001") == []


def test_an_unknown_claim(serve):
    response = resume(serve(graph_on(stub())), "CLM-9999")
    assert response.status_code == 404
    assert response.json()["error"] == "Claim CLM-9999 not found"


def test_sign_off_is_queued_before_current_worker_releases(client, monkeypatch):
    graph = graph_on(stub(*decide("approve", 1350)))
    monkeypatch.setattr(
        harness, "execution_graph", lambda version=None, **options: graph
    )
    harness.work_claim("CLM-1002", graph)
    client.post("/api/claims/CLM-1002/sign-off", json={"handler_id": "HND-01"})
    assert harness.reserve_claim("CLM-1002")
    background = BackgroundTasks()
    assert service.resume_claim("CLM-1002", background) == {"status": "accepted"}
    with pytest.raises(Refused) as duplicate:
        service.resume_claim("CLM-1002", BackgroundTasks())
    assert duplicate.value.status_code == 409
    harness.release_claim("CLM-1002")
    assert harness.execute_claim("CLM-1002", "other-run", 1).startswith("Skipped:")
    task = background.tasks[0]
    task.func(*task.args, **task.kwargs)
    assert not harness.is_reserved("CLM-1002")
    assert client.get("/api/claims/CLM-1002").json()["payment"]["amount"] == 1350


def test_resume_handoff_reaches_the_interrupt_after_the_claim_was_paid(
    client, monkeypatch
):
    graph = graph_on(stub(*decide("approve", 1350)))
    monkeypatch.setattr(
        harness, "execution_graph", lambda version=None, **kwargs: graph
    )
    original = harness.interrupt

    def stop_before_interrupt(value):
        raise KeyboardInterrupt

    monkeypatch.setattr(harness, "interrupt", stop_before_interrupt)
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim("CLM-1002", graph)
    claim = claim_of(client, "CLM-1002")
    thread = harness.thread_of(claim)
    assert graph.get_state(thread).next == (harness.PAUSE,)
    assert not graph.get_state(thread).interrupts
    client.post("/api/claims/CLM-1002/sign-off").raise_for_status()
    assert harness.reserve_claim("CLM-1002")
    background = BackgroundTasks()
    service.resume_claim("CLM-1002", background)
    monkeypatch.setattr(harness, "interrupt", original)
    harness.release_claim("CLM-1002")
    task = background.tasks[0]
    task.func(*task.args, **task.kwargs)
    assert graph.get_state(thread).next == ()
