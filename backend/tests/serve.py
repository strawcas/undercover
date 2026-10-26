"""Run the full-stack test app with a temporary database and scripted models."""

import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from agent.main import run
from agent.tests.queue_stubs import ScriptedQueueModel
from agent.tests.stubs import scripted_model
from backend.main import create_app
from backend.storage import Store
from backend.worker import api_ready

PORT = int(os.getenv("UNDERCOVER_TEST_PORT", "8011"))
os.environ["UNDERCOVER_BACKEND_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["MODEL_PROVIDER"] = "openrouter"
os.environ["MODEL_NAME"] = ""
os.environ["OPENROUTER_API_KEY"] = "stub-models-only"
os.environ.pop("UNDERCOVER_MAX_CLAIMS", None)
attempts: dict[str, int] = {}


def scripted_runner(claim_id: str, thread_id: str) -> str:
    time.sleep(1.2)
    attempts[claim_id] = attempts.get(claim_id, 0) + 1
    if claim_id == "CLM-1001" and attempts[claim_id] == 1:
        store = app.state.claims.store
        store.add_claim(store.record("claim", claim_id) | {"claim_id": "TEST-LATER"})
    if claim_id == "CLM-1005" and attempts[claim_id] == 1:
        raise RuntimeError("Synthetic provider failure")
    outcomes = {
        "CLM-1002": ("decline", "0"),
        "CLM-1003": ("approve", "1250.00"),
        "CLM-1004": ("refer", "0"),
    }
    outcome, payout = outcomes.get(claim_id, ("approve", "420.00"))
    return run(claim_id, thread_id, scripted_model(outcome, payout))


if __name__ == "__main__":
    with TemporaryDirectory(prefix="undercover-e2e-") as directory:
        gate = os.getenv("UNDERCOVER_TEST_GATE")

        def ready():
            return (gate is None or Path(gate).exists()) and api_ready(
                f"http://127.0.0.1:{PORT}", app.state.instance_id
            )

        app = create_app(
            Store(Path(directory) / "claims.db"),
            scripted_runner,
            worker_ready=ready,
            queue_model=ScriptedQueueModel(),
        )
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=PORT,
            log_level="warning",
        )
