from unittest.mock import Mock

import httpx

from backend import notifications, routes
from backend.claims import Claims
from backend.models import DecisionInput
from backend.tests.conftest import (
    begin_decision_claim,
    decide_fixture,
    decision_payload,
)


def test_unreachable_notification_preserves_saved_sign_off(
    client, repository, monkeypatch, caplog
):
    thread = begin_decision_claim(repository)
    decide_fixture(
        Claims(repository),
        "CLM-1001",
        DecisionInput.model_validate(decision_payload(thread)),
    )
    post = Mock(side_effect=httpx.ConnectTimeout("PRIVATE-DESTINATION"))
    monkeypatch.setattr(notifications.httpx, "post", post)
    monkeypatch.setattr(routes, "notify_sign_off", notifications.notify_sign_off)
    result = client.post("/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"})
    assert result.status_code == 201
    saved = Claims(repository).detail("CLM-1001")
    assert saved["sign_off"] == result.json()
    assert saved["status"] == "in_progress"
    assert saved["payment"] is None
    assert post.call_args.kwargs["timeout"] == 1
    assert "PRIVATE-" not in caplog.text
    assert "Notify saved sign-off" in caplog.text
