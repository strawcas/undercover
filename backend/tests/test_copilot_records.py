from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from backend import copilot_records, copilot_service
from backend.database import COPILOT_TABLES, engine, seed, upgrade
from backend.tests.auth_support import authorization
from backend.tests.claim_requests import decide, start

PATH = "/api/copilot/conversations"


@pytest.fixture
def work(client, monkeypatch):
    launches = []
    monkeypatch.setattr(copilot_service, "launch", lambda *args: launches.append(args))
    response = client.post(PATH)
    assert response.status_code == 201
    identity = response.json()
    assert identity["handler_id"] == "HND-01"
    assert str(UUID(identity["conversation_id"])) == identity["conversation_id"]
    path = f"{PATH}/{identity['conversation_id']}"
    response = client.post(f"{path}/messages", json={"message": "What needs me?"})
    assert response.status_code == 202
    turn = response.json()["turn_id"]
    return path, f"{path}/turns/{turn}", launches


def outcome(stretch, **changes):
    return {
        "stretch_id": stretch,
        "status": "done",
        "answer": "The queue is ready.",
        "failure": None,
        "confirmation": None,
        "usage": None,
    } | changes


def approval():
    return {
        "claim_id": "CLM-1001",
        "customer_name": "Maya Patel",
        "amount": 2500,
        "reason": "Covered loss",
        "revision": 2,
    }


def test_copilot_sign_off_requires_an_active_confirmed_receipt(
    client, work, monkeypatch
):
    path, turn, launches = work
    claim_id = "CLM-1002"
    start(client, claim_id).raise_for_status()
    claim = decide(client, claim_id, "approve", 1350).json()
    start_id = launches[0][3]
    client.post(f"{turn}/take", json={"stretch_id": start_id}).raise_for_status()
    card = approval() | {"claim_id": claim_id, "revision": claim["revision"]}
    client.put(
        f"{turn}/outcome",
        json=outcome(
            start_id,
            status="waiting",
            answer=None,
            confirmation=card,
        ),
    ).raise_for_status()
    turn_id = turn.split("/")[-1]
    client.post(
        f"{path}/confirmation", json={"turn_id": turn_id, "confirmed": True}
    ).raise_for_status()
    stretch = launches[1][3]
    client.post(f"{turn}/take", json={"stretch_id": stretch}).raise_for_status()
    receipt = f"{path.split('/')[-1]}/{turn_id}/{stretch}/{uuid4()}"
    headers = {
        "X-UnderCover-Copilot-Receipt": receipt,
        "If-Match": f'"{claim["revision"]}"',
    }
    future = datetime.now(UTC) + timedelta(minutes=20)
    monkeypatch.setattr(copilot_records, "now", future.isoformat)
    assert (
        client.post(f"/api/claims/{claim_id}/sign-off", headers=headers).status_code
        == 409
    )
    assert client.get(f"/api/claims/{claim_id}").json()["payment"] is None


def test_retrying_the_same_admitted_action_returns_its_run_id(
    client, work, agent_calls
):
    path, turn, launches = work
    stretch = launches[0][3]
    client.post(f"{turn}/take", json={"stretch_id": stretch}).raise_for_status()
    receipt = f"{path.split('/')[-1]}/{turn.split('/')[-1]}/{stretch}/{uuid4()}"
    request = {
        "json": {"concurrency": 2},
        "headers": {"X-UnderCover-Copilot-Receipt": receipt},
    }
    first = client.post("/api/dispatches", **request)
    second = client.post("/api/dispatches", **request)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert len(agent_calls) == 1


def test_conversations_are_owned_and_titled(client, work):
    path, turn, launches = work
    assert client.get(PATH).json()[0]["title"] == "What needs me?"
    foreign = authorization("HND-02")
    assert client.get(PATH, headers=foreign).json() == []
    assert client.get(path, headers=foreign).status_code == 404
    assert (
        client.post(
            f"{path}/messages", json={"message": "Hello"}, headers=foreign
        ).status_code
        == 404
    )
    answer = {"turn_id": turn.split("/")[-1], "confirmed": True}
    assert (
        client.post(f"{path}/confirmation", json=answer, headers=foreign).status_code
        == 404
    )
    assert (
        client.post(
            f"{turn}/take", json={"stretch_id": launches[0][3]}, headers=foreign
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"{turn}/outcome", json=outcome(launches[0][3]), headers=foreign
        ).status_code
        == 404
    )
    assert client.get(path, headers={"Authorization": ""}).status_code == 401
    assert (
        client.get(path, headers={"Authorization": "Bearer invalid"}).status_code == 401
    )
    latest = client.post(PATH).json()
    assert client.get(PATH).json()[0]["conversation_id"] == latest["conversation_id"]


