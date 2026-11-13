"""Isolation, answer-key boundaries and hand-calculated evaluation measures."""

import pytest
from sqlalchemy import delete, func, select, update

from backend import runs
from backend.database import claims, engine, evaluation_labels, evaluation_sources, seed
from backend.scoring import compare
from backend.tests.evaluation_requests import evaluation
from backend.tests.run_requests import configuration


@pytest.mark.parametrize("set_name", ["evaluation", "held_out"])
@pytest.mark.parametrize(("tiny", "count"), [(False, 25), (True, 5)])
def test_pristine_copies_never_change_queue(client, set_name, tiny, count):
    before = client.get("/api/claims").json()
    run = evaluation(client, set_name, tiny)
    assert len(run["members"]) == count
    ids = [m["claim_id"] for m in run["members"]]
    assert len(set(ids)) == count
    for claim_id in ids:
        copy = client.get(f"/api/claims/{claim_id}").json()
        assert (copy["claim_id"], copy["evaluation_id"], copy["attempts"]) == (
            claim_id,
            run["run_id"],
            0,
        )
        assert copy["source_id"] != claim_id
        assert copy["thread_id"] is copy["decision"] is copy["payment"] is None
        assert (
            not {
                "tiny",
                "handler_id",
                "reference",
                "tests",
                "difficulty",
                "over_approval_limit",
            }
            & copy.keys()
        )
        with engine().connect() as db:
            record = db.execute(
                select(claims.c.record).where(claims.c.claim_id == claim_id)
            ).scalar_one()
        assert record["claim_id"] == claim_id
    assert client.get("/api/claims").json() == before
    ordinary = client.post("/api/runs", json=configuration()).json()["run_id"]
    assert (
        client.put(
            f"/api/runs/{ordinary}/claims/{ids[0]}",
            json={"status": "selected", "error": None},
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/api/runs/{run['run_id']}/claims/CLM-1001",
            json={"status": "selected", "error": None},
        ).status_code
        == 409
    )


def test_seed_loads_both_sets_and_clears_scoreboard(client):
    with engine().connect() as db:
        for table in (evaluation_sources, evaluation_labels):
            assert (
                db.execute(select(func.count()).select_from(table)).scalar_one() == 50
            )
        assert (
            db.execute(
                select(func.count())
                .select_from(evaluation_labels)
                .where(evaluation_labels.c.tiny.is_(True))
            ).scalar_one()
            == 10
        )
        sources = db.execute(select(evaluation_sources.c.record)).scalars()
        for source in sources:
            assert client.get(f"/api/policies/{source['policy_id']}").status_code == 200
            assert (
                client.get(f"/api/customers/{source['customer_id']}/claims").status_code
                == 200
            )
    evaluation(client)
    seed(engine())
    assert client.get("/api/evaluations").json() == []
    assert len(client.get("/api/claims").json()) == 25


def test_original_work_is_not_copied_and_copies_cannot_be_signed_off(
    client, agent_calls
):
    client.post("/api/claims/CLM-1001/start", json={"thread_id": "original"})
    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "approve",
            "payout": 1350,
            "section": "3",
            "reason": "Covered",
        },
    )
    run = evaluation(client, tiny=False)
    copies = [client.get(f"/api/claims/{m['claim_id']}").json() for m in run["members"]]
    copy = next(c for c in copies if c["source_id"] == "CLM-1001")
    assert copy["decision"] is None
    client.post(f"/api/claims/{copy['claim_id']}/start", json={"thread_id": "copy"})
    client.post(
        f"/api/claims/{copy['claim_id']}/decision",
        json={
            "decision": "approve",
            "payout": 1350,
            "section": "3",
            "reason": "Covered",
        },
    )
    response = client.post(
        f"/api/claims/{copy['claim_id']}/sign-off", json={"handler_id": "HND-01"}
    )
    assert response.status_code == 409
    assert agent_calls == []
    assert (
        client.post(
            "/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"}
        ).status_code
        == 200
    )
    assert agent_calls == ["/api/claims/CLM-1001/resume"]


@pytest.mark.parametrize(
    "case",
    [
        ("approve", 101, "approve", 100, True, 1),
        ("approve", 101.01, "approve", 100, False, 1.01),
        ("approve", 101.11, "approve", 100.11, True, 1),
        ("decline", 0, "approve", 100, False, 1500),
        ("approve", 1350, "decline", 0, False, 1350),
        ("refer", 0, "decline", 0, False, 50),
        ("refer", 0, "refer", 0, True, 0),
        ("decline", 0, "decline", 0, True, 0),
        (None, None, "approve", 100, False, 50),
        (None, None, "refer", 0, True, 0),
    ],
)
def test_scoring_formulas(case):
    actual, payout, expected, amount, right, cost = case
    scored = compare(actual, payout, {"decision": expected, "payout": amount})
    assert scored["right"] is right
    assert scored["mistake_cost"] == cost


