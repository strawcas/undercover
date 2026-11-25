import io
import json
from uuid import uuid4

from botocore.exceptions import ClientError

from backend import agent_service, copilot_service


def test_runtime_retries_only_pre_delivery_conflicts_in_the_same_session(monkeypatch):
    requests, delays = [], []

    class Runtime:
        def invoke_agent_runtime(self, **request):
            requests.append(request)
            if len(requests) in (1, 2):
                raise ClientError(
                    {"Error": {"Code": "RetryableConflictException"}},
                    "InvokeAgentRuntime",
                )
            return {"response": io.BytesIO(b'{"accepted":true}')}

    monkeypatch.setattr(agent_service, "copilot_runtime_client", Runtime)
    monkeypatch.setattr(copilot_service, "sleep", delays.append)
    session = str(uuid4())
    payload = {"operation": "resume", "conversation_id": session, "bearer": "offline"}
    response = copilot_service.runtime_reply("stand-in-arn", session, payload)
    assert agent_service.accepted(response) == {"accepted": True}
    assert [request["runtimeSessionId"] for request in requests] == [session] * 3
    assert [json.loads(request["payload"]) for request in requests] == [payload] * 3
    assert delays == [0.5, 1]
