import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.claims import ClaimError, Claims
from backend.main import create_app
from backend.ownership import DatabaseOwner
from backend.storage import ROOT, SEED_FILES, Store


def hold_database(path, ready):
    with DatabaseOwner(path):
        ready.set()
        multiprocessing.Event().wait(30)


def test_source_order_and_appended_claim(tmp_path):
    source = tmp_path / "data"
    source.mkdir()
    for filename in SEED_FILES:
        (source / filename).write_text(
            (ROOT / "data" / filename).read_text(encoding="utf-8"), encoding="utf-8"
        )
    original = json.loads((source / "claims.json").read_text(encoding="utf-8"))
    reordered = [original[2], original[0], original[1]]
    (source / "claims.json").write_text(json.dumps(reordered), encoding="utf-8")
    store = Store(tmp_path / "ordered.db")
    store.initialize(source)
    expected = [claim["claim_id"] for claim in reordered]
    assert [row["claim_id"] for row in Claims(store).queue()] == expected
    store.add_claim(reordered[0] | {"claim_id": "CLM-0001"})
    expected.append("CLM-0001")
    claims = Claims(store)
    attempted = set()
    for claim_id in expected:
        selected = claims.start_next(attempted)
        assert selected is not None
        assert selected[0] == claim_id
        attempted.add(claim_id)
        claims.finish(*selected, "Synthetic failure")
    assert claims.start_next(attempted) is None
    store.add_claim(reordered[0] | {"claim_id": "CLM-0000"})
    selected = claims.start_next(attempted)
    assert selected is not None
    assert selected[0] == "CLM-0000"


def test_migration_preserves_saved_records(repository):
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE claims SET state='decided', thread_id='saved-thread', "
            "decision='{}', payout_cents=125000, sign_off='{}', payment='{}' "
            "WHERE id='CLM-1001'"
        )
        connection.execute("DROP INDEX queue_positions")
        connection.execute("ALTER TABLE claims DROP COLUMN queue_position")
        before = [dict(row) for row in connection.execute("SELECT * FROM claims")]
    repository.initialize()
    repository.initialize()
    with repository.connect() as connection:
        after = [dict(row) for row in connection.execute("SELECT * FROM claims")]
    assert [row.pop("queue_position") for row in after] == list(range(1, 26))
    assert before == after


def test_reservation_is_atomic_and_skips_decided(repository):
    claims = Claims(repository)
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE claims SET state='awaiting_sign_off', decision='{}' "
            "WHERE id='CLM-1001'"
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claims.start_next(set()), range(2)))
    assert results.count(None) == 1
    selected = next(result for result in results if result is not None)
    assert selected[0] == "CLM-1002"
    with pytest.raises(ClaimError, match="already running"):
        claims.start("CLM-1003")
    claims.finish(*selected, "Synthetic failure")
    selected = claims.start_next({"CLM-1002"}, {"CLM-1004"})
    assert selected is not None
    assert selected[0] == "CLM-1004"


def test_claim_links_must_exist(repository):
    claim = repository.record("claim", "CLM-1001")
    with pytest.raises(ValueError, match="belong"):
        repository.add_claim(claim | {"customer_id": "CUS-3002"})
    with pytest.raises(KeyError):
        repository.add_claim(claim | {"policy_id": "missing"})


def test_database_ownership_blocks_backend(repository, tmp_path):
    with DatabaseOwner(repository.path):
        with (
            pytest.raises(RuntimeError, match="already in use"),
            TestClient(create_app(repository, auto_run=False)),
        ):
            pass
        with DatabaseOwner(tmp_path / "separate.db"):
            pass
    with TestClient(create_app(repository, auto_run=False)) as client:
        assert client.get("/api/health").status_code == 200


def test_process_exit_releases_ownership(tmp_path):
    path = tmp_path / "owned.db"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=hold_database, args=(path, ready))
    process.start()
    try:
        assert ready.wait(15)
        with pytest.raises(RuntimeError, match="already in use"), DatabaseOwner(path):
            pass
    finally:
        process.terminate()
        process.join(10)
    with DatabaseOwner(path):
        pass


def test_shared_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "load_dotenv", lambda _path: None)
    monkeypatch.delenv("UNDERCOVER_DB_PATH", raising=False)
    monkeypatch.delenv("UNDERCOVER_BACKEND_URL", raising=False)
    assert config.database_path() == ROOT / "undercover.db"
    assert config.backend_url() == "http://localhost:8000"
    monkeypatch.setenv("UNDERCOVER_DB_PATH", str(tmp_path / "custom.db"))
    monkeypatch.setenv("UNDERCOVER_BACKEND_URL", "http://localhost:8765/")
    assert config.database_path() == tmp_path / "custom.db"
    assert config.backend_url() == "http://localhost:8765"
