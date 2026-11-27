"""Report bounded tool results while synchronous work is running."""

import json
from collections.abc import Callable
from time import monotonic
from typing import Any
from uuid import uuid4

import httpx
from langchain.tools import ToolException
from mcp.shared.exceptions import McpError

from agent.backend_client import Refused
from agent.copilot.api import Progress

CARD_TEXT_LIMIT = 600
CARD_RESULT_LIMIT = 6000


def clean(text: str, bearer: str) -> str:
    """Remove the live bearer if a remote service ever reflects it."""
    return text.replace(bearer, "[sign-in removed]") if bearer else text


def bounded(value: Any, bearer: str) -> Any:
    """Preserve short JSON results and produce a readable preview of larger ones."""
    rendered = clean(json.dumps(value, ensure_ascii=False, default=str), bearer)
    if len(rendered) <= CARD_RESULT_LIMIT:
        return json.loads(rendered)
    if isinstance(value, dict):
        raw_sources = value.get("sources")
        if not isinstance(raw_sources, list):
            raw_sources = [value.get("source")]
        sources = [
            {
                field: clean(str(item[field]), bearer)[:200]
                for field in ("file", "owner")
                if field in item
            }
            for item in raw_sources[:5]
            if isinstance(item, dict)
        ]
        if sources:
            return {"sources": sources, "preview": rendered[:1200] + "…"}
    return rendered[: CARD_RESULT_LIMIT // 2] + "…"


def reason_for(error: Exception) -> str:
    """Explain known tool refusals and avoid leaking transport diagnostics."""
    if isinstance(error, Refused):
        return str(error.detail)
    if isinstance(error, (ToolException, McpError)):
        return str(error)
    return "The service could not complete this step."


def observe(
    progress: Progress, tool: str, kind: str, inputs: str, work: Callable[[], Any]
) -> Any:
    """Publish a card before work and update it after the result or refusal."""
    identity = str(uuid4())
    bearer = progress.app.bearer
    card = {
        "tool": tool,
        "kind": kind,
        "input": clean(inputs, bearer)[:CARD_TEXT_LIMIT],
        "status": "working",
        "reason": None,
        "result": None,
        "seconds": None,
    }
    progress.step(identity, card)
    started = monotonic()
    try:
        result = work()
    except (Refused, ToolException, McpError, httpx.RequestError) as error:
        reason = clean(reason_for(error), bearer)[:CARD_TEXT_LIMIT]
        progress.step(
            identity,
            card
            | {
                "status": "failed"
                if isinstance(error, httpx.RequestError)
                else "refused",
                "reason": reason,
                "seconds": monotonic() - started,
            },
        )
        raise ToolException(reason) from None
    outcome = result.get("status") if isinstance(result, dict) else None
    failed = outcome in ("stopped", "failed", "timed_out")
    reason = (result.get("reason") or result.get("stop_reason")) if failed else None
    progress.step(
        identity,
        card
        | {
            "status": "failed" if failed else "done",
            "reason": clean(str(reason), bearer)[:CARD_TEXT_LIMIT] if reason else None,
            "result": bounded(result, bearer),
            "seconds": monotonic() - started,
        },
    )
    return result
