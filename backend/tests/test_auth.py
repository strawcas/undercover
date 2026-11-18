"""Handler actions verify signatures, identity and permissions."""

import re
import time

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from backend import agent_key, auth
from backend.main import app
from backend.tests.auth_support import AGENT_HEADERS, authorization, token


def test_route_inventory_requires_the_right_credential():
    shared = {
        "get_agent_openapi",
        "list_claims",
        "get_claim",
        "get_policy",
        "get_claim_history",
        "get_current_configuration",
    }
    handler = {
        "list_evaluations",
        "get_evaluation",
        "list_handlers",
        "list_steps",
        "get_latest_run",
        "get_active_run",
        "get_current_handler",
        "list_configurations",
        "get_settings",
        "list_rounds",
        "list_recipes",
        "start_dispatch",
        "start_evaluation",
    }
    senior = {
        "sign_off_claim",
        "change_settings",
        "start_round",
        "stop_round",
        "decide_proposal",
    }
    service = {
        "get_configuration",
        "search_documents",
        "read_section",
        "start_claim",
        "record_step",
        "record_decision",
        "calculate_payout",
        "pay_claim",
        "hold_evaluation_payment",
        "fail_claim",
        "start_run",
        "stop_runs",
        "get_run",
        "get_run_threads",
        "record_run_claim",
        "record_run_selection",
        "finish_run",
        "record_model_call",
        "admit_claim",
        "stop_run",
        "record_work_slice",
    }
    routes = TestClient(app)
    for path, operations in app.openapi()["paths"].items():
        url = re.sub(r"{\w+}", "1", path)
        for method, operation in operations.items():
            name = operation["operationId"]
            if name == "health":
                assert routes.get(url).status_code == 200
                continue
            assert routes.request(method, url).status_code == 401, name
            if name in handler | senior:
                assert (
                    routes.request(method, url, headers=AGENT_HEADERS).status_code
                    == 401
                )
            if name in senior:
                assert (
                    routes.request(
                        method, url, headers=authorization("HND-02")
                    ).status_code
                    == 403
                )
            elif name in service or path.startswith("/api/improver/"):
                assert (
                    routes.request(method, url, headers=authorization()).status_code
                    == 401
                )
            else:
                assert name in shared | handler, name


@pytest.mark.parametrize(
    "headers",
    [{}, {agent_key.HEADER: "wrong"}, authorization()],
)
def test_agent_routes_reject_missing_wrong_and_handler_credentials(headers):
    routes = TestClient(app, headers=headers)
    assert (
        routes.post("/api/claims/CLM-1001/start", json={"thread_id": "t"}).status_code
        == 401
    )
    assert routes.post("/api/claims/CLM-1001/model-calls", json={}).status_code == 401
    assert routes.post("/api/improver/rounds", json={}).status_code == 401
    assert (
        routes.post("/api/improver/rounds/1/proposals/1/decision", json={}).status_code
        == 401
    )


def test_agent_key_alone_grants_internal_write_access():
    routes = TestClient(app, headers=AGENT_HEADERS)
    assert (
        routes.post("/api/claims/CLM-1001/start", json={"thread_id": "t"}).status_code
        == 200
    )
    assert (
        routes.put(
            "/api/claims/CLM-1001/work-slices/s1", json={"run_id": None, "seconds": 1}
        ).status_code
        == 200
    )
    handler = TestClient(app, headers=authorization()).get("/api/me").json()
    assert routes.post("/api/improver/rounds", json=handler).status_code == 200


def test_shared_reads_and_missing_key_startup(monkeypatch):
    with TestClient(app) as routes:
        for headers, status in [
            (AGENT_HEADERS, 200),
            (authorization(), 200),
            ({}, 401),
        ]:
            assert routes.get("/api/claims", headers=headers).status_code == status
    monkeypatch.delenv(agent_key.ENV_NAME)
    with TestClient(app) as routes:
        assert routes.get("/api/health").status_code == 200
        assert routes.get("/api/claims", headers=authorization()).status_code == 200
        assert routes.get("/api/claims", headers=AGENT_HEADERS).status_code == 401
        assert (
            routes.get("/api/runs/1/threads", headers=AGENT_HEADERS).status_code == 401
        )


def test_service_authentication_failure_keeps_the_handler_signed_in(
    client, agent_answers
):
    agent_answers(lambda _: httpx.Response(401, json={"detail": "Invalid agent key"}))
    response = client.post("/api/dispatches", json={"concurrency": 3})
    assert response.status_code == 503
    assert client.get("/api/me").status_code == 200


@pytest.mark.parametrize("claims", [{"exp": 1}, {"aud": "other"}, {"iss": "other"}])
def test_invalid_claims_are_refused(client, claims):
    assert client.get("/api/me", headers=authorization(**claims)).status_code == 401


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/dispatches", {"concurrency": 3}),
        (
            "/api/evaluations",
            {"concurrency": 3, "tiny": True, "set_name": "evaluation"},
        ),
        ("/api/claims/CLM-1001/sign-off", {}),
    ],
)
def test_actions_require_sign_in(path, body):
    assert TestClient(app).post(path, json=body).status_code == 401


def test_invalid_signatures_and_malformed_tokens(client):
    for bad in ["invalid", token()[:-10] + "invalid123"]:
        assert (
            client.get(
                "/api/me", headers={"Authorization": f"Bearer {bad}"}
            ).status_code
            == 401
        )


def test_unknown_handler_and_key_failure(client, monkeypatch):
    assert client.get("/api/me", headers=authorization("HND-99")).status_code == 403

    def unavailable(_):
        raise jwt.PyJWKClientError("offline")

    monkeypatch.setattr(auth.keys, "get_signing_key_from_jwt", unavailable)
    assert client.get("/api/me").status_code == 401


def test_identity_comes_from_the_token(client):
    assert client.get("/api/me").json()["name"] == "Dana Whitfield"
    marcus = client.get("/api/me", headers=authorization("HND-02")).json()
    assert marcus["can_approve_over_limit"] is False
    assert (
        client.post(
            "/api/dispatches", json={"concurrency": 3}, headers=authorization("HND-02")
        ).status_code
        == 202
    )
    assert (
        client.post(
            "/api/claims/CLM-1001/sign-off",
            json={"handler_id": "HND-01"},
            headers=authorization("HND-02"),
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    ("claim", "offset", "status"),
    [("iat", 2, 200), ("iat", 30, 401), ("exp", -30, 401)],
)
def test_clock_tolerance_accepts_small_provider_skew_but_refuses_invalid_times(
    client, claim, offset, status
):
    response = client.get(
        "/api/me", headers=authorization(**{claim: int(time.time()) + offset})
    )
    assert response.status_code == status
