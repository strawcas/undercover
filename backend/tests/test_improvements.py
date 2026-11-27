"""Immutable round snapshots, restricted evidence and audited activation."""

import pytest
from sqlalchemy import select, update

from backend import evaluations, improvements
from backend.database import (
    engine,
    evaluation_labels,
    evaluation_sources,
    round_model_calls,
    rounds,
    settings,
)
from backend.tests.auth_support import authorization
from backend.tests.improvement_support import (
    baseline,
    decide,
    freeze,
    new_round,
    proposal,
    scores,
)


def test_round_snapshot_baseline_and_one_open_round(client):
    current = new_round(client)
    assert current["improver_model"] == "openai/gpt-6-luna"
    assert current["improver_effort"] == "medium"
    assert current["settings"]["concurrency"] == 25
    assert current["thread_id"] != current["round_id"]
    assert client.post("/api/rounds").status_code == 409
    with engine().begin() as db:
        assert improvements.baseline(db, current["round_id"])["missing"] == [
            "evaluation",
            "held_out",
            "held_out",
        ]
        db.execute(
            update(settings).values(
                improvement=current["settings"] | {"spending_limit": 2}
            )
        )
    freeze(current["round_id"], set_name="evaluation")
    freeze(current["round_id"])
    with engine().begin() as db:
        assert improvements.baseline(db, current["round_id"])["missing"] == ["held_out"]
        assert (
            improvements.get_round(db, current["round_id"]).settings.spending_limit
            == 0.5
        )


def test_rejection_keeps_versions_approval_creates_candidate_and_rule_activates(client):
    current = new_round(client)
    baseline(client, current)
    path = f"/api/improver/rounds/{current['round_id']}"
    pending = proposal(client, current)
    assert client.post(path + "/proposals", json=pending).status_code == 200
    assert client.get("/api/configurations/current").json()["version"] == 1
    rejected = decide(
        client, current, pending, "reject", "Keep the instructions general"
    )
    assert (
        rejected.json()["proposals"][0]["feedback"] == "Keep the instructions general"
    )
    assert len(client.get("/api/configurations").json()) == 1
    pending = proposal(client, current, "replacement")
    client.post(path + "/proposals", json=pending).raise_for_status()
    accepted = decide(client, current, pending)
    assert accepted.status_code == 200
    assert decide(client, current, pending).status_code == 409
    candidate = accepted.json()["candidate_version"]
    assert candidate == 2
    assert client.get("/api/configurations/current").json()["version"] == 1
    for name in ("evaluation", "held_out", "held_out"):
        freeze(current["round_id"], candidate, name, scores(0.9, 2))
    verdict = client.post(path + "/verdict").json()
    assert (
        verdict["verdict"],
        verdict["before"]["accuracy"],
        verdict["after"]["accuracy"],
    ) == ("kept", 0.7, 0.9)
    assert client.get("/api/configurations/current").json()["version"] == candidate
    report = client.get(path + "/report").json()
    assert report["configuration"]["version"] == candidate
    assert report["scores"][str(candidate)]["evaluation"]["accuracy"] == 0.9
    client.post(
        path + "/finish", json={"stopped": True, "reason": "Stopped after verdict"}
    ).raise_for_status()
    assert client.post(path + "/verdict").status_code == 409
    assert client.get("/api/configurations/current").json()["version"] == candidate


def test_invalid_proposals_recipe_versions_and_no_candidate_completion(client):
    current = new_round(client)
    baseline(client, current)
    path = f"/api/improver/rounds/{current['round_id']}"
    invalid = proposal(client, current, model="not-allowed")
    assert "allowed" in client.post(path + "/validate", json=invalid).json()["refusal"]
    assert client.post(path + "/proposals", json=invalid).status_code == 422
    assert client.get(path).json()["proposals"] == []
    pending = proposal(client, current, kind="recipe")
    client.post(path + "/proposals", json=pending).raise_for_status()
    assert decide(client, current, pending).status_code == 200
    assert len(client.get("/api/recipes").json()) == 2
    assert client.get(path).json()["recipe_version"] == 1
    client.post(
        path + "/finish", json={"stopped": False, "reason": "No change needed"}
    ).raise_for_status()
    next_round = new_round(client)
    assert next_round["recipe_version"] == 2
    assert next_round["thread_id"] != current["thread_id"]


