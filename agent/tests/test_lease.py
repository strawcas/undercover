import httpx
import pytest

from agent.execution import AttemptBudget
from agent.lease import ClaimLease
from agent.tests.stubs import api_client
from backend.tests.conftest import begin_claim


def test_operation_and_release_failure_keep_primary(
    client, repository, monkeypatch, caplog
):
    monkeypatch.setattr("agent.http.sleep", lambda _: None)
    thread = begin_claim(repository)

    def lose_release(request, _response):
        if request.url.path.endswith("/release"):
            raise httpx.ReadTimeout("PRIVATE-CLEANUP", request=request)

    primary = ValueError("PRIVATE-OPERATION")
    with (
        api_client(client, after_response=lose_release) as transport,
        pytest.raises(ValueError, match="PRIVATE-OPERATION") as error,
        ClaimLease(transport, "CLM-1001", thread, AttemptBudget()),
    ):
        raise primary
    assert error.value is primary
    assert "Release claim execution also failed" in str(primary.__notes__)
    assert "PRIVATE-" not in caplog.text


def test_release_failure_alone_is_visible(client, repository, monkeypatch):
    monkeypatch.setattr("agent.http.sleep", lambda _: None)
    thread = begin_claim(repository)

    def lose_release(request, _response):
        if request.url.path.endswith("/release"):
            raise httpx.ReadTimeout("Lost response", request=request)

    with (
        api_client(client, after_response=lose_release) as transport,
        pytest.raises(httpx.ReadTimeout),
        ClaimLease(transport, "CLM-1001", thread, AttemptBudget()),
    ):
        pass
