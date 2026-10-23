import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.models import Money, cents, dollars
from backend.storage import ROOT, SEED_FILES, Store, decode, encode


@pytest.fixture
def store(tmp_path):
    repository = Store(tmp_path / "pilot.db")
    repository.initialize()
    return repository


def test_seed_queue_and_relationships(store):
    source = json.loads((ROOT / "data/claims.json").read_text(encoding="utf-8"))
    claims = store.records("claim")
    assert {claim["claim_id"] for claim in claims} == {
        claim["claim_id"] for claim in source
    }
    assert len(claims) == 25
    assert {claim["status"] for claim in claims} == {"waiting"}
    assert {p["policy_id"] for p in store.records("policy")} == {
        claim["policy_id"] for claim in claims
    }
    assert {c["customer_id"] for c in store.records("customer")} == {
        claim["customer_id"] for claim in claims
    }
    for original, claim in zip(source, claims, strict=True):
        assert claim["documents"] == original["documents"]
        assert claim["story"] == original["story"]
        assert len(claim["items"]) == len(original["items"])
        for item, original_item in zip(claim["items"], original["items"], strict=True):
            assert Decimal(item["amount"]) == Decimal(str(original_item["amount"]))
    assert store.record("customer", "CUS-3001")["previous_claims"]


def test_settings(store):
    company = store.record("settings", "company")
    assert company["currency"] == "USD"
    assert company["payout_approval_limit"] == "1000.00"
    assert company["policy_wording_version"] == "2026.1"
    assert "repeat_claim_review" not in company
    assert "meal_allowance_without_receipts" not in company
    plans = store.record("settings", "plans")
    assert {plan["plan"] for plan in plans["plans"]} == {"economy", "premium", "first"}
    assert len(plans["add_ons"]) == 3
    assert len(store.record("settings", "handlers")) == 3
    assert "## 10. How we work out your payment" in store.record("settings", "wording")
    assert "two or" in store.record("settings", "instructions")


def test_only_allowlisted_source_files_are_read(tmp_path, monkeypatch):
    original = Path.read_text
    reads = []

    def checked_read(path, **kwargs):
        assert path.parent == ROOT / "data" or path == ROOT / "backend/instructions.md"
        if path.parent == ROOT / "data":
            assert path.name in SEED_FILES
            reads.append(path.name)
        return original(path, **kwargs)

    monkeypatch.setattr(Path, "read_text", checked_read)
    Store(tmp_path / "safe.db").initialize()
    assert set(reads) == set(SEED_FILES)


def test_reseed_preserves_work(store):
    with store.transaction() as connection:
        connection.execute(
            "UPDATE claims SET decision = ?, state = 'decided' WHERE id = ?",
            ('{"outcome":"refer"}', "CLM-1001"),
        )
        connection.execute(
            "INSERT INTO events VALUES (1, ?, ?, ?, ?, ?, ?)",
            ("CLM-1001", "thread-1", "decision", "completed", "Refer", "2026-01-01"),
        )
    reopened = Store(store.path)
    reopened.initialize()
    assert len(reopened.records("claim")) == 25
    with reopened.connect() as connection:
        row = connection.execute("SELECT * FROM claims WHERE id='CLM-1001'").fetchone()
        assert json.loads(row["decision"])["outcome"] == "refer"
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


@pytest.mark.parametrize("value", ["0", "0.01", "999.99", "1000", "1000.01"])
def test_exact_money(value):
    amount = TypeAdapter(Money).validate_python(value)
    assert Decimal(dollars(cents(amount))) == amount
    assert decode(encode({"amount": amount})) == {"amount": dollars(cents(amount))}


@pytest.mark.parametrize("value", ["-1", "0.001", "NaN", "Infinity"])
def test_invalid_money(value):
    with pytest.raises(ValidationError):
        TypeAdapter(Money).validate_python(value)


def test_missing_record(store):
    with pytest.raises(KeyError):
        store.record("claim", "missing")


def test_transaction_rolls_back(store):
    def failing_transaction():
        with store.transaction() as connection:
            connection.execute("DELETE FROM claims")
            raise RuntimeError("rollback")

    with pytest.raises(RuntimeError):
        failing_transaction()
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 25
