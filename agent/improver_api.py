"""The improver's backend operations; this is the only agent client for scores."""

from typing import Any

from agent import backend_client

backend = backend_client.client()


def _call(method: str, path: str, **request: Any) -> Any:
    return backend_client.call(backend, method, path, **request)


def start_round(handler: dict) -> dict:
    return _call("POST", "/improver/rounds", json=handler)


def recover() -> list[str]:
    return _call("POST", "/improver/recover")


def get_round(round_id: str) -> dict:
    return _call("GET", f"/improver/rounds/{round_id}")


def read_results(round_id: str) -> dict:
    return _call("GET", f"/improver/rounds/{round_id}/report")


def baseline(round_id: str) -> dict:
    return _call("POST", f"/improver/rounds/{round_id}/baseline")


def progress(round_id: str, status: str, progress: int) -> dict:
    return _call(
        "PUT",
        f"/improver/rounds/{round_id}/progress",
        json={"status": status, "progress": progress},
    )


def validate_proposal(round_id: str, proposal: dict) -> str | None:
    return _call("POST", f"/improver/rounds/{round_id}/validate", json=proposal)[
        "refusal"
    ]


def pending(round_id: str, proposal: dict) -> dict:
    return _call("POST", f"/improver/rounds/{round_id}/proposals", json=proposal)


def decide(round_id: str, proposal_id: str, decision: dict) -> dict:
    return _call(
        "POST",
        f"/improver/rounds/{round_id}/proposals/{proposal_id}/decision",
        json=decision,
    )


def quote(round_id: str, version: int, set_name: str) -> dict:
    return _call(
        "POST",
        f"/improver/rounds/{round_id}/quote",
        json={"configuration_version": version, "set_name": set_name},
    )


def admit(round_id: str) -> dict:
    return _call("POST", f"/improver/rounds/{round_id}/admission")


def sequence_quote(round_id: str) -> dict:
    return _call("POST", f"/improver/rounds/{round_id}/sequence-quote")


def usage(round_id: str, measurement: dict) -> dict:
    return _call("POST", f"/improver/rounds/{round_id}/usage", json=measurement)


def verdict(round_id: str) -> dict:
    return _call("POST", f"/improver/rounds/{round_id}/verdict")


def finish(round_id: str, stopped: bool, reason: str) -> dict:
    return _call(
        "POST",
        f"/improver/rounds/{round_id}/finish",
        json={"stopped": stopped, "reason": reason},
    )
