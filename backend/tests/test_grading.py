"""Parallel, idempotent grading and whole-set version averages."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from time import monotonic

import pytest
from sqlalchemy import event, select, update

from backend import evaluations, judge
from backend.database import engine, runs, seed
from backend.judge_models import Grade
from backend.tests.claim_requests import decide, start
from backend.tests.evaluation_requests import evaluation


def decide_members(client, run, count=None):
    chosen = run["members"] if count is None else run["members"][:count]
    for member in chosen:
        claim_id = member["claim_id"]
        start(client, claim_id)
        decide(client, claim_id, "decline", 0)


def test_grade_failures_are_excluded_and_undecided_claims_get_f(client, monkeypatch):
    run = evaluation(client)
    decide_members(client, run, 2)
    states = []
    guard = Lock()

    def grade(state):
        with guard:
            first = not states
            states.append(state)
        if first:
            return Grade(
                grade=2,
                letter="B",
                same_grounds=0.7,
                same_section=0.3,
                judge_cost=0.0001,
            )
        return Grade(judge_cost=0.0002)

    monkeypatch.setattr(judge, "grade", grade)
    assert client.post(f"/api/runs/{run['run_id']}/finish").status_code == 200
    result = client.get(f"/api/evaluations/{run['run_id']}").json()
    assert len(states) == 2
    assert all(
        state["handler"]["reason"] and state["handler"]["reference"] for state in states
    )
    assert all(state["agent"]["section"] == "Section 3, Trip Delay" for state in states)
    assert result["scores"]["grade"] == 0.5
    assert result["judge_cost"] == pytest.approx(0.0003)
    assert result["llm_cost"] == 0
    assert sum(item["letter"] == "F" for item in result["comparisons"]) == 3
    assert sum(item["grade"] is None for item in result["comparisons"]) == 1
    assert client.post(f"/api/runs/{run['run_id']}/finish").status_code == 200
    assert len(states) == 2
    with engine().connect() as db:
        assert (
            db.execute(
                select(runs.c.scores).where(runs.c.run_id == run["run_id"])
            ).scalar_one()["grade"]
            == 0.5
        )


def test_all_ungraded_and_free_mode_have_no_grade(client, monkeypatch):
    run = evaluation(client)
    decide_members(client, run)
    monkeypatch.setattr(judge, "grade", lambda state: Grade())
    client.post(f"/api/runs/{run['run_id']}/finish")
    result = client.get(f"/api/evaluations/{run['run_id']}").json()
    assert result["scores"]["grade"] is None
    assert result["judge_cost"] is None
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    seed(engine())
    free = evaluation(client)

    def no_judge(state):
        raise AssertionError("Free mode must not call a judge")

    monkeypatch.setattr(judge, "grade", no_judge)
    client.post(f"/api/runs/{free['run_id']}/finish")
    result = client.get(f"/api/evaluations/{free['run_id']}").json()
    assert result["scores"]["grade"] is None
    assert result["judge_cost"] == 0
    assert all(item["grade"] is None for item in result["comparisons"])


def test_grading_is_parallel_without_checked_out_database_connections(
    client, monkeypatch
):
    run = evaluation(client, tiny=False)
    decide_members(client, run)
    barrier = Barrier(25)
    guard = Lock()
    checked_out = [0]

    def checkout(*args):
        with guard:
            checked_out[0] += 1

    def checkin(*args):
        with guard:
            checked_out[0] -= 1

    def grade(state):
        assert checked_out[0] == 0
        barrier.wait(timeout=5)
        return Grade(grade=1, letter="C", judge_cost=0.001)

    database = engine()
    event.listen(database, "checkout", checkout)
    event.listen(database, "checkin", checkin)
    monkeypatch.setattr(judge, "grade", grade)
    began = monotonic()
    try:
        assert client.post(f"/api/runs/{run['run_id']}/finish").status_code == 200
    finally:
        event.remove(database, "checkout", checkout)
        event.remove(database, "checkin", checkin)
    assert monotonic() - began < 20
    assert checked_out[0] == 0


def test_concurrent_finish_is_refused_and_sequential_replay_is_idempotent(
    client, monkeypatch
):
    run = evaluation(client)
    decide_members(client, run, 1)
    entered, release = Event(), Event()
    calls = []

    def grade(state):
        calls.append(state)
        entered.set()
        assert release.wait(timeout=5)
        return Grade(grade=1, letter="C", judge_cost=0.001)

    monkeypatch.setattr(judge, "grade", grade)
    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(client.post, f"/api/runs/{run['run_id']}/finish")
        try:
            assert entered.wait(timeout=5)
            assert client.post(f"/api/runs/{run['run_id']}/finish").status_code == 409
        finally:
            release.set()
        assert first.result().status_code == 200
    assert client.post(f"/api/runs/{run['run_id']}/finish").status_code == 200
    assert len(calls) == 1


def test_stopping_during_grading_does_not_revive_an_evaluation(client, monkeypatch):
    run = evaluation(client)
    decide_members(client, run, 1)
    entered, release = Event(), Event()

    def grade(state):
        entered.set()
        assert release.wait(timeout=5)
        return Grade(grade=2, letter="B", judge_cost=0.001)

    monkeypatch.setattr(judge, "grade", grade)
    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(client.post, f"/api/runs/{run['run_id']}/finish")
        try:
            assert entered.wait(timeout=5)
            client.post("/api/runs/stop")
        finally:
            release.set()
        assert first.result().json()["status"] == "stopped"
    assert client.get(f"/api/evaluations/{run['run_id']}").json()["judge_cost"] == 0.001


def score_values(accuracy, grade, cost, seconds):
    return {
        "accuracy": accuracy,
        "decision_accuracy": accuracy,
        "grade": grade,
        "cost_of_mistakes": 0,
        "straight_through": accuracy,
        "model_cost_per_claim": cost,
        "seconds_per_claim": seconds,
    }


def test_version_averages_only_completed_full_round_evaluations(client):
    first = evaluation(client, "held_out", False, "round-one")
    second = evaluation(client, "held_out", False, "round-two")
    excluded = [
        evaluation(client, "held_out", False),
        evaluation(client, "held_out", True, "round-three"),
        evaluation(client, "held_out", False, "stopped"),
        evaluation(client, "evaluation", False, "other-set"),
    ]
    with engine().begin() as db:
        for run in [first, second, *excluded]:
            db.execute(
                update(runs)
                .where(runs.c.run_id == run["run_id"])
                .values(status="finished", scores=score_values(1, 3, 1, 100))
            )
        db.execute(
            update(runs)
            .where(runs.c.run_id == first["run_id"])
            .values(scores=score_values(0.6, 1, 0.001, 10))
        )
        db.execute(
            update(runs)
            .where(runs.c.run_id == second["run_id"])
            .values(scores=score_values(0.8, 2, 0.003, 30))
        )
        db.execute(
            update(runs)
            .where(runs.c.run_id == excluded[2]["run_id"])
            .values(status="stopped")
        )
        result = evaluations.version_scores(db, 1, "held_out")
        assert result is not None
        assert (
            result.accuracy,
            result.grade,
            result.model_cost_per_claim,
            result.seconds_per_claim,
        ) == (0.7, 1.5, 0.002, 20)
        assert len(evaluations.version_evaluations(db, 1, "held_out")) == 2
        assert evaluations.version_scores(db, 99, "held_out") is None
    assert (
        client.get("/api/configurations").json()[0]["held_out_scores"]["grade"] == 1.5
    )
