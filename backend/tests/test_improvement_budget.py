"""Model-specific estimates and complete round billing, without provider requests."""

import pytest
from sqlalchemy import insert, select, update

from backend import improvement_budget
from backend.database import (
    engine,
    evaluation_grades,
    model_calls,
    round_model_calls,
    rounds,
    run_claims,
    runs,
)
from backend.tests.improvement_support import (
    baseline,
    decide,
    freeze,
    new_round,
    proposal,
)


def observed_run(round_id, total, model="openai/gpt-6-luna"):
    identity = freeze(round_id)
    with engine().begin() as db:
        claim_id = (
            db.execute(
                select(run_claims.c.claim_id).where(run_claims.c.run_id == identity)
            )
            .scalars()
            .first()
        )
        db.execute(update(runs).where(runs.c.run_id == identity).values(model=model))
        db.execute(
            insert(model_calls).values(
                claim_id=claim_id,
                thread_id="cost-thread",
                reply_id=identity,
                run_id=identity,
                cost=total,
            )
        )
    return identity, claim_id


def test_quote_uses_maximum_model_observation_and_larger_price_ratio(client):
    current = new_round(client)
    identity = current["round_id"]
    with engine().begin() as db:
        cold = improvement_budget.quote(db, identity, "openai/gpt-6-luna", "evaluation")
        assert cold.estimate == pytest.approx(25 * 0.011)
        assert cold.allowed
    observed_run(identity, 0.025)
    observed_run(identity, 0.05)
    with engine().begin() as db:
        assert improvement_budget.observations(db)["openai/gpt-6-luna"] == 0.002
        assert improvement_budget.quote(
            db, identity, "openai/gpt-6-luna", "evaluation"
        ).estimate == pytest.approx(25 * 0.003)
        # Gemini's output price ratio is 5, larger than its input ratio of 3.
        assert improvement_budget.quote(
            db, identity, "google/gemini-3.5-flash-lite", "evaluation"
        ).estimate == pytest.approx(25 * 0.011)
    observed_run(identity, 0.0125, "google/gemini-3.5-flash-lite")
    with engine().begin() as db:
        assert improvement_budget.quote(
            db, identity, "google/gemini-3.5-flash-lite", "evaluation"
        ).estimate == pytest.approx(25 * 0.0015)


def test_missing_prices_and_usage_refuse_and_zero_prices_are_supported(client):
    current = new_round(client)
    identity = current["round_id"]
    observed_run(identity, 0.024)
    with engine().begin() as db:
        assert (
            improvement_budget.quote(db, identity, "missing", "evaluation").estimate
            is None
        )
        row = improvement_budget.lock_round(db, identity)
        record = dict(row["record"])
        record["models_allowed"][record["base_model"]]["input_price"] = 0
        db.execute(
            update(rounds).where(rounds.c.round_id == identity).values(record=record)
        )
        assert (
            improvement_budget.quote(
                db, identity, "google/gemini-3.5-flash-lite", "evaluation"
            ).estimate
            is None
        )
        record["models_allowed"][record["base_model"]]["output_price"] = 0
        record["improver_effort"] = "default"
        db.execute(
            update(rounds).where(rounds.c.round_id == identity).values(record=record)
        )
        assert improvement_budget.quote(
            db, identity, record["base_model"], "evaluation"
        ).allowed
        db.execute(
            insert(round_model_calls).values(
                round_id=identity, reply_id="unknown", cost=None, record={}
            )
        )
        assert not improvement_budget.quote(
            db, identity, record["base_model"], "evaluation"
        ).allowed


def test_round_total_includes_baseline_claims_judge_and_improver(client):
    current = new_round(client)
    identity, claim_id = observed_run(current["round_id"], 0.02)
    with engine().begin() as db:
        db.execute(
            insert(evaluation_grades).values(
                run_id=identity, claim_id=claim_id, record={"judge_cost": 0.01}
            )
        )
        db.execute(
            insert(round_model_calls).values(
                round_id=current["round_id"], reply_id="reply", cost=0.03, record={}
            )
        )
        total = improvement_budget.spending(db, current["round_id"], 0.5)
    assert (total.improver, total.claims, total.judge) == (0.03, 0.02, 0.01)
    assert total.total == pytest.approx(0.06)
    assert total.remaining == pytest.approx(0.44)


def test_sequence_refuses_when_one_run_fits_but_three_do_not(client):
    current = new_round(client)
    baseline(client, current)
    path = f"/api/improver/rounds/{current['round_id']}"
    pending = proposal(client, current)
    client.post(path + "/proposals", json=pending).raise_for_status()
    decide(client, current, pending).raise_for_status()
    # No model observation yet: each cold-start run fits $0.50; all three do not.
    one = client.post(
        path + "/quote", json={"configuration_version": 2, "set_name": "evaluation"}
    ).json()
    sequence = client.post(path + "/sequence-quote").json()
    assert one["allowed"]
    assert not sequence["allowed"]
    assert sequence["estimate"] == pytest.approx(0.825)
    assert sequence["remaining"] == 0.5
    assert "sequence refused" in client.post(path + "/verdict").json()["explanation"]
    with engine().connect() as db:
        assert (
            db.execute(
                select(runs.c.run_id).where(runs.c.configuration_version == 2)
            ).first()
            is None
        )
