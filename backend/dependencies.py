"""Request-scoped database transactions shared by API dependencies."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Connection

from backend.database import engine


def database() -> Iterator[Connection]:
    """Commit before the response and its background work begin."""
    with engine().begin() as connection:
        yield connection


Database = Annotated[Connection, Depends(database, scope="function")]
