"""Map provider replies to the common idempotent usage record."""

import uuid

from langchain_core.messages import AIMessage, BaseMessage


def model_reply(reply: BaseMessage, run_id: str | None) -> dict:
    """Preserve unavailable token counts and OpenRouter's actual reported cost."""
    usage = reply.usage_metadata if isinstance(reply, AIMessage) else None
    reported = reply.response_metadata.get("token_usage") or {}
    return {
        "reply_id": reply.id or str(uuid.uuid4()),
        "run_id": run_id,
        "input_tokens": usage.get("input_tokens") if usage else None,
        "output_tokens": usage.get("output_tokens") if usage else None,
        "total_tokens": usage.get("total_tokens") if usage else None,
        "cost": reported.get("cost"),
    }
