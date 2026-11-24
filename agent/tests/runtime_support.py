from time import monotonic, sleep

from starlette.testclient import TestClient

from agent import entrypoint
from backend.tests.auth_support import TEST_AGENT_KEY

LOCAL_BEARER = {"Authorization": f"Bearer {TEST_AGENT_KEY}"}


def runtime_client(headers=None):
    return TestClient(
        entrypoint.app, headers=LOCAL_BEARER if headers is None else headers
    )


def invoke(client, operation, **arguments):
    return client.post("/invocations", json={"operation": operation, **arguments})


def wait_for_idle(client):
    deadline = monotonic() + 10
    while client.get("/ping").json()["status"] == "HealthyBusy":
        assert monotonic() < deadline
        sleep(0.02)
