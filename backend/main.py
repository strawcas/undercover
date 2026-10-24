"""The UnderCover backend."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.claims import ClaimError, Claims
from backend.config import database_path
from backend.ownership import DatabaseOwner
from backend.routes import router
from backend.storage import Store
from backend.worker import Runner, default_runner


def create_app(store: Store | None = None, runner: Runner = default_runner) -> FastAPI:
    """Create the API with isolated storage available to tests."""
    repository = store or Store(database_path())

    claims = Claims(repository)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with DatabaseOwner(repository.path):
            repository.initialize()
            claims.recover_interrupted()
            yield

    app = FastAPI(title="UnderCover", lifespan=lifespan)
    app.state.claims = claims
    app.state.runner = runner

    @app.exception_handler(ClaimError)
    async def claim_error(_request: Request, exc: ClaimError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    app.include_router(router)
    return app


app = create_app()
