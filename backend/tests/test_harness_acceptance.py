from uuid import uuid4

from backend.tests.conftest import decision_payload


class Scenario:
    def __init__(self, client):
        self.client = client
        self.run_id = str(uuid4())
        self.path = f"/api/agent-runs/{self.run_id}"

    def start(self, claim_id):
        thread = str(uuid4())
        response = self.client.post(
            self.path + "/attempts", json={"claim_id": claim_id, "thread_id": thread}
        )
        assert response.status_code == 201
        return thread

    def finish(self, thread, error=""):
        response = self.client.post(
            f"{self.path}/attempts/{thread}/finish", json={"error": error}
        )
        assert response.status_code == 200
        return response.json()

    def audit(self, claim_id, thread):
        for source, section in [
            ("policy_wording", "3"),
            ("policy_wording", "10"),
            ("claims_manual", "6"),
        ]:
            response = self.client.post(
                f"/api/claims/{claim_id}/reference-reads",
                json={"thread_id": thread, "source": source, "section_id": section},
            )
            assert response.status_code == 201

    def settle(self, claim_id, outcome, payout):
        thread = self.start(claim_id)
        self.audit(claim_id, thread)
        response = self.client.post(
            f"/api/claims/{claim_id}/decision",
            json=decision_payload(thread, outcome, payout),
        )
        assert response.status_code == 201
        self.finish(thread)
        return thread


def seed_six_claims(repository):
    original = repository.record("claim", "CLM-1001")
    with repository.transaction() as connection:
        connection.execute("DELETE FROM claims")
    ids = [f"TEST-{index:03d}" for index in range(1, 7)]
    for claim_id in ids:
        repository.add_claim(original | {"claim_id": claim_id})
    return ids


def test_published_seven_step_api_acceptance(client, repository):
    ids = seed_six_claims(repository)
    assert client.get("/api/agent-runs/current").json() == {"run": None}
    scenario = Scenario(client)
    body = {"run_id": scenario.run_id, "scope": {"mode": "limited", "max_claims": 5}}
    created = client.post("/api/agent-runs", json=body)
    assert created.status_code == 201
    assert created.json()["snapshot_claim_ids"] == ids
    assert client.post("/api/agent-runs", json=body).status_code == 200
    todos = [
        {
            "id": claim_id,
            "claim_id": claim_id,
            "text": f"Assess {claim_id}",
            "status": "to_do",
        }
        for claim_id in ids[:5]
    ]
    assert (
        client.put(scenario.path + "/todos", json={"items": todos}).status_code == 200
    )
    first = scenario.start(ids[0])
    assert (
        client.post(
            scenario.path + "/attempts", json={"claim_id": ids[0], "thread_id": first}
        ).status_code
        == 200
    )
    assert (
        client.post(
            scenario.path + "/attempts",
            json={"claim_id": ids[1], "thread_id": str(uuid4())},
        ).status_code
        == 409
    )
    finish = {"status": "completed", "detail": ""}
    assert client.post(scenario.path + "/finish", json=finish).status_code == 409
    read_body = {"thread_id": first, "source": "policy_wording", "section_id": "3"}
    read_path = f"/api/claims/{ids[0]}/reference-reads"
    evidence = client.post(read_path, json=read_body)
    assert evidence.status_code == 201
    replay = client.post(read_path, json=read_body)
    assert replay.status_code == 200
    assert replay.json() == evidence.json()
    assert (
        client.post(
            f"/api/claims/{ids[0]}/decision", json=decision_payload(first)
        ).status_code
        == 409
    )
    assert scenario.finish(first, "Synthetic timeout")["claim_status"] == "waiting"
    retry = scenario.start(ids[0])
    scenario.audit(ids[0], retry)
    assert (
        client.post(
            f"/api/claims/{ids[0]}/decision",
            json=decision_payload(retry, "approve", "420.00"),
        ).status_code
        == 201
    )
    payment = {"thread_id": retry, "amount": "420.00"}
    assert (
        client.post(f"/api/claims/{ids[0]}/agent-pay", json=payment).status_code == 201
    )
    assert (
        client.post(f"/api/claims/{ids[0]}/agent-pay", json=payment).status_code == 409
    )
    scenario.finish(retry)
    assert client.get(scenario.path + "/completion-check").json()["missing_claim_ids"]
    assert client.post(scenario.path + "/finish", json=finish).status_code == 409
    for _ in range(2):
        scenario.finish(scenario.start(ids[1]), "Synthetic provider failure")
    held = client.get(f"/api/claims/{ids[1]}").json()
    assert held["status"] == "handler_review"
    assert held["failed_attempts"] == 2
    assert held["decision"] is held["payment"] is None
    scenario.settle(ids[2], "approve", "1250.00")
    scenario.settle(ids[3], "decline", "0")
    scenario.settle(ids[4], "refer", "0")
    check = client.get(scenario.path + "/completion-check").json()
    assert check["complete"] is True
    assert check["capacity_remaining"] == 0
    assert (
        check["missing_claim_ids"]
        == check["retryable_claim_ids"]
        == check["active_claim_ids"]
        == []
    )
    sixth = client.post(
        scenario.path + "/attempts",
        json={"claim_id": ids[5], "thread_id": str(uuid4())},
    )
    assert sixth.status_code == 409
    assert sixth.json()["detail"] == "Run claim limit reached."
    verify_handover(scenario, ids, first)


def verify_handover(scenario, ids, first):
    client = scenario.client
    finish = {"status": "completed", "detail": ""}
    result = client.post(scenario.path + "/finish", json=finish).json()
    assert result["status"] == "completed"
    assert result["counts"] == dict(
        worked=5,
        assessment_attempts=7,
        failed_assessments=3,
        approved=2,
        declined=1,
        referred=1,
        simulated_payments=1,
        awaiting_sign_off=1,
        handler_review=1,
        unpaid_approvals=0,
        waiting_remaining=1,
    )
    assert client.post(scenario.path + "/finish", json=finish).json() == result
    assert client.get(f"/api/claims/{ids[5]}").json()["status"] == "waiting"
    assert (
        client.post(
            f"/api/claims/{ids[0]}/reference-reads",
            json={"thread_id": first, "source": "policy_wording", "section_id": "3"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/claims/{ids[0]}/decision", json=decision_payload(first)
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/claims/{ids[0]}/agent-pay",
            json={"amount": "420.00", "thread_id": first},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"{scenario.path}/attempts/{first}/finish",
            json={"error": "Different failure"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/claims/{ids[2]}/sign-off", json={"handler_id": "HND-01"}
        ).status_code
        == 201
    )
    assert (
        client.post(f"/api/claims/{ids[2]}/pay", json={"amount": "1250.00"}).status_code
        == 201
    )
    assert client.get(scenario.path).json() == result
