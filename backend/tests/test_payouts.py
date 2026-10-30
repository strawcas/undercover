import json
from decimal import Decimal

import pytest

from backend.payouts import PayoutInput, calculate


def line(kind="expense", amount="100", quantity=1, nights=0, hours="0"):
    return dict(
        description="Synthetic covered cost",
        kind=kind,
        amount=amount,
        quantity=quantity,
        nights=nights,
        hours=hours,
    )


def request(lines, benefit="trip_delay", paid_by_others="0"):
    return PayoutInput.model_validate(
        dict(lines=lines, benefit=benefit, paid_by_others=paid_by_others)
    )


def set_record(store, kind, key, value):
    with store.transaction() as connection:
        connection.execute(
            "UPDATE records SET body=? WHERE kind=? AND id=?",
            (json.dumps(value), kind, key),
        )


@pytest.mark.parametrize(
    ("benefit", "maximum"),
    [
        ("trip_delay", "800.00"),
        ("baggage_delay", "300.00"),
        ("baggage", "1500.00"),
        ("passport", "250.00"),
        ("emergency_medical", "100000.00"),
        ("trip_cancellation", "123.45"),
        ("trip_interruption", "123.45"),
    ],
)
def test_benefit_and_trip_maximum(repository, benefit, maximum):
    claim = repository.record("claim", "CLM-1001")
    policy = repository.record("policy", claim["policy_id"])
    policy["trip"]["trip_cost"] = "123.45"
    set_record(repository, "policy", policy["policy_id"], policy)
    result = calculate(
        repository, "CLM-1001", request([line(amount="200000")], benefit)
    )
    assert result.payout == maximum
    assert sum(Decimal(step.amount) for step in result.calculation) == Decimal(maximum)


@pytest.mark.parametrize(
    ("lines", "benefit", "expected"),
    [
        ([line("item", "999", quantity=2)], "baggage", "550.00"),
        ([line("cash", "200"), line("cash", "200")], "baggage", "50.00"),
        ([line("hotel", "9999", nights=1)], "trip_delay", "350.00"),
        ([line("meals_without_receipts", "999", hours="25")], "trip_delay", "70.00"),
        ([line(amount="0.29"), line(amount="0.01")], "trip_delay", "0.30"),
    ],
)
def test_line_limits_and_exact_money(repository, lines, benefit, expected):
    assert calculate(repository, "CLM-1001", request(lines, benefit)).payout == expected


def test_electronics_rooms_offsets_and_zero_floor(repository):
    claim = repository.record("claim", "CLM-1001")
    policy = repository.record("policy", claim["policy_id"])
    policy["add_ons"] = ["electronics_plus"]
    policy["travelers"] = ["A", "B", "C"]
    set_record(repository, "policy", policy["policy_id"], policy)
    assert (
        calculate(
            repository, "CLM-1001", request([line("electronics", "1500")], "baggage")
        ).payout
        == "1150.00"
    )
    assert (
        calculate(
            repository, "CLM-1001", request([line("hotel", "900", nights=1)])
        ).payout
        == "700.00"
    )
    result = calculate(
        repository, "CLM-1001", request([line(amount="100")], "baggage", "75")
    )
    assert result.payout == "0.00"
    assert result.calculation[-1].amount == "-25.00"
    result = calculate(
        repository, "CLM-1001", request([line(amount="50")], paid_by_others="100")
    )
    assert result.payout == "0.00"
    assert result.calculation[-1].amount == "50.00"


def test_api_settings_boundary_and_no_writes(client, repository):
    path = "/api/claims/CLM-1001/payout-calculation"
    for amount, needed in [("1000", False), ("1000.01", True)]:
        body = request([line(amount=amount)], "emergency_medical").model_dump(
            mode="json"
        )
        body["lines"][0]["amount"] = str(Decimal(amount) + 50)
        result = client.post(path, json=body)
        assert result.status_code == 200
        assert result.json()["needs_sign_off"] is needed
    company = repository.record("settings", "company") | {
        "payout_approval_limit": "100"
    }
    set_record(repository, "settings", "company", company)
    assert client.post(
        path, json=request([line(amount="101")]).model_dump(mode="json")
    ).json()["needs_sign_off"]
    detail = client.get("/api/claims/CLM-1001").json()
    assert detail["decision"] is None
    assert detail["payment"] is None
    assert client.get("/api/claims/CLM-1001/events").json() == []
    assert (
        client.post("/api/claims/missing/payout-calculation", json=body).status_code
        == 404
    )


@pytest.mark.parametrize(
    "change",
    [
        {"amount": "-1"},
        {"quantity": 0},
        {"nights": -1},
        {"hours": "NaN"},
        {"kind": "hotel"},
        {"kind": "meals_without_receipts"},
    ],
)
def test_payout_validation(client, change):
    body = request([line()]).model_dump(mode="json")
    body["lines"][0].update(change)
    assert (
        client.post("/api/claims/CLM-1001/payout-calculation", json=body).status_code
        == 422
    )


def test_nested_payout_inputs_are_required(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    for name in ["PayoutInput", "PayoutLine"]:
        assert set(schemas[name]["properties"]) == set(schemas[name]["required"])
