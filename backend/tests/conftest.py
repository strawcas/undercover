from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.harness_models import AllScope, AttemptInput, RunInput, TodosInput
from backend.main import create_app
from backend.models import DecisionInput
from backend.reference_models import ReadInput
from backend.references import References
from backend.runs import Runs
from backend.storage import Store


@pytest.fixture
def repository(tmp_path):
    store = Store(tmp_path / "claims.db")
    store.initialize()
    return store


@pytest.fixture
def client(repository):
    with TestClient(create_app(repository, auto_run=False)) as api:
        yield api


def decision_payload(thread_id, outcome="approve", payout="1250.00"):
    return {
        "thread_id": thread_id,
        "outcome": outcome,
        "payout": payout,
        "calculation": "Synthetic test calculation using policy limits.",
        "explanation": (
            "The submitted evidence supports this outcome. "
            "Section 3 sets the applicable benefit."
        ),
        "policy_sections": ["3", "10"],
        "manual_sections": ["6"],
    }


def begin_claim(repository, claim_id="CLM-1001"):
    runs = Runs(repository)
    run, _ = runs.create(RunInput(run_id=uuid4(), scope=AllScope(mode="all")))
    runs.todos(
        run["run_id"],
        TodosInput.model_validate(
            {
                "items": [
                    {
                        "id": f"claim:{claim_id}",
                        "claim_id": claim_id,
                        "text": f"Assess {claim_id}",
                        "status": "to_do",
                    }
                ]
            }
        ),
    )
    entry, _ = runs.start(
        run["run_id"], AttemptInput(claim_id=claim_id, thread_id=uuid4())
    )
    return entry["thread_id"]


def audit_decision(repository, claim_id, decision):
    references = References(repository)
    for source, section_ids in (
        ("policy_wording", decision.policy_sections),
        ("claims_manual", decision.manual_sections),
    ):
        for section_id in section_ids:
            references.read(
                claim_id,
                ReadInput.model_validate(
                    {
                        "thread_id": decision.thread_id,
                        "source": source,
                        "section_id": section_id,
                    }
                ),
            )


def decide_fixture(claims, claim_id, decision):
    if claims.detail(claim_id)["run_id"]:
        audit_decision(claims.store, claim_id, decision)
    return claims.decide(claim_id, decision)


def begin_decision_claim(repository):
    thread = begin_claim(repository)
    audit_decision(
        repository, "CLM-1001", DecisionInput.model_validate(decision_payload(thread))
    )
    return thread
