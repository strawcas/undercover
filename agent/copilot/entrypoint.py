"""Serve the Copilot as an independent AgentCore app."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Thread
from typing import Any, Literal
from uuid import UUID

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import BaseModel, SecretStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from agent.copilot import worker
from agent.runtime_settings import RuntimeSettings, on_runtime

settings = RuntimeSettings(("OPENROUTER_API_KEY", "DATABASE_URL"))
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: BedrockAgentCoreApp) -> AsyncIterator[None]:
    """Release the first request's process pool on shutdown."""
    try:
        yield
    finally:
        await run_in_threadpool(settings.close)


app = BedrockAgentCoreApp(lifespan=lifespan)


class WorkRequest(BaseModel):
    operation: Literal["start", "resume"]
    conversation_id: UUID
    turn_id: UUID
    stretch_id: UUID
    bearer: SecretStr


def start(request: WorkRequest) -> None:
    """Keep this AgentCore session busy until the admitted worker returns."""
    task = app.add_async_task("copilot_turn")

    def execute() -> None:
        try:
            worker.run(
                request.bearer.get_secret_value(),
                str(request.conversation_id),
                str(request.turn_id),
                str(request.stretch_id),
            )
        finally:
            app.complete_async_task(task)

    try:
        Thread(target=execute, name="copilot_turn", daemon=True).start()
    except RuntimeError:
        app.complete_async_task(task)
        raise


@app.entrypoint
async def invoke(payload: Any) -> JSONResponse:
    """Answer an open health check or launch a verified handler's turn."""
    health = payload == {"operation": "health"}
    try:
        request = None if health else WorkRequest.model_validate(payload)
    except ValidationError:
        return JSONResponse({"error": "Invalid Copilot invocation"}, status_code=400)
    try:
        await settings.initialize(on_runtime())
        if request is not None:
            start(request)
    except Exception as error:
        log.warning("The Copilot could not initialize (%s)", type(error).__name__)
        return JSONResponse({"error": "The Copilot is unavailable"}, status_code=503)
    return JSONResponse({"healthy": True} if health else {"accepted": True})
