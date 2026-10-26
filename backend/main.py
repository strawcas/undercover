"""The UnderCover backend."""

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.claims import ClaimError, Claims
from backend.config import database_identity, database_path
from backend.ownership import DatabaseOwner
from backend.reference_routes import router as reference_router
from backend.routes import router
from backend.run_routes import router as run_router
from backend.runs import Runs
from backend.storage import Store


def create_app(store: Store | None = None) -> FastAPI:
    """Create the API with isolated storage available to tests."""
    repository = store or Store(database_path())

    claims = Claims(repository)
    instance_id = str(uuid4())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        owner = DatabaseOwner(repository.path).acquire()
        try:
            repository.initialize()
            claims.recover_interrupted()
            yield
        finally:
            try:
                Runs(repository).recover("Backend stopped during this run.")
            finally:
                owner.release()

    app = FastAPI(title="UnderCover", lifespan=lifespan)
    app.state.claims = claims
    app.state.instance_id = instance_id
    app.state.database_id = database_identity(repository.path)

    @app.exception_handler(ClaimError)
    async def claim_error(_request: Request, exc: ClaimError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    app.include_router(router)
    app.include_router(run_router)
    app.include_router(reference_router)
    return app


app = create_app()
