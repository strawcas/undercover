"""Store minimal claim events and retrieve only this customer's memories."""

import json
import logging
import os
import uuid
from collections.abc import Iterable
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import cached_property
from threading import Lock
from typing import Any

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from langchain.tools import ToolException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.store.base import BaseStore, Op, Result
from langgraph_checkpoint_aws import AgentCoreMemoryStore

from agent import api
from agent.backend_client import Refused

log = logging.getLogger(__name__)

CLAIM_FIELDS = ("summary", "claim_type", "incident_date", "incident_location", "items")
DECISION_FIELDS = ("decision", "payout", "section", "reason")
SEARCH_LIMIT = 5
CREATE_EVENT = "provide-client-params.bedrock-agentcore.CreateEvent"
REQUEST_LIMITS = Config(
    connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1}
)
_recovery_lock = Lock()
_event_key: ContextVar[str | None] = ContextVar(
    "undercover_memory_event_key", default=None
)


def add_event_token(params: dict[str, Any], **_: Any) -> None:
    """Carry the same event identifier through a provider retry or graph replay."""
    if token := _event_key.get():
        params["clientToken"] = token


class DeferredMemory(BaseStore):
    """Defer AWS client setup until a cleared original uses memory."""

    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id

    @cached_property
    def sdk(self) -> AgentCoreMemoryStore:
        """Build the SDK store and register the replay token on its client."""
        store = AgentCoreMemoryStore(
            memory_id=self.memory_id,
            region_name=boto3.Session().region_name,
            config=REQUEST_LIMITS,
        )
        store.client.meta.events.register_first(CREATE_EVENT, add_event_token)
        return store

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        return self.sdk.batch(ops)

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        return await self.sdk.abatch(ops)

    def save_turn(
        self, actor: str, session: str, token: str, messages: tuple[str, str]
    ) -> None:
        """Write a chat pair as one idempotent event in the configured store."""
        self.sdk.client.create_event(
            memoryId=self.memory_id,
            actorId=actor,
            sessionId=session,
            eventTimestamp=datetime.now(UTC),
            clientToken=token,
            payload=[
                {"conversational": {"content": {"text": text}, "role": role}}
                for role, text in zip(("USER", "ASSISTANT"), messages, strict=True)
            ],
        )


def configured(*, evaluation: bool) -> BaseStore | None:
    """Build the configured store for original claims only."""
    memory_id = os.getenv("UNDERCOVER_MEMORY_ID")
    return DeferredMemory(memory_id) if memory_id and not evaluation else None


def remember(
    store: BaseStore, claim: dict[str, Any], decision: dict[str, Any]
) -> list[BaseMessage]:
    """Write the customer submission summary and the recorded decision."""
    identity = (claim["customer_id"], claim["claim_id"])
    messages = [
        HumanMessage(json.dumps({field: claim[field] for field in CLAIM_FIELDS})),
        AIMessage(json.dumps({field: decision[field] for field in DECISION_FIELDS})),
    ]
    saved = []
    for message in messages:
        event = f"{identity[0]}/{identity[1]}/{decision['decided_at']}/{message.type}"
        key = uuid.uuid5(uuid.NAMESPACE_URL, event).hex
        current = _event_key.set(key)
        try:
            store.put(identity, key, {"message": message})
            saved.append(message)
        except (BotoCoreError, ClientError) as error:
            log.warning(
                "%s could not save %s memory: %s", identity[1], message.type, error
            )
        finally:
            _event_key.reset(current)
    return saved


def claim_fields(claim: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields the memory step may read."""
    fields = ("customer_id", "claim_id", "thread_id", "revision", *CLAIM_FIELDS)
    return {field: claim[field] for field in fields}


def save_decision(
    store: BaseStore, claim: dict[str, Any], decision: dict[str, Any]
) -> None:
    """Report acknowledged events so the app can settle the receipt."""
    saved = remember(store, claim, decision)
    if not saved:
        return
    messages = [
        {
            "role": "customer" if message.type == "human" else "agent",
            "text": message.text,
        }
        for message in saved
    ]
    try:
        api.finish_memory(
            claim["claim_id"], claim["thread_id"], messages, claim["revision"]
        )
    except (Refused, httpx.RequestError) as error:
        log.warning(
            "%s could not settle its memory receipt: %s", claim["claim_id"], error
        )


def recover_pending() -> None:
    """Retry incomplete memory receipts even when those claims are settled."""
    store = configured(evaluation=False)
    if store is None or not _recovery_lock.acquire(blocking=False):
        return
    try:
        for claim_id in api.list_pending_memory():
            claim = api.get_claim(claim_id)
            save_decision(store, claim_fields(claim), claim["decision"])
    except (Refused, httpx.RequestError) as error:
        log.warning("Could not recover pending customer memories: %s", error)
    finally:
        _recovery_lock.release()


def search(store: BaseStore, customer_id: str, claim_id: str, query: str) -> list[str]:
    """Read prior claim summaries and facts under the runtime customer's namespace."""
    customer = ("customers", customer_id)
    current = (*customer, "claims", claim_id)
    try:
        matches = store.search(customer, query=query, limit=SEARCH_LIMIT)
    except (BotoCoreError, ClientError) as error:
        raise ToolException(
            f"Customer memory could not be searched: {error}"
        ) from error
    records = []
    for match in matches:
        paths = [
            tuple(path.strip("/").split("/"))
            for path in match.value.get("namespaces", [])
        ]
        if paths and all(
            path[:2] == customer and path[:4] != current for path in paths
        ):
            records.append(match.value["content"])
    return records
