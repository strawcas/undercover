from sqlalchemy import insert, update

from backend.database import claims, decisions, engine, payments, runs


def test_customer_lookup_uses_original_records(client):
    assert client.get("/api/customers", params={"name": "mAyA"}).json() == [
        {"customer_id": "CUS-3001", "name": "Maya Patel"}
    ]
    assert client.get("/api/customers", params={"name": "%"}).json() == []
    customer = client.get("/api/customers/CUS-3001").json()
    assert "POL-2001" in customer["policy_ids"]
    assert "CLM-1001" in customer["claim_ids"]
    assert (
        client.get("/api/customers/CUS-3001/claims").json()[0]["claim_id"] == "CLM-0412"
    )
    assert client.get("/api/customers/missing").status_code == 404


def test_dispatch_and_evaluation_reads_are_kind_scoped(client):
    dispatch = client.post("/api/dispatches", json={"concurrency": 2}).json()["run_id"]
    reply = client.get(f"/api/dispatches/{dispatch}").json()
    assert reply["kind"] == "dispatch"
    assert reply["returned"] == []
    assert client.get(f"/api/evaluations/{dispatch}/measures").status_code == 404


def test_evaluation_measures_have_no_handler_labels(client):
    run_id = client.post(
        "/api/evaluations",
        json={
            "set_name": "held_out",
            "tiny": True,
            "concurrency": 2,
        },
    ).json()["run_id"]
    reply = client.get(f"/api/evaluations/{run_id}/measures")
    assert reply.status_code == 200
    assert "scores" in reply.json()
    assert "comparisons" not in reply.json()
    assert "handler_reason" not in reply.text
    assert client.get(f"/api/dispatches/{run_id}").status_code == 404


def test_payout_counts_use_one_limit_and_only_originals(client):
    path = "/api/settings/payout-coverage"
    initial = client.get(path).json()
    limit = initial["limit"]
    assert initial == {"limit": limit, "paid": 0, "signed_off": 0}
    with engine().begin() as db:
        db.execute(
            insert(runs).values(
                run_id="copy-run",
                kind="evaluation",
                concurrency=1,
                model="fake",
                configuration_version=1,
                status="finished",
                started_at="now",
            )
        )
        db.execute(
            update(claims)
            .where(claims.c.claim_id == "CLM-1004")
            .values(evaluation_id="copy-run")
        )
        for index, amount in enumerate(
            [limit + 1, limit + 5, limit, limit + 10], start=1
        ):
            claim_id = f"CLM-100{index}"
            db.execute(
                insert(payments).values(claim_id=claim_id, amount=amount, paid_at="now")
            )
            db.execute(
                insert(decisions).values(
                    claim_id=claim_id,
                    decision="approve",
                    payout=amount,
                    section="1",
                    reason="Covered",
                    decided_by="Agent",
                    decided_at="now",
                    manual_version="test",
                    approval_limit=limit,
                    signed_off_by="Dana" if index in (1, 3, 4) else None,
                )
            )
    assert client.get(path).json() == {"limit": limit, "paid": 2, "signed_off": 1}
