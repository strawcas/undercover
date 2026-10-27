"""Report useful failure locations without logging exception bodies or locals."""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from traceback import walk_tb
from uuid import uuid4

logger = logging.getLogger("undercover")


def diagnose(operation: str, error: BaseException) -> str:
    """Correlate a safe public reference with exception types and code locations."""
    reference = uuid4().hex
    chain: list[str] = []
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        frames = ", ".join(
            f"{Path(frame.f_code.co_filename).name}:{line}:{frame.f_code.co_name}"
            for frame, line in walk_tb(current.__traceback__)
        )
        chain.append(f"{type(current).__name__} [{frames}]")
        current = current.__cause__ or current.__context__
    logger.error("Diagnostic %s | %s | %s", reference, operation, " <- ".join(chain))
    return f"Diagnostic {reference}"


@contextmanager
def preserve_primary(operation: str) -> Iterator[None]:
    """Keep an active failure when cleanup also fails; otherwise surface cleanup."""
    primary = sys.exception()
    try:
        yield
    except Exception as cleanup_error:
        reference = diagnose(operation, cleanup_error)
        if primary is None:
            raise
        primary.add_note(f"{operation} also failed. {reference}.")
