"""Run configurations shared by backend and agent test scenarios."""

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from backend.database import engine
from backend.main import create_run
from backend.models import RunRequest


def configuration(**overrides):
    return {
        "kind": "dispatch",
        "concurrency": 3,
        "configuration_version": 1,
        "round_id": None,
        "set_name": None,
        "tiny": None,
    } | overrides


def start(client, **overrides):
    selection = configuration(**overrides)
    if selection["round_id"] is not None:
        return client.post("/api/runs", json=selection)
    request = httpx.Request("POST", "/api/runs", json=selection)
    try:
        return httpx.Response(200, json=fixture_run(selection), request=request)
    except ValidationError as error:
        return httpx.Response(
            422, json={"detail": error.errors(include_context=False)}, request=request
        )
    except HTTPException as error:
        return httpx.Response(
            error.status_code, json={"detail": error.detail}, request=request
        )


def fixture_run(selection):
    """Create a run for record-level scenarios independent of slot admission."""
    with engine().begin() as db:
        return create_run(db, RunRequest(**selection)).model_dump(mode="json")


def member(client, run_id, claim_id, status="selected", error=None):
    return client.put(
        f"/api/runs/{run_id}/claims/{claim_id}", json={"status": status, "error": error}
    )
