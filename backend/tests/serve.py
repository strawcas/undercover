"""Run the full-stack test app with a temporary database and scripted models."""

import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

import uvicorn

from agent import queue
from agent.main import run
from agent.tests.queue_stubs import ScriptedQueueModel
from agent.tests.stubs import scripted_model
from backend.batch import main as run_command
from backend.main import create_app
from backend.storage import Store

PORT = int(os.getenv("UNDERCOVER_TEST_PORT", "8011"))
os.environ["UNDERCOVER_BACKEND_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["MODEL_PROVIDER"] = "openrouter"
os.environ["MODEL_NAME"] = ""
os.environ["OPENROUTER_API_KEY"] = "stub-models-only"
os.environ.pop("UNDERCOVER_MAX_CLAIMS", None)
attempts: dict[str, int] = {}


def scripted_runner(claim_id: str, thread_id: str, *, budget) -> str:
    time.sleep(1.2)
    attempts[claim_id] = attempts.get(claim_id, 0) + 1
    if claim_id == "CLM-1001" and attempts[claim_id] == 1:
        store = app.state.claims.store
        store.add_claim(store.record("claim", claim_id) | {"claim_id": "TEST-LATER"})
    if claim_id == "CLM-1005" and attempts[claim_id] == 1:
        raise RuntimeError("Synthetic provider failure")
    if claim_id == "CLM-1006":
        raise RuntimeError("Synthetic repeated provider failure")
    outcomes = {
        "CLM-1002": ("decline", "0"),
        "CLM-1003": ("approve", "1250.00"),
        "CLM-1004": ("refer", "0"),
    }
    outcome, payout = outcomes.get(claim_id, ("approve", "420.00"))
    return run(claim_id, thread_id, scripted_model(outcome, payout), budget=budget)


def wait_for_command(gate: Path) -> None:
    """Let browser tests explicitly invoke the CLI after checking the idle app."""
    while not gate.exists():
        time.sleep(0.1)
    queue.model = ScriptedQueueModel
    run_command(scripted_runner)


if __name__ == "__main__":
    with TemporaryDirectory(prefix="undercover-e2e-") as directory:
        store = Store(Path(directory) / "claims.db")
        os.environ["UNDERCOVER_DB_PATH"] = str(store.path)
        app = create_app(store)
        gate = os.getenv("UNDERCOVER_TEST_GATE")
        if gate:
            Thread(target=wait_for_command, args=(Path(gate),), daemon=True).start()
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=PORT,
            log_level="warning",
        )
