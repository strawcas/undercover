"""Run dispatches and nested round evaluations through the same execution path."""

import contextvars

from agent import api, checkpoints, dispatcher


def execute(selection: dict) -> dict:
    """Create a nested evaluation for the improver and work its pinned run."""
    return work(api.start_run(selection))


def work(run: dict) -> dict:
    """Drain an app-created run and clear only its terminal evaluation threads."""
    identity = run["run_id"]
    try:
        contextvars.Context().run(dispatcher.dispatch, identity, run["concurrency"])
        return api.finish_run(identity)
    except Exception:
        api.stop_run(identity, "The agent could not finish this run")
        raise
    finally:
        if run["kind"] == "evaluation":
            checkpoints.clear_threads(api.get_run_threads(identity))
