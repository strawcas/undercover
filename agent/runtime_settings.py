"""Initialize the agent only after an invocation supplies its request context."""

import asyncio
import os
import random
from collections.abc import Awaitable, Callable, Mapping
from contextlib import ExitStack
from secrets import compare_digest
from typing import cast

from bedrock_agentcore.identity.auth import requires_api_key
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
from starlette.concurrency import run_in_threadpool

from agent import checkpoints
from agent.console import show_reports
from agent.credentials import API_KEY, configured_key

CREDENTIALS = ("OPENROUTER_API_KEY", API_KEY, "DATABASE_URL")
PROVIDERS = {setting: f"AGENTCORE_CREDENTIAL_{setting}_NAME" for setting in CREDENTIALS}


def on_runtime() -> bool:
    """Detect the platform-provided credential settings."""
    return any(provider in os.environ for provider in PROVIDERS.values())


def authorized(headers: Mapping[str, str] | None) -> bool:
    """Check the local bearer credential in constant time."""
    key = configured_key()
    supplied = next(
        (
            value
            for name, value in (headers or {}).items()
            if name.lower() == "authorization"
        ),
        "",
    )
    scheme, _, token = supplied.partition(" ")
    return (
        bool(key)
        and scheme.lower() == "bearer"
        and compare_digest(key.encode(), token.encode())
    )


async def credential(provider_name: str) -> str:
    """Request one provider's key with the current request's workload identity."""

    @requires_api_key(provider_name=provider_name)
    async def receive(api_key: str) -> str:
        return api_key

    return await cast(Callable[[], Awaitable[str]], receive)()


async def managed_credentials(required: tuple[str, ...]) -> dict[str, str]:
    """Resolve all platform secrets before modifying process settings."""
    if not os.getenv("UNDERCOVER_GATEWAY_URL"):
        raise RuntimeError("AgentCore Runtime requires UNDERCOVER_GATEWAY_URL")
    if not BedrockAgentCoreContext.get_workload_access_token():
        raise RuntimeError("AgentCore Runtime did not provide a workload access token")
    random.seed()
    return {
        setting: await credential(os.environ[PROVIDERS[setting]])
        for setting in required
    }


class RuntimeSettings:
    """Keep the one initialized pool and allow the next request to retry failures."""

    def __init__(self, required: tuple[str, ...] = CREDENTIALS) -> None:
        self.lock = asyncio.Lock()
        self.resources: ExitStack | None = None
        self.required = required

    async def initialize(self, managed: bool) -> bool:
        """Return whether this request opened the pool and published credentials."""
        async with self.lock:
            if self.resources is not None:
                return False
            received = await managed_credentials(self.required) if managed else {}
            previous = {name: os.getenv(name) for name in received}
            os.environ.update(received)
            try:
                self.resources = await run_in_threadpool(self.open)
            finally:
                if self.resources is None:
                    for name, value in previous.items():
                        if value is None:
                            os.environ.pop(name, None)
                        else:
                            os.environ[name] = value
            return True

    def open(self) -> ExitStack:
        """Open the checkpoint pool after credentials are ready."""
        if API_KEY in self.required:
            if not configured_key():
                raise RuntimeError(f"{API_KEY} is missing")
            show_reports()
        with ExitStack() as resources:
            resources.enter_context(checkpoints.checkpointer())
            return resources.pop_all()

    def close(self) -> None:
        """Close the checkpoint pool if a request initialized it."""
        if self.resources is not None:
            self.resources.close()
            self.resources = None
