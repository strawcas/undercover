"""Durable per-assessment call reservations across graph node replay."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.attempt_guard import require_attempt
from backend.claim_records import claim_row
from backend.errors import ClaimError
from backend.harness_models import RequiredInput
from backend.storage import Store


class UsageInput(RequiredInput):
    thread_id: UUID
    request_id: UUID
    kind: Literal["model", "tool"]
    limit: int = Field(ge=1, le=80)


class Usage(BaseModel):
    model_calls: int
    tool_calls: int


def reserve(
    store: Store, claim_id: str, request: UsageInput, execution_id: str | None = None
) -> Usage:
    """Consume budget before an external request, including provider retries."""
    with store.transaction() as connection:
        require_attempt(
            connection,
            claim_row(connection, claim_id),
            str(request.thread_id),
            execution_id,
        )
        rows = connection.execute(
            "SELECT kind,request_id FROM attempt_usage WHERE thread_id=?",
            (str(request.thread_id),),
        ).fetchall()
        counts = Usage(
            model_calls=sum(r["kind"] == "model" for r in rows),
            tool_calls=sum(r["kind"] == "tool" for r in rows),
        )
        prior = next(
            (r for r in rows if r["request_id"] == str(request.request_id)), None
        )
        if prior:
            if prior["kind"] != request.kind:
                raise ClaimError(409, "Call reservation conflicts with its saved kind.")
            return counts
        maximum = min(request.limit, 30 if request.kind == "model" else 80)
        if getattr(counts, f"{request.kind}_calls") >= maximum:
            raise ClaimError(409, f"Assessment {request.kind}-call limit exceeded.")
        connection.execute(
            "INSERT INTO attempt_usage (thread_id,request_id,kind) VALUES (?,?,?)",
            (str(request.thread_id), str(request.request_id), request.kind),
        )
        setattr(
            counts,
            f"{request.kind}_calls",
            getattr(counts, f"{request.kind}_calls") + 1,
        )
        return counts
