"""Versions and settings keep business authority out of model proposals."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update
from sqlalchemy.dialects import postgresql

from backend import configurations
from backend.configuration_models import ConfigurationProposal
from backend.database import decisions, engine, seed, settings
from backend.main import app
from backend.models import Handler
from backend.settings import decision_limit
from backend.tests.auth_support import authorization
from backend.tests.claim_requests import decide, pay, sign_off, start
from backend.tests.run_requests import start as start_run


def test_initial_versions_and_score_free_reads(client):
    value = client.get("/api/configurations/current").json()
    assert value["version"] == 1
    assert value["model"] == "openai/gpt-6-luna"
    assert value["reasoning_effort"] == "low"
    assert value["instructions"].startswith("# UnderCover Agent")
    assert "Contents of the claims manual" in value["prompt"]
    assert not {"scores", "held_out_scores", "comparisons"} & value.keys()
    assert client.get("/api/configurations/99").status_code == 404
    history = client.get("/api/configurations").json()
    assert history[0]["held_out_scores"] is None
    assert "+# UnderCover Agent" in history[0]["diff"]
    with engine().connect() as db:
        recipe = configurations.get_recipe(db, 1)
        assert recipe.recipe.startswith("1. Call read_results")
        assert "Keep this as the last step" in recipe.recipe


def test_approved_candidate_is_immutable_and_not_current_until_kept(client):
    handler = Handler(**client.get("/api/me").json())
    proposal = ConfigurationProposal(
        instructions="Apply policy rules to each claim.",
        model="openai/gpt-6-luna",
        reasoning_effort="medium",
        reason="Clearer objectives",
    )
    with engine().begin() as db:
        candidate = configurations.append_configuration(
            db, proposal, configurations.Approval(1, handler, "Improver")
        )
    assert candidate.version == 2
    assert candidate.approved_by == handler.name
    assert client.get("/api/configurations/current").json()["version"] == 1
    run = start_run(client).json()
    assert run["configuration_version"] == 1
    assert run["model"] == "openai/gpt-6-luna"
    with engine().begin() as db:
        configurations.activate_configuration(db, 2)
    assert (
        client.get("/api/configurations/current").json()["reasoning_effort"] == "medium"
    )
    assert client.get(f"/api/runs/{run['run_id']}").json()["configuration_version"] == 1
    history = client.get("/api/configurations").json()
    assert [value["version"] for value in history] == [2, 1]
    assert "+Apply policy rules" in history[0]["diff"]
    seed(engine())
    assert client.get("/api/configurations/current").json()["version"] == 1
    assert len(client.get("/api/configurations").json()) == 1


def test_free_model_is_persisted_at_seed_time(client, monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    seed(engine())
    configuration = client.get("/api/configurations/current").json()
    assert (configuration["model"], configuration["reasoning_effort"]) == (
        "openrouter/free",
        "default",
    )
    allowed = client.get("/api/settings").json()["models_allowed"]
    assert allowed == {
        "openrouter/free": {
            "reasoning_efforts": ["default"],
            "input_price": 0,
            "output_price": 0,
        }
    }


def test_settings_defaults_audit_and_authority(client):
    initial = client.get("/api/settings").json()
    values = initial["values"]
    assert values["payout_approval_limit"] == 1000
    assert values["spending_limit"] == 0.5
    assert values["concurrency"] == 25
    assert values["tolerance"]["cost_per_claim"] == 1
    assert initial["models_allowed"]["openai/gpt-6-luna"]["input_price"] == 0.1
    updated = values | {"spending_limit": 0.4}
    assert (
        client.put(
            "/api/settings", json=updated, headers=authorization("HND-02")
        ).status_code
        == 403
    )
    assert (
        client.get("/api/settings", headers=authorization("HND-02")).status_code == 200
    )
    result = client.put("/api/settings", json=updated).json()
    [change] = result["changes"]
    assert change["handler"] == "Dana Whitfield"
    assert change["before"] == values
    assert change["after"] == updated
    assert change["changed_at"]
    assert len(client.put("/api/settings", json=updated).json()["changes"]) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("concurrency", 0),
        ("concurrency", 26),
        ("spending_limit", -1),
        ("payout_approval_limit", -1),
        ("objectives", {"accuracy": 1.01, "grade": 1}),
        (
            "noise",
            {
                "accuracy": 0.1,
                "grade": -1,
                "cost_per_claim": 0.1,
                "seconds_per_claim": 0.1,
            },
        ),
    ],
)
def test_settings_validation(client, field, value):
    values = client.get("/api/settings").json()["values"]
    assert client.put("/api/settings", json=values | {field: value}).status_code == 422
    assert client.get("/api/settings").json()["changes"] == []


def test_a_raised_limit_keeps_held_decisions_and_a_lowered_limit_blocks_agent_payment(
    client,
):
    start(client, "CLM-1001")
    decide(client, "CLM-1001", "approve", 1350)
    values = client.get("/api/settings").json()["values"]
    client.put("/api/settings", json=values | {"payout_approval_limit": 2000})
    assert pay(client, "CLM-1001", 1350).status_code == 403
    assert "$1,000.00" in pay(client, "CLM-1001", 1350).json()["detail"]
    start(client, "CLM-1002")
    assert decide(client, "CLM-1002", "approve", 1350).json()["status"] == "in_progress"
    client.put("/api/settings", json=values | {"payout_approval_limit": 500})
    refused = pay(client, "CLM-1002", 1350)
    assert refused.status_code == 403
    assert "$500.00" in refused.json()["detail"]
    assert client.get("/api/claims/CLM-1002").json()["sign_off_available"]
    signed = sign_off(client, "CLM-1002", "HND-01").json()
    assert signed["payment"]["amount"] == 1350
    assert not signed["sign_off_available"]
    start(client, "CLM-1003")
    assert (
        decide(client, "CLM-1003", "approve", 501).json()["status"]
        == "waiting_for_sign_off"
    )


def test_model_catalogue_cannot_be_changed_from_settings(client):
    values = client.get("/api/settings").json()["values"]
    assert (
        client.put("/api/settings", json=values | {"models_allowed": {}}).status_code
        == 422
    )


def test_current_ceiling_also_applies_to_the_amount_saved_in_cents(client):
    claim_id = "CLM-1001"
    values = client.get("/api/settings").json()["values"]
    client.put("/api/settings", json=values | {"payout_approval_limit": 2000})
    start(client, claim_id)
    assert (
        decide(client, claim_id, "approve", 1000.01).json()["status"] == "in_progress"
    )
    client.put("/api/settings", json=values | {"payout_approval_limit": 1000.006})
    assert pay(client, claim_id, 1000.006).status_code == 403
    assert client.get(f"/api/claims/{claim_id}").json()["payment"] is None


def test_settings_write_requires_a_session(client):
    values = client.get("/api/settings").json()["values"]
    assert TestClient(app).put("/api/settings", json=values).status_code == 401


def test_invalid_proposal_model_is_not_saved(client):
    handler = Handler(**client.get("/api/me").json())
    with engine().begin() as db:
        for model, effort in [("unknown/model", "low"), ("openai/gpt-6-luna", "high")]:
            with pytest.raises(HTTPException, match="allowed models"):
                configurations.append_configuration(
                    db,
                    ConfigurationProposal(
                        instructions="A general goal",
                        model=model,
                        reasoning_effort=effort,
                        reason="Test",
                    ),
                    configurations.Approval(1, handler, "Improver"),
                )
        assert len(configurations.recipe_history(db)) == 1
    assert len(client.get("/api/configurations").json()) == 1


def test_decision_captures_limit_at_insert_even_if_a_setting_changes(client):
    start(client, "CLM-1001")
    triggered = []
    database = engine()

    def lower_limit(connection, cursor, statement, *args):
        if statement.startswith("INSERT INTO decisions") and not triggered:
            triggered.append(True)
            connection.execute(update(settings).values(payout_approval_limit=500))

    event.listen(database, "before_cursor_execute", lower_limit)
    try:
        result = decide(client, "CLM-1001", "approve", 750)
    finally:
        event.remove(database, "before_cursor_execute", lower_limit)
    assert triggered
    assert result.json()["status"] == "waiting_for_sign_off"
    with database.connect() as db:
        assert db.execute(select(decisions.c.approval_limit)).scalar_one() == 500
    assert "FOR SHARE" in str(decision_limit().compile(dialect=postgresql.dialect()))
