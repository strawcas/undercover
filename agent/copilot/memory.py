"""Search and save chat memories under the verified handler's actor ID."""

import os
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from langchain.tools import ToolException

from agent.copilot import progress
from agent.copilot.state import Context
from agent.memory import SEARCH_LIMIT, DeferredMemory

MEMORY_ID_ENV = "UNDERCOVER_COPILOT_MEMORY_ID"
NAMESPACES = ("preferences", "facts")


def configured() -> DeferredMemory | None:
    """Enable handler memory only when its own resource ID is set."""
    identity = os.getenv(MEMORY_ID_ENV)
    return DeferredMemory(identity) if identity else None


def search(store: DeferredMemory, actor: str, question: str) -> list[str]:
    """Read both allowed handler namespaces and verify returned scope."""
    found = []
    try:
        for kind in NAMESPACES:
            namespace = ("handlers", actor, kind)
            matches = store.search(namespace, query=question, limit=SEARCH_LIMIT)
            prefix = "/" + "/".join(namespace) + "/"
            for match in matches:
                paths = match.value.get("namespaces", [])
                if paths and all(path.startswith(prefix) for path in paths):
                    found.append(match.value["content"])
    except (BotoCoreError, ClientError) as error:
        raise ToolException("Handler memory is temporarily unavailable.") from error
    return found


def lookup(store: DeferredMemory, context: Context, question: str) -> list[str]:
    """Record the memory lookup and continue if the optional store is unavailable."""
    try:
        return progress.observe(
            context.progress,
            "search_handler_memory",
            "memory",
            question,
            lambda: search(store, context.handler["handler_id"], question),
        )
    except ToolException:
        return []


def save(store: DeferredMemory, context: Context, messages: tuple[str, str]) -> None:
    """Submit exactly the question and final answer with a stable event key."""
    receipt = context.progress
    try:
        store.save_turn(
            context.handler["handler_id"],
            receipt.conversation_id,
            UUID(receipt.turn_id).hex,
            messages,
        )
    except (BotoCoreError, ClientError):
        return
