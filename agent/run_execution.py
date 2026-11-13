"""Run dispatches and nested round evaluations through the same execution path."""

import contextvars

from agent import api, checkpoints, dispatcher


def execute(selection: dict) -> dict:
    """Create a pinned run, drain its workers and clear only its evaluation threads."""
    run = api.start_run(selection)
    identity = run["run_id"]
    try:
        contextvars.Context().run(dispatcher.dispatch, identity, run["concurrency"])
        return api.finish_run(identity)
    except Exception:
        api.stop_run(identity)
        raise
    finally:
        if run["kind"] == "evaluation":
            checkpoints.clear_threads(api.get_run_threads(identity))