def test_handler_stop_rejects_late_writes_without_global_recovery(client):
    current = new_round(client)
    baseline(client, current)
    path = f"/api/improver/rounds/{current['round_id']}"
    pending = proposal(client, current)
    client.post(path + "/proposals", json=pending).raise_for_status()
    assert client.post("/api/improver/recover").status_code == 404
    assert client.get(path).json()["status"] == "awaiting_configuration"
    decide(client, current, pending).raise_for_status()
    client.post(f"/api/rounds/{current['round_id']}/stop").raise_for_status()
    stopped = client.get(path).json()
    assert stopped["status"] == "stopped"
    assert stopped["verdict"] == "rolled_back"
    for suffix, body in (
        ("/progress", {"status": "working", "progress": 0}),
        ("/proposals", pending),
    ):
        assert (
            client.request(
                "PUT" if suffix == "/progress" else "POST", path + suffix, json=body
            ).status_code
            == 409
        )
    assert client.get("/api/configurations/current").json()["version"] == 1


def test_held_out_sentinels_never_enter_any_improver_report(client):
    current = new_round(client)
    baseline(client, current)
    with engine().begin() as db:
        for table in (evaluation_sources, evaluation_labels):
            db.execute(
                update(table)
                .where(table.c.set_name == "held_out")
                .values(record={"secret": "HELD_OUT_SENTINEL"})
            )
    report = client.get(f"/api/improver/rounds/{current['round_id']}/report")
    assert report.status_code == 200
    assert "HELD_OUT_SENTINEL" not in report.text
    assert len(report.json()["evaluation_examples"]) == 24
    assert report.json()["scores"]["1"]["evaluation"]["accuracy"] == 0.7
    assert report.json()["scores"]["1"]["held_out"]["grade"] == 1.5
    assert "absolute differences" in report.json()["rule"]
    for example in report.json()["evaluation_examples"]:
        assert example["summary"]
        assert {"same_grounds", "same_section"} <= example.keys()
    for endpoint in ("/api/rounds", "/api/configurations/current", "/api/recipes"):
        assert "HELD_OUT_SENTINEL" not in client.get(endpoint).text


@pytest.mark.parametrize(
    ("path", "body", "operation"),
    [
        ("/api/rounds", None, "work_round"),
        ("/api/rounds/unknown/stop", None, None),
        (
            "/api/rounds/unknown/proposals/p/decision",
            {"decision": "approve", "reason": None},
            None,
        ),
    ],
)
def test_handler_round_actions_require_senior_identity(
    client, agent_calls, path, body, operation
):
    for headers, expected in (
        ({"Authorization": ""}, 401),
        (authorization("HND-02"), 403),
    ):
        assert client.post(path, json=body, headers=headers).status_code == expected
    assert agent_calls == []
    assert client.post(path, json=body).status_code == (202 if operation else 404)
    assert [call["operation"] for call in agent_calls] == (
        [operation] if operation else []
    )


def test_budget_counts_idempotent_billing_and_rollback_without_tests(client):
    current = new_round(client)
    baseline(client, current)
    path = f"/api/improver/rounds/{current['round_id']}"
    pending = proposal(client, current)
    client.post(path + "/proposals", json=pending).raise_for_status()
    decide(client, current, pending).raise_for_status()
    usage = {
        "reply_id": "reply",
        "run_id": None,
        "input_tokens": 10,
        "output_tokens": 10,
        "total_tokens": 20,
        "cost": 0.5,
    }
    for _ in range(2):
        assert client.post(path + "/usage", json=usage).status_code == 200
    assert client.get(path).json()["spending"]["improver"] == 0.5
    assert client.post(path + "/admission").status_code == 409
    quote = client.post(
        path + "/quote", json={"configuration_version": 2, "set_name": "evaluation"}
    ).json()
    assert not quote["allowed"]
    assert quote["remaining"] == 0
    assert "remaining" in quote["reason"]
    assert client.post(path + "/verdict").json()["verdict"] == "rolled_back"
    client.post(
        path + "/finish", json={"stopped": True, "reason": "Budget"}
    ).raise_for_status()
    usage.update(reply_id="in-flight", cost=0.01)
    client.post(path + "/usage", json=usage).raise_for_status()
    with engine().connect() as db:
        assert len(db.execute(select(round_model_calls)).all()) == 2
        assert db.execute(select(rounds.c.status)).scalar_one() == "stopped"


def test_development_examples_choose_graded_correct_explanations_before_missing(
    client, monkeypatch
):
    current = new_round(client)
    identity = freeze(current["round_id"], set_name="evaluation")
    with engine().begin() as db:
        detail = evaluations.get_evaluation(db, identity)
        grades = [None, 3, 0.1, 1.2, 0.3, 0.4, 0.5]
        items = [
            item.model_copy(update={"right": True, "grade": grade})
            for item, grade in zip(detail.comparisons, grades, strict=False)
        ]
        monkeypatch.setattr(
            evaluations,
            "get_evaluation",
            lambda *args: detail.model_copy(update={"comparisons": items}),
        )
        examples = evaluations.development_examples(db, 1)
    assert [example["grade"] for example in examples] == [0.1, 0.3, 0.4, 0.5, 1.2]
