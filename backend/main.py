"""The UnderCover backend."""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel

from backend.claims import ClaimError, Claims
from backend.config import backend_url, database_path
from backend.ownership import DatabaseOwner
from backend.reference_routes import router as reference_router
from backend.routes import router
from backend.run_routes import router as run_router
from backend.storage import Store
from backend.worker import QueueWorker, Runner, api_ready, default_runner


def create_app(
    store: Store | None = None,
    runner: Runner = default_runner,
    *,
    auto_run: bool = True,
    worker_ready: Callable[[], bool] | None = None,
    queue_model: BaseChatModel | None = None,
) -> FastAPI:
    """Create the API with isolated storage available to tests."""
    repository = store or Store(database_path())

    claims = Claims(repository)
    instance_id = str(uuid4())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        owner = DatabaseOwner(repository.path).acquire()
        worker = None

        def release_after_shutdown():
            if worker is not None and worker.stopping.is_set():
                owner.release()

        try:
            repository.initialize()
            claims.recover_interrupted()
            if auto_run:
                url = backend_url()
                worker = QueueWorker(
                    claims,
                    runner,
                    worker_ready or (lambda: api_ready(url, instance_id)),
                    release_after_shutdown,
                    queue_model=queue_model,
                )
                _app.state.worker = worker
                worker.start()
            yield
        finally:
            if worker is None or await asyncio.to_thread(worker.stop):
                owner.release()

    app = FastAPI(title="UnderCover", lifespan=lifespan)
    app.state.claims = claims
    app.state.runner = runner
    app.state.instance_id = instance_id

    @app.exception_handler(ClaimError)
    async def claim_error(_request: Request, exc: ClaimError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    app.include_router(router)
    app.include_router(run_router)
    app.include_router(reference_router)
    return app


app = create_app()
