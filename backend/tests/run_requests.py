"""Run configurations shared by backend and agent test scenarios."""


def configuration(**overrides):
    return {
        "kind": "dispatch",
        "concurrency": 3,
        "model": "stub",
        "reasoning_effort": None,
        "set_name": None,
        "tiny": None,
    } | overrides


def start(client, **overrides):
    return client.post("/api/runs", json=configuration(**overrides))


def member(client, run_id, claim_id, status="selected", error=None):
    return client.put(
        f"/api/runs/{run_id}/claims/{claim_id}", json={"status": status, "error": error}
    )
