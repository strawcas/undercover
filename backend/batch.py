"""Start one scoped queue agent and report persisted results from the terminal."""

import argparse
import signal
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from decimal import Decimal
from threading import current_thread, main_thread

import httpx

from agent.execution import AttemptResult, Execution, Runner
from agent.main import AgentRunError
from agent.queue import run_queue
from backend.claim_models import ClaimDetail
from backend.config import backend_url, database_path, run_scope
from backend.diagnostics import preserve_primary
from backend.harness_models import Counts, Run
from backend.local import agent_backend
from backend.storage import Store
from backend.worker import default_runner, reconcile_execution

TOTALS = (
    ("Attempted", "worked"),
    ("Decided", "decided"),
    ("Approved", "approved"),
    ("Declined", "declined"),
    ("Referred", "referred"),
    ("Failed runs", "failed_assessments"),
    ("Awaiting sign-off", "awaiting_sign_off"),
    ("Assessment attempts", "assessment_attempts"),
    ("Handler review", "handler_review"),
    ("Unpaid approvals", "unpaid_approvals"),
    ("Waiting", "waiting_remaining"),
)


class RunParser(argparse.ArgumentParser):
    def error(self, message: str):
        raise ValueError(message)


def arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = RunParser(description="Work a finite UnderCover claims queue.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--cap",
        "--max-claims",
        dest="max_claims",
        type=int,
        help="Maximum distinct claims for this run.",
    )
    scope.add_argument("--claim-id", help="Work only this eligible claim.")
    return parser.parse_args(argv)


class Report:
    """Print each attempt's stored outcome and final backend-derived totals."""

    def record(self, result: AttemptResult) -> None:
        entry, detail = result.attempt, result.claim
        decision = detail.decision
        if decision:
            self._decision(detail)
        else:
            print(f"{detail.claim_id} FAILED - No decision recorded", flush=True)
        if entry.error:
            print(f"Error: {entry.error}", flush=True)

    @staticmethod
    def _decision(detail: ClaimDetail) -> None:
        decision = detail.decision
        if decision is None:
            return
        payment = "No payment recorded"
        if detail.status == "awaiting_sign_off":
            payment = "Waiting for sign-off"
        elif detail.payment:
            payment = "Simulated payment recorded"
        print(
            f"{detail.claim_id} {decision.outcome.upper()} "
            f"${Decimal(decision.payout):,.2f} - {payment}",
            flush=True,
        )
        print(f"Explanation: {decision.explanation}", flush=True)
        print(f"Policy sections: {', '.join(decision.policy_sections)}", flush=True)
        print(f"Manual sections: {', '.join(decision.manual_sections)}", flush=True)

    @staticmethod
    def summary(result: Run | None) -> None:
        counts = result.counts.model_dump() if result else Counts().model_dump()
        counts["decided"] = counts["approved"] + counts["declined"] + counts["referred"]
        print(
            " | ".join(f"{label}: {counts[key]}" for label, key in TOTALS), flush=True
        )


@contextmanager
def interruptible(context: Execution):
    if current_thread() is not main_thread():
        yield
        return

    def interrupt(_signal, _frame):
        context.stopping.set()
        context.cancel_active.set()
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGINT, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def main(runner: Runner = default_runner, argv: Sequence[str] = ()) -> int:
    """Explicitly work a full, capped or named claims queue."""
    report = Report()
    context = Execution(on_result=report.record)
    code = 0
    try:
        parsed = arguments(argv)
        scope = run_scope(parsed.max_claims, parsed.claim_id)
        with agent_backend(Store(database_path()), runner, backend_url()) as claims:
            try:
                with interruptible(context):
                    result = run_queue(scope, runner, execution=context)
            finally:
                if claims is not None:
                    with preserve_primary("Reconcile terminal run"):
                        reconcile_execution(claims, context)
        if (
            result.status != "completed"
            or result.counts.handler_review
            or result.counts.unpaid_approvals
        ):
            code = 1
    except KeyboardInterrupt:
        print("Interrupted. Undecided work remains recoverable.", file=sys.stderr)
        code = 130
    except (
        OSError,
        ValueError,
        RuntimeError,
        sqlite3.Error,
        httpx.HTTPError,
        AgentRunError,
    ) as error:
        print(f"Queue processing failed: {error}", file=sys.stderr)
        code = 1
    finally:
        report.summary(context.result)
    return code
