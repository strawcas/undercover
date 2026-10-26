import json
from uuid import uuid4

import pytest

from backend.claims import Claims
from backend.models import DecisionInput
from backend.reference_index import sections
from backend.tests.conftest import audit_decision, begin_claim, decision_payload


def read(client, thread, source, section_id, claim_id="CLM-1001"):
    return client.post(
        f"/api/claims/{claim_id}/reference-reads",
        json={
            "thread_id": thread,
            "source": source,
            "section_id": section_id,
        },
    )


def test_contents_and_audited_full_sections(client, repository):
    thread = begin_claim(repository)
    contents = client.get("/api/reference-documents/claims_manual/sections")
    assert contents.status_code == 200
    assert contents.json()["version"] == "2026.2"
    assert {
        "section_id": "7.wild-animals",
        "title": "Wild animals",
        "parent_id": "7",
    } in contents.json()["sections"]
    result = read(client, thread, "claims_manual", "7")
    assert result.status_code == 201
    evidence = result.json()
    assert len(evidence["text"]) > 2000
    assert "**Wild animals.**" in evidence["text"]
    assert evidence["thread_id"] == thread
    assert evidence["run_id"] == Claims(repository).detail("CLM-1001")["run_id"]
    replay = read(client, thread, "claims_manual", "7")
    assert replay.status_code == 200
    assert replay.json() == evidence
    saved = client.get("/api/claims/CLM-1001/evidence").json()
    assert saved == {"claim_id": "CLM-1001", "reads": [evidence]}
    assert (
        len(
            [
                event
                for event in Claims(repository).events("CLM-1001")
                if event["action"] == "Read reference section"
            ]
        )
        == 2
    )
    assert client.get("/api/claims/CLM-1002/evidence").json()["reads"] == []


def test_search_is_not_a_read_and_empty_results_are_valid(client, repository):
    thread = begin_claim(repository)
    body = {"thread_id": thread, "source": "claims_manual", "query": "wild animals"}
    result = client.post("/api/claims/CLM-1001/reference-search", json=body)
    assert result.status_code == 200
    matches = result.json()["matches"]
    assert any(item["section_id"] == "7.wild-animals" for item in matches)
    assert len(matches) <= 10
    assert all(len(item["snippet"]) <= 240 for item in matches)
    assert client.get("/api/claims/CLM-1001/evidence").json()["reads"] == []
    body["query"] = "zznonexistenttermzz"
    assert (
        client.post("/api/claims/CLM-1001/reference-search", json=body).json()[
            "matches"
        ]
        == []
    )


def test_unread_unknown_and_client_supplied_decision_provenance(client, repository):
    thread = begin_claim(repository)
    body = decision_payload(thread, payout="420")
    path = "/api/claims/CLM-1001/decision"
    assert client.post(path, json=body).status_code == 409
    audit_decision(repository, "CLM-1001", DecisionInput.model_validate(body))
    missing = dict(body)
    del missing["manual_sections"]
    assert client.post(path, json=missing).status_code == 422
    assert (
        client.post(path, json=body | {"claims_manual_version": "2026.2"}).status_code
        == 422
    )
    assert client.post(path, json=body | {"manual_sections": ["99"]}).status_code == 404
    result = client.post(path, json=body)
    assert result.status_code == 201
    assert result.json()["policy_wording_version"] == "2026.1"
    assert result.json()["claims_manual_version"] == "2026.2"
    assert result.json()["manual_sections"] == ["6"]
    assert client.post(path, json=body).status_code == 409
    payment = {"thread_id": thread, "amount": "420"}
    assert (
        client.post("/api/claims/CLM-1001/agent-pay", json=payment).status_code == 201
    )
    assert (
        client.post("/api/claims/CLM-1001/agent-pay", json=payment).status_code == 409
    )


def test_expired_wrong_and_finished_attempts_cannot_write(client, repository):
    thread = begin_claim(repository)
    assert read(client, thread, "claims_manual", "6", "CLM-1002").status_code == 409
    assert read(client, str(uuid4()), "claims_manual", "6").status_code == 409
    with repository.transaction() as connection:
        row = connection.execute(
            "SELECT body FROM agent_attempts WHERE thread_id=?", (thread,)
        ).fetchone()
        entry = json.loads(row["body"])
        entry["deadline_at"] = "2000-01-01T00:00:00+00:00"
        connection.execute(
            "UPDATE agent_attempts SET body=? WHERE thread_id=?",
            (json.dumps(entry), thread),
        )
    assert read(client, thread, "claims_manual", "6").status_code == 409
    assert (
        client.post(
            "/api/claims/CLM-1001/agent-pay", json={"thread_id": thread, "amount": "1"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/claims/CLM-1001/decision", json=decision_payload(thread)
        ).status_code
        == 409
    )
    Claims(repository).finish("CLM-1001", thread, "Assessment deadline exceeded.")
    assert read(client, thread, "claims_manual", "6").status_code == 409


@pytest.mark.parametrize(
    ("source", "section_id", "status"),
    [
        ("policy_wording", "missing", 404),
        ("claims_manual", "../../evals", 404),
        ("other", "3", 422),
    ],
)
def test_reference_allowlist_and_unknown_sections(
    client, repository, source, section_id, status
):
    thread = begin_claim(repository)
    assert read(client, thread, source, section_id).status_code == status
    assert client.get("/api/claims/missing/evidence").status_code == 404
    assert client.get("/api/reference-documents/other/sections").status_code == 422


def test_legacy_decisions_and_instruction_contract(client, repository):
    legacy = decision_payload("legacy", "refer", "0") | {
        "created_at": "2026-01-01T00:00:00Z"
    }
    del legacy["manual_sections"]
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE claims SET decision=? WHERE id='CLM-1001'", (json.dumps(legacy),)
        )
    old = client.get("/api/claims/CLM-1001").json()["decision"]
    assert old["manual_sections"] == []
    assert old["policy_wording_version"] is old["claims_manual_version"] is None
    prompts = client.get("/api/agent-instructions").json()
    assert prompts["text"] == repository.record("settings", "instructions")
    assert prompts["queue_text"] == repository.record("settings", "queue_instructions")
    assert "claims manual" in prompts["text"]
    assert "third-claim" in prompts["text"]
    assert "wild-animal" in prompts["text"]
    assert "meal allowance" in prompts["text"]


@pytest.mark.parametrize(
    "text", ["No version or sections", "Version 2026.2 without headings"]
)
def test_invalid_reference_document(text):
    with pytest.raises(ValueError, match="version and numbered sections"):
        sections(text)
