"""Recorded round and whole-set score fixtures without model calls."""

from sqlalchemy import insert, select, update

from backend import evaluations, improvements
from backend import runs as run_records
from backend.database import engine, model_calls, run_claims, runs
from backend.models import RunRequest, Scores
from backend.tests.run_requests import configuration


def scores(
    accuracy: float = 0.7,
    grade: float | None = 1.5,
    cost: float | None = 0.001,
    seconds: float = 10,
):
    return Scores(
        accuracy=accuracy,
        decision_accuracy=accuracy,
        grade=grade,
        cost_of_mistakes=0,
        straight_through=accuracy,
        model_cost_per_claim=cost,
        seconds_per_claim=seconds,
    )


def new_round(client):
    handler = client.get("/api/me").json()
    response = client.post("/api/improver/rounds", json=handler)
    assert response.status_code == 200, response.text
    return response.json()


def freeze(round_id, version=1, set_name="held_out", values=None):
    """Create a whole-set score fact independently of the admission under test."""
    with engine().begin() as db:
        request = RunRequest(
            **configuration(
                kind="evaluation",
                set_name=set_name,
                tiny=False,
                round_id=round_id,
                configuration_version=version,
            )
        )
        run = run_records.start_run(db, request)
        evaluations.create_copies(db, run.run_id, request)
        run_records.finish_run(db, run.run_id)
        db.execute(
            update(runs)
            .where(runs.c.run_id == run.run_id)
            .values(status="finished", scores=(values or scores()).model_dump())
        )
        return run.run_id


def baseline(client, current):
    for name in ("evaluation", "held_out", "held_out"):
        freeze(current["round_id"], set_name=name)
    with engine().begin() as db:
        improvements.baseline(db, current["round_id"])


def bill_run(identity, cost):
    """Attach a provider receipt to a frozen run fixture for estimate tests."""
    with engine().begin() as db:
        claim_id = (
            db.execute(
                select(run_claims.c.claim_id).where(run_claims.c.run_id == identity)
            )
            .scalars()
            .first()
        )
        db.execute(
            insert(model_calls).values(
                claim_id=claim_id,
                thread_id="fixture-thread",
                reply_id=identity,
                run_id=identity,
                cost=cost,
            )
        )
        return claim_id


def proposal(client, current, identity="proposal-one", kind="configuration", **changes):
    configuration = client.get("/api/configurations/current").json()
    arguments = (
        {
            "instructions": configuration["instructions"]
            + "\nCheck the evidence carefully.",
            "model": configuration["model"],
            "reasoning_effort": "low",
            "reason": "Improve evidence grounding",
        }
        if kind == "configuration"
        else {
            "recipe": "Use stronger development evidence.",
            "reason": "Learn from the tested change",
        }
    )
    return {"proposal_id": identity, "kind": kind, "arguments": arguments | changes}


def decide(client, current, pending, decision="approve", reason=None):
    return client.post(
        f"/api/improver/rounds/{current['round_id']}/proposals/{pending['proposal_id']}/decision",
        json={
            "decision": decision,
            "reason": reason,
            "handler": client.get("/api/me").json(),
        },
    )
