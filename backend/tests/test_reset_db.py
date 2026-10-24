import json
import socket
from decimal import Decimal

import pytest

from backend.claims import Claims
from backend.local import local_backend
from backend.ownership import DatabaseOwner
from backend.reset_db import main, rebuild
from backend.storage import ROOT, SEED_FILES, Store
from backend.tests.test_payments import approve


@pytest.fixture
def copied_data(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    for name in SEED_FILES:
        (directory / name).write_text(
            (ROOT / "data" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return directory


def test_rebuild_clears_all_work_and_preserves_other_files(repository, tmp_path):
    claims = approve(repository, "1250")
    claims.sign_off("CLM-1001", "HND-01")
    claims.pay("CLM-1001", Decimal("1250"))
    claims.finish("CLM-1001", claims.detail("CLM-1001")["thread_id"])
    failed = claims.start("CLM-1002")
    claims.finish("CLM-1002", failed, "Synthetic failure")
    untouched = tmp_path / "another.db"
    untouched.write_bytes(b"Unrelated database")
    source = json.loads((ROOT / "data/claims.json").read_text(encoding="utf-8"))
    assert rebuild(repository.path) == len(source)
    assert rebuild(repository.path) == len(source)
    assert untouched.read_bytes() == b"Unrelated database"
    for row in Claims(repository).queue():
        detail = claims.detail(row["claim_id"])
        assert detail["status"] == "waiting"
        assert detail["thread_id"] is None
        assert not detail["run_active"]
        assert all(detail[key] is None for key in ("decision", "payment", "sign_off"))
        assert claims.events(row["claim_id"]) == []


def test_rebuild_creates_missing_database_and_loads_additions(tmp_path, copied_data):
    source = copied_data / "claims.json"
    records = json.loads(source.read_text(encoding="utf-8"))
    records.insert(0, records[0] | {"claim_id": "TEST-NEW-CLAIM"})
    source.write_text(json.dumps(records), encoding="utf-8")
    path = tmp_path / "fresh.sqlite"
    assert rebuild(path, copied_data) == len(records)
    assert Claims(Store(path)).queue()[0]["claim_id"] == "TEST-NEW-CLAIM"
    assert not list(tmp_path.glob(".fresh.sqlite-*.db"))


@pytest.mark.parametrize("invalid", ["json", "missing", "policy", "customer", "plan"])
def test_invalid_source_preserves_existing_database(repository, copied_data, invalid):
    before = repository.path.read_bytes()
    path = copied_data / "claims.json"
    if invalid == "json":
        path.write_text("not json", encoding="utf-8")
    elif invalid == "missing":
        path.unlink()
    else:
        name = {"policy": "policies.json", "customer": "customers.json"}.get(
            invalid, "plans.json"
        )
        value = {"plans": [], "add_ons": []} if invalid == "plan" else []
        (copied_data / name).write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        rebuild(repository.path, copied_data)
    assert repository.path.read_bytes() == before
    assert not list(repository.path.parent.glob(f".{repository.path.name}-*.db"))


def test_main_honors_configuration_and_active_ownership(
    repository, monkeypatch, capsys
):
    monkeypatch.setenv("UNDERCOVER_DB_PATH", str(repository.path))
    with DatabaseOwner(repository.path):
        assert main() == 1
    assert "already in use" in capsys.readouterr().err
    assert main() == 0
    output = capsys.readouterr().out
    assert str(repository.path) in output
    assert "Queued 25 claims" in output


def test_mismatched_policy_is_rejected(repository, copied_data):
    path = copied_data / "policies.json"
    records = json.loads(path.read_text(encoding="utf-8"))
    records[0]["customer_id"] = "wrong-customer"
    path.write_text(json.dumps(records), encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        rebuild(repository.path, copied_data)


def test_rebuild_refuses_a_running_http_backend(repository):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with local_backend(repository, lambda _c, _t: "Unused", f"http://127.0.0.1:{port}"):
        before = repository.path.read_bytes()
        with pytest.raises(RuntimeError, match="already in use"):
            rebuild(repository.path)
        assert repository.path.read_bytes() == before
