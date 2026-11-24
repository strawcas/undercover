from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import claim_reviews, main
from backend.tests.auth_support import authorization
from backend.tests.claim_requests import decide, decision_input, sign_off, start

CLAIM = "CLM-1002"
NOTE = "Check whether section 7 covers the clinic fee."


def hold(client):
    start(client, CLAIM).raise_for_status()
    return decide(client, CLAIM, "approve", 1350).json()


def send_back(client, note=NOTE, handler="HND-01"):
    return client.post(
        f"/api/claims/{CLAIM}/send-back",
        json={"note": note},
        headers=authorization(handler),
    )


def take(client, owner):
    return client.post(
        f"/api/claims/{CLAIM}/take-review", json={"owner": owner, "run_id": None}
    )


def heartbeat(client, owner, working=True):
    return client.put(
        f"/api/claims/{CLAIM}/review-heartbeat",
        json={"owner": owner, "working": working},
    )


@pytest.mark.parametrize("note", ["", "   ", "x" * 1001])
def test_review_requires_a_short_nonblank_note(client, note):
    hold(client)
    assert send_back(client, note).status_code == 422


def test_return_keeps_the_complete_approval_and_adds_a_timeline_step(
    client, agent_calls
):
    assert send_back(client).status_code == 409
    approval = hold(client)
    assert send_back(client, handler="HND-02").status_code == 403
    response = send_back(client)
    assert response.status_code == 200
    claim = response.json()
    assert (claim["status"], claim["decision"], claim["payment"]) == (
        "sent_back",
        None,
        None,
    )
    assert claim["thread_id"] == approval["thread_id"]
    (review,) = claim["returned_decisions"]
    assert review["decision"] == approval["decision"]
    assert (review["handler"], review["note"]) == ("Dana Whitfield", NOTE)
    timeline = client.get(f"/api/claims/{CLAIM}/steps").json()
    assert timeline[-1]["kind"] == "claim_review"
    assert timeline[-1]["content"] == {
        key: review[key] for key in ("handler", "note", "reviewed_at")
    }
    assert agent_calls == [f"/api/claims/{CLAIM}/resume"]
    assert send_back(client).status_code == 409


def test_sign_off_and_send_back_have_one_winner(client):
    hold(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        settle = pool.submit(sign_off, client, CLAIM, "HND-01")
        returned = pool.submit(send_back, client)
        assert sorted((settle.result().status_code, returned.result().status_code)) == [
            200,
            409,
        ]


@pytest.mark.parametrize("action", ["sign-off", "send-back"])
def test_a_dashboard_review_rejects_a_stale_decision_revision(client, action):
    approved = hold(client)
    reply = client.post(
        f"/api/claims/{CLAIM}/{action}",
        json={"note": NOTE},
        headers={"If-Match": '"999"'},
    )
    assert reply.status_code == 409
    assert "Reload the claim" in reply.json()["detail"]
    assert client.get(f"/api/claims/{CLAIM}").json() == approved


def test_lease_expires_at_five_minutes_and_rejects_old_worker_writes(
    client, monkeypatch
):
    approval = hold(client)
    send_back(client).raise_for_status()
    timestamp = ["2026-11-10T09:00:00+00:00"]
    monkeypatch.setattr(claim_reviews, "now", lambda: timestamp[0])
    monkeypatch.setattr(main, "now", lambda: timestamp[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(lambda owner: take(client, owner), ("standalone", "dispatch"))
        )
    first, refused = sorted(replies, key=lambda response: response.status_code)
    assert [first.status_code, refused.status_code] == [200, 409]
    owner = "standalone" if first is replies[0] else "dispatch"
    assert first.json()["review_active"]
    assert first.json()["thread_id"] == approval["thread_id"]
    assert heartbeat(client, owner).status_code == 200
    timestamp[0] = "2026-11-10T09:04:59+00:00"
    assert take(client, "recovery").status_code == 409
    timestamp[0] = "2026-11-10T09:05:00+00:00"
    recovered = take(client, "recovery")
    assert recovered.status_code == 200
    assert heartbeat(client, owner, False).status_code == 409
    assert heartbeat(client, owner).status_code == 409
    body = decision_input("refer", 0, thread_id=approval["thread_id"])
    response = client.post(
        f"/api/claims/{CLAIM}/decision",
        json=body | {"revision": first.json()["revision"]},
    )
    assert response.status_code == 409
    replacement = client.post(
        f"/api/claims/{CLAIM}/decision",
        json=body | {"revision": recovered.json()["revision"]},
    )
    assert replacement.status_code == 200
    assert replacement.json()["decision"]["decision"] == "refer"
