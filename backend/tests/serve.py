"""Run the full-stack test app with a temporary database and scripted models."""

import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from agent.main import run
from agent.tests.stubs import scripted_model
from backend.main import create_app
from backend.storage import Store

PORT = 8011
os.environ["UNDERCOVER_BACKEND_URL"] = f"http://127.0.0.1:{PORT}"
attempts: dict[str, int] = {}


def scripted_runner(claim_id: str, thread_id: str) -> str:
    time.sleep(1.2)
    attempts[claim_id] = attempts.get(claim_id, 0) + 1
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
        uvicorn.run(
            create_app(Store(Path(directory) / "claims.db"), scripted_runner),
            host="127.0.0.1",
            port=PORT,
        )