def test_all_selected_copies_are_scored_with_attributed_cost_time(client, monkeypatch):
    monkeypatch.setattr(runs, "now", lambda: "2026-11-09T00:00:00+00:00")
    run = evaluation(client)
    ids = [m["claim_id"] for m in run["members"]]
    expected = [
        ("approve", 100),
        ("approve", 100),
        ("decline", 0),
        ("approve", 220),
        ("decline", 0),
    ]
    with engine().begin() as db:
        for claim_id, (decision, payout) in zip(ids, expected, strict=True):
            source_id = db.execute(
                select(claims.c.source_id).where(claims.c.claim_id == claim_id)
            ).scalar_one()
            db.execute(
                update(evaluation_labels)
                .where(
                    evaluation_labels.c.set_name == "evaluation",
                    evaluation_labels.c.source_id == source_id,
                )
                .values(record={"decision": decision, "payout": payout})
            )
    client.post(f"/api/claims/{ids[0]}/start", json={"thread_id": "failed-thread"})
    client.post(
        f"/api/claims/{ids[0]}/model-calls",
        json={
            "reply_id": "first",
            "run_id": run["run_id"],
            "input_tokens": 1,
            "output_tokens": 2,
            "total_tokens": 3,
            "cost": 0.0002,
        },
    )
    client.put(
        f"/api/claims/{ids[0]}/work-slices/failed-work",
        json={"run_id": run["run_id"], "seconds": 10},
    )
    client.post(
        f"/api/claims/{ids[0]}/failure", json={"reason": "A failed first attempt"}
    )
    actual = [
        (0, "approve", 101),
        (1, "decline", 0),
        (2, "refer", 0),
        (4, "approve", 1350),
    ]
    for index, decision, payout in actual:
        claim_id = ids[index]
        client.post(f"/api/claims/{claim_id}/start", json={"thread_id": f"t{index}"})
        client.post(
            f"/api/claims/{claim_id}/decision",
            json={
                "decision": decision,
                "payout": payout,
                "section": "3",
                "reason": "Stub rationale",
            },
        )
    for reply_id, cost in (("second", 0.0003),):
        client.post(
            f"/api/claims/{ids[0]}/model-calls",
            json={
                "reply_id": reply_id,
                "run_id": run["run_id"],
                "input_tokens": 1,
                "output_tokens": 2,
                "total_tokens": 3,
                "cost": cost,
            },
        )
    client.put(
        f"/api/claims/{ids[0]}/work-slices/work",
        json={"run_id": run["run_id"], "seconds": 5},
    )
    monkeypatch.setattr(runs, "now", lambda: "2026-11-09T00:00:30+00:00")
    client.post("/api/runs/stop")
    result = client.get(f"/api/evaluations/{run['run_id']}").json()
    assert result["status"] == "stopped"
    assert result["seconds"] == 30
    assert result["scores"] == {
        "grade": 0,
        "decision_accuracy": 0.2,
        "accuracy": 0.2,
        "cost_of_mistakes": 2951,
        "straight_through": 0.6,
        "model_cost_per_claim": 0.0001,
        "seconds_per_claim": 3,
    }
    assert result["comparisons"][3]["decision"] is None
    assert result["comparisons"][4]["claim_status"] == "waiting_for_sign_off"
    assert client.get("/api/evaluations").json()[0]["run_id"] == run["run_id"]
    assert client.get("/api/runs/latest").json() is None


def test_evaluation_reads_require_an_evaluation(client):
    run_id = client.post("/api/runs", json=configuration()).json()["run_id"]
    assert client.get(f"/api/evaluations/{run_id}").status_code == 404
    assert client.get("/api/evaluations/missing").status_code == 404


def test_zero_work_scores_are_defined(client):
    with engine().begin() as db:
        db.execute(
            delete(evaluation_sources).where(
                evaluation_sources.c.set_name == "held_out"
            )
        )
    run = evaluation(client, "held_out")
    result = client.get(f"/api/evaluations/{run['run_id']}").json()
    assert result["members"] == []
    assert result["scores"]["grade"] is None
    assert all(
        value == 0 for name, value in result["scores"].items() if name != "grade"
    )