def test_pause_resume_and_fenced_callbacks(client, work, monkeypatch):
    path, turn, launches = work
    stretch = launches[0][3]
    lease = {"stretch_id": stretch}
    assert client.post(f"{turn}/take", json=lease).status_code == 200
    assert client.post(f"{turn}/take", json=lease).status_code == 409
    card = {
        **lease,
        "tool": "get_claim",
        "kind": "claim",
        "input": "CLM-1001",
        "status": "working",
        "result": None,
        "reason": None,
        "seconds": None,
    }
    step = f"{turn}/steps/{uuid4()}"
    assert client.put(step, json=card).status_code == 204
    assert (
        client.put(step, json=card | {"status": "done", "seconds": 1}).status_code
        == 204
    )
    assert (
        client.post(f"{path}/messages", json={"message": "Too soon"}).status_code == 409
    )
    waiting = outcome(stretch, status="waiting", confirmation=approval(), answer=None)
    assert client.put(f"{turn}/outcome", json=waiting).status_code == 204
    future = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr(copilot_records, "now", future.isoformat)
    assert client.get(path).json()["turns"][0]["status"] == "waiting"
    assert (
        client.post(f"{path}/messages", json={"message": "Too soon"}).status_code == 409
    )
    answer = {"turn_id": turn.split("/")[-1], "confirmed": False}
    assert client.post(f"{path}/confirmation", json=answer).status_code == 202
    assert client.post(f"{path}/confirmation", json=answer).status_code == 409
    resumed = launches[1][3]
    assert resumed != stretch
    assert client.put(f"{turn}/outcome", json=waiting).status_code == 409
    payload = client.post(f"{turn}/take", json={"stretch_id": resumed}).json()
    assert payload["confirmed"] is False
    assert payload["confirmation"] == approval()
    assert client.put(f"{turn}/outcome", json=outcome(resumed)).status_code == 204
    done = client.get(path).json()["turns"][0]
    assert done["status"] == "done"
    assert done["steps"][0]["status"] == "done"
    assert done["confirmation"] is None


def test_expiry_and_single_admission(client, work, monkeypatch):
    path, stale, launches = work
    future = datetime.now(UTC) + timedelta(minutes=20)
    monkeypatch.setattr(copilot_records, "now", future.isoformat)
    assert client.get(path).json()["turns"][0]["status"] == "stopped"
    assert (
        client.post(f"{stale}/take", json={"stretch_id": launches[0][3]}).status_code
        == 409
    )

    def send(index):
        return client.post(
            f"{path}/messages", json={"message": f"New {index}"}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send, range(2))) == [202, 409]
    latest = client.get(path).json()["turns"][-1]
    turn = f"{path}/turns/{latest['turn_id']}"
    stretch = launches[-1][3]
    assert client.post(f"{turn}/take", json={"stretch_id": stretch}).status_code == 200
    failed = outcome(
        stretch, status="failed", failure="Service unavailable", answer=None
    )
    assert client.put(f"{turn}/outcome", json=failed).status_code == 204
    assert client.post(f"{path}/messages", json={"message": "Retry"}).status_code == 202


def test_upgrade_and_reseed_only_clear_copilot_threads(client):
    db = engine()
    assert client.get(PATH).json() == []
    with db.begin() as connection:
        for table in reversed(COPILOT_TABLES):
            table.drop(connection)
    upgrade(db)
    assert len(client.get("/api/claims").json()) == 26
    assert client.post(PATH).status_code == 201
    with db.begin() as connection:
        connection.execute(text("CREATE TABLE checkpoints (thread_id TEXT)"))
        connection.execute(
            text("INSERT INTO checkpoints VALUES ('copilot:one'), ('claim-thread')")
        )
    seed(db)
    assert client.get(PATH).json() == []
    with db.connect() as connection:
        assert list(connection.scalars(text("SELECT thread_id FROM checkpoints"))) == [
            "claim-thread"
        ]


@pytest.mark.parametrize("delivery", ["accepted", "rejected", "uncertain"])
def test_local_invocation_does_not_send_the_agent_key(client, monkeypatch, delivery):
    path = f"{PATH}/{client.post(PATH).json()['conversation_id']}"
    headers = authorization()

    def reply(request):
        assert request.url.path == "/invocations"
        assert "Authorization" not in request.headers
        assert "X-API-Key" not in request.headers
        assert (
            headers["Authorization"].removeprefix("Bearer ").encode() in request.content
        )
        if delivery == "uncertain":
            raise httpx.ReadTimeout("Offline stand-in", request=request)
        return httpx.Response(
            200,
            json={"accepted": True}
            if delivery == "accepted"
            else {
                "refused": headers["Authorization"],
            },
        )

    monkeypatch.setattr(
        copilot_service,
        "copilot",
        httpx.Client(
            base_url="http://stand-in",
            transport=httpx.MockTransport(reply),
        ),
    )
    response = client.post(
        f"{path}/messages", json={"message": "Hello"}, headers=headers
    )
    assert response.status_code == (503 if delivery == "rejected" else 202)
    detail = client.get(path)
    assert headers["Authorization"].removeprefix("Bearer ") not in detail.text
    assert detail.json()["turns"][0]["status"] == (
        "failed" if delivery == "rejected" else "working"
    )
