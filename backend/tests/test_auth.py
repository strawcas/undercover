"""Handler actions verify signatures, identity and permissions."""

import jwt
import pytest
from fastapi.testclient import TestClient

from backend import auth
from backend.main import app
from backend.tests.auth_support import authorization, token


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
