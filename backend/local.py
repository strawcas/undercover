"""A local HTTP server for the standalone terminal agent."""

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Thread
from time import monotonic
from urllib.parse import urlsplit

import uvicorn

from backend.claims import Claims
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
        app = create_app(store, runner, auto_run=False)
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
