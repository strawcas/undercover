"""Optional claim tracing; exporting telemetry never owns business success."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

from langchain_core.callbacks import BaseCallbackHandler
from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler

from backend.diagnostics import diagnose, preserve_primary


@contextmanager
def claim_trace(claim_id: str, thread_id: str) -> Iterator[list[BaseCallbackHandler]]:
    if not all(
        os.getenv(key) for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    ):
        yield []
        return
    try:
        handler = CallbackHandler()
        scope = propagate_attributes(
            session_id=f"{claim_id}:{thread_id}",
            metadata={"claim_id": claim_id, "thread_id": thread_id},
        )
    except Exception as error:
        diagnose("Initialize claim tracing", error)
        yield []
        return
    with scope:
        try:
            yield [handler]
        finally:
            with preserve_primary("Flush claim traces"):
                try:
                    get_client().flush()
                except Exception as error:
                    diagnose("Export claim traces", error)
