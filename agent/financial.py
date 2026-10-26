"""Reconcile ambiguous financial responses before considering a second write."""

from decimal import Decimal

import httpx

from agent.execution import Record, check_attempt
from agent.http import RETRY_STATUSES, request


def committed(claim: Record, path: str, body: Record) -> Record | None:
    if path.endswith("/decision"):
        saved = claim["decision"]
        if (
            saved
            and all(
                saved.get(key) == value
                for key, value in body.items()
                if key != "payout"
            )
            and Decimal(saved["payout"]) == Decimal(body["payout"])
        ):
            return saved
    else:
        payment = claim["payment"]
        if payment and Decimal(payment["amount"]) == Decimal(body["amount"]):
            return payment
    return None


def write(
    client: httpx.Client, claim_path: str, path: str, body: Record
) -> httpx.Response:
    """Retry once only after storage confirms that the first write did not commit."""
    for attempt in range(2):
        check_attempt()
        failure: httpx.TransportError | None = None
        response = None
        try:
            response = client.post(path, json=body)
            if response.status_code not in RETRY_STATUSES:
                return response
        except httpx.TransportError as error:
            failure = error
        stored = request(client, "GET", claim_path)
        stored.raise_for_status()
        claim = stored.json()
        saved = committed(claim, path, body)
        if saved is not None:
            return httpx.Response(200, json=saved, request=httpx.Request("POST", path))
        if (
            attempt
            or claim["thread_id"] != body["thread_id"]
            or not claim["run_active"]
        ):
            if failure is not None:
                raise failure
            if response is not None:
                return response
    raise RuntimeError("Financial reconciliation ended unexpectedly.")
