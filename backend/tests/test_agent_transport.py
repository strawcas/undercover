import json
from io import BytesIO

import httpx
import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    IncompleteReadError,
    ReadTimeoutError,
)

from backend import agent_service


@pytest.mark.parametrize("failure", ["timeout", "truncated", "malformed"])
def test_uncertain_laptop_invocations_ask_the_handler_to_reload(
    client, agent_answers, failure
):
    calls = []

    def answer(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("waiting for the answer", request=request)
        if failure == "truncated":
            raise httpx.RemoteProtocolError("cut-off response", request=request)
        return httpx.Response(200, content=b'{"accepted":')

    agent_answers(answer)
    reply = client.post("/api/dispatches", json={"concurrency": 5})
    assert reply.status_code == 503
    assert "Reload" in reply.json()["detail"]
    assert len(calls) == 1


def test_runtime_receives_fresh_session_ids_and_one_attempt(monkeypatch):
    requested, returned = [], []

    class Runtime:
        def invoke_agent_runtime(self, **kwargs):
            requested.append(kwargs)
            stream = BytesIO(b'{"accepted": true}')
            returned.append(stream)
            return {"response": stream, "statusCode": 200}

    def create(service, *, config):
        assert service == "bedrock-agentcore"
        assert config.retries["total_max_attempts"] == 1
        assert (config.connect_timeout, config.read_timeout) == (5, 20)
        return Runtime()

    agent_service.runtime_client.cache_clear()
    monkeypatch.setattr(agent_service.boto3, "client", create)
    monkeypatch.setenv("UNDERCOVER_AGENT_RUNTIME", "test-runtime")
    try:
        for _ in range(2):
            assert agent_service.invoke("resume_claim", claim_id="CLM-1001") == {
                "accepted": True
            }
    finally:
        agent_service.runtime_client.cache_clear()

    assert len(requested) == 2
    sessions = {call["runtimeSessionId"] for call in requested}
    assert len(sessions) == 2
    assert all(len(session) >= 33 for session in sessions)
    for call in requested:
        assert call["agentRuntimeArn"] == "test-runtime"
        assert json.loads(call["payload"]) == {
            "operation": "resume_claim",
            "claim_id": "CLM-1001",
        }
    assert all(stream.closed for stream in returned)


@pytest.mark.parametrize(
    ("failure", "status", "uncertain"),
    [
        ("offline", 503, False),
        ("runtime_error", 503, False),
        ("explicit", 503, False),
        ("refused", 409, False),
        ("timeout", 503, True),
        ("partial", 503, True),
    ],
)
def test_runtime_classifies_refusal_and_ambiguous_failures(
    monkeypatch, failure, status, uncertain
):
    calls = []

    class Partial(BytesIO):
        def read(self, *args):
            raise IncompleteReadError(actual_bytes=2, expected_bytes=10)

    class Runtime:
        def invoke_agent_runtime(self, **kwargs):
            calls.append(kwargs)
            if failure == "offline":
                raise EndpointConnectionError(endpoint_url="https://runtime.invalid")
            if failure == "runtime_error":
                raise ClientError({"Error": {"Code": "Denied"}}, "InvokeAgentRuntime")
            if failure == "timeout":
                raise ReadTimeoutError(endpoint_url="https://runtime.invalid")
            if failure == "partial":
                return {"statusCode": 200, "response": Partial()}
            if failure == "refused":
                return {
                    "statusCode": 200,
                    "response": BytesIO(b'{"refused":"Dispatch is busy"}'),
                }
            return {"statusCode": 500, "response": BytesIO(b"upstream error")}

    monkeypatch.setenv("UNDERCOVER_AGENT_RUNTIME", "test-runtime")
    monkeypatch.setattr(agent_service, "runtime_client", Runtime)
    with pytest.raises(agent_service.InvocationError) as refused:
        agent_service.invoke("start_dispatch", agents=3)
    assert (refused.value.status_code, refused.value.uncertain) == (status, uncertain)
    assert len(calls) == 1
    if failure == "refused":
        assert refused.value.detail == "Dispatch is busy"
