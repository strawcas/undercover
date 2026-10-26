"""A local HTTP server for the standalone terminal agent."""

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Thread
from time import monotonic
from urllib.parse import urlsplit

import httpx
import uvicorn

from backend.claims import Claims
from backend.config import database_identity
from backend.main import create_app
from backend.storage import Store
from backend.worker import Runner


def local_address(url: str) -> tuple[str, int]:
    """Accept only local HTTP addresses that the terminal command can serve."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("UNDERCOVER_BACKEND_URL must be a local HTTP address.")
    return parsed.hostname, parsed.port or 80


def wait_started(server: uvicorn.Server, thread: Thread) -> None:
    deadline = monotonic() + 10
    while not server.started:
        if not thread.is_alive() or monotonic() > deadline:
            raise RuntimeError(
                "Local backend could not start. Check database ownership."
            )
        thread.join(timeout=0.05)


def serve(server: uvicorn.Server, listener: socket.socket) -> None:
    """Let the caller report Uvicorn's startup exits as terminal errors."""
    try:
        server.run(sockets=[listener])
    except SystemExit:
        return


@contextmanager
def local_backend(store: Store, runner: Runner, url: str) -> Iterator[Claims]:
    """Serve agent tools until the terminal command has finished."""
    host, port = local_address(url)
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.bind((host, port))
        app = create_app(store)
        server = uvicorn.Server(
            uvicorn.Config(
                app, log_level="error", access_log=False, timeout_graceful_shutdown=5
            )
        )
        thread = Thread(
            target=lambda: serve(server, listener),
            daemon=True,
            name="claims-api",
        )
        thread.start()
        try:
            wait_started(server, thread)
            yield app.state.claims
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def existing_backend(store: Store, url: str) -> bool:
    """Attach only to an UnderCover API serving the command's own database."""
    local_address(url)
    try:
        response = httpx.get(f"{url}/api/health", timeout=1)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return False
    except httpx.HTTPError as error:
        raise RuntimeError(
            "Backend address is occupied but its API is unavailable."
        ) from error
    if not response.is_success or response.headers.get(
        "X-UnderCover-Database"
    ) != database_identity(store.path):
        raise RuntimeError(
            "Backend address is occupied by another service or database."
        )
    return True


@contextmanager
def agent_backend(store: Store, runner: Runner, url: str) -> Iterator[Claims | None]:
    """Use the running app or own a temporary backend for a standalone command."""
    if existing_backend(store, url):
        yield None
    else:
        with local_backend(store, runner, url) as claims:
            yield claims
