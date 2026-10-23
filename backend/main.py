"""The UnderCover backend."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.claims import ClaimError, Claims
from backend.routes import router
from backend.storage import ROOT, Store


def create_app(store: Store | None = None) -> FastAPI:
    """Create the API with isolated storage available to tests."""
    repository = store or Store(
        Path(os.getenv("UNDERCOVER_DB_PATH", ROOT / "undercover.db"))
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        repository.initialize()
        yield

    app = FastAPI(title="UnderCover", lifespan=lifespan)
    app.state.claims = Claims(repository)

    @app.exception_handler(ClaimError)
    async def claim_error(_request: Request, exc: ClaimError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    app.include_router(router)
    return app


app = create_app()
