"""Serve the UnderCover agent through the AgentCore HTTP protocol."""

import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from http import HTTPStatus
from threading import Thread
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from agent import memory
from agent.backend_client import Refused
from agent.invocations import Invocation, invocation, work
from agent.runtime_settings import RuntimeSettings, authorized, on_runtime
from agent.service import UnconfirmedTake

settings = RuntimeSettings()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: BedrockAgentCoreApp) -> AsyncIterator[None]:
    """Release resources opened by the first invocation at process shutdown."""
    try:
        yield
    finally:
        await run_in_threadpool(settings.close)


app = BedrockAgentCoreApp(lifespan=lifespan)


class BackgroundWork:
    """Keep a Runtime session busy until its accepted worker drains."""

    def add_task[**P](
        self, func: Callable[P, Any], /, *args: P.args, **kwargs: P.kwargs
    ) -> None:
        task = app.add_async_task(func.__name__)

        def complete() -> None:
            try:
                func(*args, **kwargs)
            except Exception:
                log.exception("The agent worker failed")
            finally:
                app.complete_async_task(task)

        try:
            Thread(target=complete, name=func.__name__, daemon=True).start()
        except RuntimeError:
            app.complete_async_task(task)
            raise


async def response_for(request: Invocation, managed: bool) -> JSONResponse:
    """Initialize once, then acknowledge health or an admitted operation."""
    initialized = await settings.initialize(managed)
    background = BackgroundWork()
    if initialized and os.getenv("UNDERCOVER_MEMORY_ID"):
        background.add_task(memory.recover_pending)
    if request.operation == "health":
        return JSONResponse({"healthy": True})
    try:
        await run_in_threadpool(work, request, background)
    except UnconfirmedTake:
        log.warning("The backend did not confirm whether the agent took the work")
        return JSONResponse({"uncertain": True})
    return JSONResponse({"accepted": True})


@app.entrypoint
async def invoke(payload: Any, context: RequestContext) -> JSONResponse:
    """Validate one request, register its work and acknowledge admission."""
    try:
        request = invocation.validate_python(payload)
    except ValidationError as error:
        first = error.errors(include_input=False, include_url=False)[0]
        field = ".".join(str(part) for part in first["loc"]) or "operation"
        return JSONResponse({"error": f"{field}: {first['msg']}"}, status_code=400)

    managed = on_runtime()
    if (
        request.operation != "health"
        and not managed
        and not authorized(context.request_headers)
    ):
        return JSONResponse(
            {"error": "A valid bearer key is required"}, status_code=401
        )
    try:
        return await response_for(request, managed)
    except Refused as error:
        if error.status_code == HTTPStatus.CONFLICT:
            return JSONResponse({"refused": error.detail})
        status = (
            HTTPStatus.BAD_GATEWAY
            if error.status_code == HTTPStatus.UNAUTHORIZED
            else error.status_code
        )
        return JSONResponse({"error": error.detail}, status_code=status)
    except Exception:
        log.exception("The agent could not start the requested work")
        return JSONResponse(
            {"error": "The agent could not start the requested work"}, status_code=503
        )
