"""Temporary local services run in separate Windows-compatible processes."""

import multiprocessing
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event
from threading import Thread
from time import monotonic, sleep
from typing import Literal

import httpx
import uvicorn

from agent.execution import Runner
from agent.service import create_app as agent_app
from agent.service_worker import ModelFactory
from backend.config import database_target
from backend.diagnostics import preserve_primary
from backend.local import local_address
from backend.main import create_app as backend_app
from backend.storage import Store


@dataclass(frozen=True)
class Settings:
    kind: Literal["backend", "agent"]
    url: str
    runner: Runner | None = None
    model_factory: ModelFactory | None = None


def serve(settings: Settings, stopped: Event) -> None:
    host, port = local_address(settings.url)
    app = (
        backend_app(Store(database_target()))
        if settings.kind == "backend"
        else agent_app(settings.runner, settings.model_factory)
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=5,
        )
    )

    def shutdown() -> None:
        stopped.wait()
        server.should_exit = True

    Thread(target=shutdown, name="local-service-stop", daemon=True).start()
    server.run()


def available(settings: Settings, identity: str) -> bool:
    local_address(settings.url)
    path = "/api/health" if settings.kind == "backend" else "/health"
    try:
        response = httpx.get(settings.url + path, timeout=1)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return False
    except httpx.HTTPError as error:
        raise RuntimeError(
            f"The {settings.kind} address is occupied but unavailable."
        ) from error
    if (
        not response.is_success
        or response.headers.get("X-UnderCover-Database") != identity
        or (
            settings.kind == "agent"
            and response.headers.get("X-UnderCover-Service") != "agent"
        )
    ):
        raise RuntimeError(
            f"The {settings.kind} address is occupied by another service or database."
        )
    return True


def stop_process(process: BaseProcess, stopped: Event) -> None:
    try:
        stopped.set()
        process.join(timeout=10)
    finally:
        try:
            with preserve_primary("Terminate temporary service"):
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
        finally:
            with preserve_primary("Close temporary service process"):
                process.close()


@contextmanager
def local_service(settings: Settings, identity: str) -> Iterator[None]:
    """Attach to a matching app, or own a temporary child until the command ends."""
    if available(settings, identity):
        yield
        return
    context = multiprocessing.get_context("spawn")
    stopped = context.Event()
    process = context.Process(
        target=serve, args=(settings, stopped), name=f"undercover-{settings.kind}"
    )
    process.start()
    try:
        deadline = monotonic() + 25
        while not available(settings, identity):
            if not process.is_alive() or monotonic() >= deadline:
                raise RuntimeError(
                    f"The local {settings.kind} could not start. "
                    "Check database ownership."
                )
            sleep(0.1)
        yield
    finally:
        with preserve_primary(f"Stop temporary {settings.kind}"):
            stop_process(process, stopped)
