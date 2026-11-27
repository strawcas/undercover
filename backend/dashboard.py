"""Serve the packaged dashboard and its browser routes."""

from http import HTTPStatus
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from backend.database import ROOT

DIST = ROOT / "dashboard" / "dist"
INDEX = "index.html"


class DashboardFiles(StaticFiles):
    """Fall back to the dashboard entry point for browser navigation."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != HTTPStatus.NOT_FOUND:
                raise
        return await super().get_response(INDEX, scope)


def install_dashboard(app: FastAPI, directory: Path = DIST) -> None:
    """Serve a built dashboard on browser paths after normal route matching."""
    if not (directory / INDEX).is_file():
        return
    files = DashboardFiles(directory=directory)

    async def browser_fallback(request: Request, error: Exception) -> Response:
        path = request.url.path
        if (
            request.method not in {"GET", "HEAD"}
            or path == "/api"
            or path.startswith("/api/")
        ):
            return await http_exception_handler(request, cast(HTTPException, error))
        return await files.get_response(files.get_path(request.scope), request.scope)

    app.add_exception_handler(HTTPStatus.NOT_FOUND, browser_fallback)
