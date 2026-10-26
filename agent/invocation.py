"""Bound helper execution while fencing late responses through attempt IDs."""

from concurrent.futures import Future, wait
from datetime import UTC, datetime
from threading import Thread
from time import monotonic

from agent.execution import (
    AgentRunError,
    Execution,
    QueueStopped,
    Record,
    Runner,
    attempt_budget,
)


def invoke_helper(runner: Runner, entry: Record, execution: Execution) -> str:
    remaining = (
        datetime.fromisoformat(entry["deadline_at"]) - datetime.now(UTC)
    ).total_seconds()
    deadline = monotonic() + max(0, remaining)
    future: Future[str] = Future()

    def invoke():
        token = attempt_budget.set((deadline, execution.cancel_active))
        try:
            future.set_result(runner(entry["claim_id"], entry["thread_id"]))
        except BaseException as failure:
            future.set_exception(failure)
        finally:
            attempt_budget.reset(token)

    Thread(target=invoke, name=f"claim-{entry['claim_id']}", daemon=True).start()
    while True:
        if execution.cancel_active.is_set():
            raise QueueStopped
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise AgentRunError("Assessment deadline exceeded.")
        done, _ = wait([future], timeout=min(remaining, 0.1))
        if done:
            return future.result()
