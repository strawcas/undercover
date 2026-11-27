"""Keep backend work alive while a model call or grading blocks the worker."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http import HTTPStatus
from threading import Event, Thread

import httpx

from agent.backend_client import Refused

INTERVAL = 60
log = logging.getLogger(__name__)


@contextmanager
def heartbeat(refresh: Callable[[], None]) -> Iterator[None]:
    """Run independent periodic heartbeats for the duration of a job."""
    finished = Event()

    def pulse() -> None:
        while not finished.wait(INTERVAL):
            try:
                refresh()
            except Refused as error:
                if error.status_code == HTTPStatus.CONFLICT:
                    return
                log.warning("The backend refused a heartbeat: %s", error)
            except httpx.RequestError as error:
                log.warning("The backend missed a heartbeat: %s", error)

    worker = Thread(target=pulse, daemon=True, name="undercover-heartbeat")
    worker.start()
    try:
        yield
    finally:
        finished.set()
        worker.join()
