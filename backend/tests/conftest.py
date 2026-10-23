import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.storage import Store


@pytest.fixture
def repository(tmp_path):
    store = Store(tmp_path / "claims.db")
    store.initialize()
    return store


@pytest.fixture
def client(repository):
    with TestClient(create_app(repository)) as api:
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
    }
