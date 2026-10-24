"""The UnderCover backend."""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.claims import ClaimError, Claims
from backend.config import backend_url, database_path
from backend.ownership import DatabaseOwner
from backend.routes import router
from backend.storage import Store
from backend.worker import QueueWorker, Runner, api_ready, default_runner


def create_app(
    store: Store | None = None,
    runner: Runner = default_runner,
    *,
    auto_run: bool = True,
    worker_ready: Callable[[], bool] | None = None,
) -> FastAPI:
    """Create the API with isolated storage available to tests."""
    repository = store or Store(database_path())

    claims = Claims(repository)
    instance_id = str(uuid4())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        owner = DatabaseOwner(repository.path).acquire()
        worker = None
        try:
            repository.initialize()
            claims.recover_interrupted()
            if auto_run:
                url = backend_url()
                worker = QueueWorker(
                    claims,
                    runner,
                    worker_ready or (lambda: api_ready(url, instance_id)),
                    owner.release,
                )
                _app.state.worker = worker
                worker.start()
            yield
        finally:
            if worker is None:
                owner.release()
            else:
                await asyncio.to_thread(worker.stop)

    app = FastAPI(title="UnderCover", lifespan=lifespan)
    app.state.claims = claims
    app.state.runner = runner
    app.state.instance_id = instance_id

    @app.exception_handler(ClaimError)
    async def claim_error(_request: Request, exc: ClaimError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    app.include_router(router)
    return app


app = create_app()
