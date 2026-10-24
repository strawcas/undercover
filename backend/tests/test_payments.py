import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.claims import ClaimError, Claims
from backend.main import create_app
from backend.models import DecisionInput
from backend.storage import Store
from backend.tests.conftest import decision_payload


def approve(repository, amount):
    claims = Claims(repository)
    thread = claims.start("CLM-1001")
    claims.decide(
        "CLM-1001",
        DecisionInput.model_validate(decision_payload(thread, payout=amount)),
    )
    return claims


@pytest.mark.parametrize("amount", ["999.99", "1000.00", "1000.01"])
def test_payment_boundary(client, repository, amount):
    approve(repository, amount)
    response = client.post("/api/claims/CLM-1001/pay", json={"amount": amount})
    if Decimal(amount) > 1000:
        assert response.status_code == 409
        assert client.get("/api/claims/CLM-1001").json()["payment"] is None
    else:
        assert response.status_code == 201
        assert response.json()["amount"] == amount
        assert client.get("/api/claims").json()[0]["payment_status"] == "paid"


def test_sign_off_and_payment_persist(client, repository):
    approve(repository, "1250.00")
    assert (
        client.post("/api/claims/CLM-1001/pay", json={"amount": "1250"}).status_code
        == 409
    )
    signed = client.post("/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"})
    assert signed.status_code == 201
    assert signed.json()["amount"] == "1250.00"
    assert signed.json()["handler_name"] == "Dana Whitfield"
    assert signed.json()["created_at"]
    assert client.get("/api/claims").json()[0]["payment_status"] == "eligible"
    paid = client.post("/api/claims/CLM-1001/pay", json={"amount": "1250"})
    assert paid.status_code == 201
    assert paid.json()["simulated"] is True
    assert (
        client.post("/api/claims/CLM-1001/pay", json={"amount": "1250"}).status_code
        == 409
    )
    assert (
        client.post(
            "/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"}
        ).status_code
        == 409
    )
    repository.initialize()
    detail = Claims(repository).detail("CLM-1001")
    assert detail["payment"] == paid.json()
    assert detail["sign_off"] == signed.json()
    assert len(Claims(repository).events("CLM-1001")) == 4


@pytest.mark.parametrize("amount", ["0", "-1", "1.001", "NaN"])
def test_invalid_payment(client, amount):
    assert (
        client.post("/api/claims/CLM-1001/pay", json={"amount": amount}).status_code
        == 422
    )


def test_payment_conflicts(client, repository):
    assert (
        client.post("/api/claims/CLM-1001/pay", json={"amount": "1"}).status_code == 409
    )
    approve(repository, "500")
    assert (
        client.post("/api/claims/CLM-1001/pay", json={"amount": "499"}).status_code
        == 409
    )
    assert (
        client.post(
            "/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"}
        ).status_code
        == 409
    )
    assert client.get("/api/claims/CLM-1001").json()["payment"] is None


def test_invalid_handler(client, repository):
    approve(repository, "1250")
    assert (
        client.post(
            "/api/claims/CLM-1001/sign-off", json={"handler_id": "missing"}
        ).status_code
        == 404
    )
    handlers = repository.record("settings", "handlers")
    handlers[0]["can_approve_over_limit"] = False
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE records SET body=? WHERE kind='settings' AND id='handlers'",
            (json.dumps(handlers),),
        )
    assert (
        client.post(
            "/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"}
        ).status_code
        == 409
    )


def test_competing_payments(repository):
    claims = approve(repository, "500")

    def pay_once(_index):
        try:
            claims.pay("CLM-1001", Decimal("500"))
        except ClaimError as error:
            return error.status
        return 201

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(pay_once, range(2))) == [201, 409]
    assert (
        len(
            [
                event
                for event in claims.events("CLM-1001")
                if event["action"] == "Simulated payment"
            ]
        )
        == 1
    )


def test_competing_runs(repository):
    claims = Claims(repository)
    claims.start("CLM-1001")
    with pytest.raises(ClaimError, match="already running"):
        claims.start("CLM-1001")
    with pytest.raises(ClaimError, match="not found"):
        claims.start("missing")


def test_financial_records_survive_a_new_app_and_store(repository):
    claims = approve(repository, "1250.00")
    claims.sign_off("CLM-1001", "HND-01")
    claims.pay("CLM-1001", Decimal("1250.00"))
    original = claims.detail("CLM-1001")
    claims.finish("CLM-1001", original["thread_id"])
    original_events = claims.events("CLM-1001")
    with TestClient(create_app(Store(repository.path), auto_run=False)) as restarted:
        saved = restarted.get("/api/claims/CLM-1001").json()
        assert saved["decision"] == original["decision"]
        assert saved["sign_off"] == original["sign_off"]
        assert saved["payment"] == original["payment"]
        assert saved["run_active"] is False
        assert restarted.get("/api/claims/CLM-1001/events").json() == original_events
        assert (
            restarted.post(
                "/api/claims/CLM-1001/pay", json={"amount": "1250"}
            ).status_code
            == 409
        )
