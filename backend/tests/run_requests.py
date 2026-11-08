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
