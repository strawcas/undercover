"""Work the waiting queue once and print each decision and final totals."""

import sqlite3
import sys
from collections import Counter
from decimal import Decimal
from typing import Any

from agent.main import validate_model_config
from backend.claims import Claims
from backend.config import backend_url, database_path
from backend.local import local_backend
from backend.storage import Store
from backend.worker import QueueWorker, Runner, default_runner

TOTALS = (
    "Attempted",
    "Decided",
    "Approved",
    "Declined",
    "Referred",
    "Failed runs",
    "Awaiting sign-off",
)


class Report:
    """Print persisted results rather than interpreting model text."""

    def __init__(self):
        self.counts: Counter[str] = Counter()
        self.reported: set[str] = set()

    def record(self, claims: Claims, detail: dict[str, Any]) -> None:
        claim_id = detail["claim_id"]
        self.reported.add(claim_id)
        self.counts["Attempted"] += 1
        last = claims.events(claim_id)[-1]
        if detail["decision"]:
            self._decision(detail)
        else:
            print(f"{claim_id} FAILED - No decision recorded", flush=True)
        if last["status"] == "failed":
            self.counts["Failed runs"] += 1
            print(f"Error: {last['detail']}", flush=True)

    def _decision(self, detail: dict[str, Any]) -> None:
        decision = detail["decision"]
        self.counts["Decided"] += 1
        category = {"approve": "Approved", "decline": "Declined", "refer": "Referred"}
        self.counts[category[decision["outcome"]]] += 1
        state = "No payment recorded"
        if detail["status"] == "awaiting_sign_off":
            self.counts["Awaiting sign-off"] += 1
            state = "Waiting for sign-off"
        elif detail["payment"]:
            state = "Simulated payment recorded"
        print(
            f"{detail['claim_id']} {decision['outcome'].upper()} "
            f"${Decimal(decision['payout']):,.2f} - {state}",
            flush=True,
        )
        print(f"Explanation: {decision['explanation']}", flush=True)
        print(f"Policy sections: {', '.join(decision['policy_sections'])}", flush=True)

    def summary(self) -> None:
        print(" | ".join(f"{name}: {self.counts[name]}" for name in TOTALS), flush=True)


def drain(claims: Claims, runner: Runner, report: Report) -> None:
    """Work the initial waiting queue once, preserving results on interruption."""
    pending = {row["claim_id"] for row in claims.queue() if row["status"] == "waiting"}
    if pending and runner is default_runner:
        validate_model_config()
    worker = QueueWorker(claims, runner, lambda: True, lambda: None)
    try:
        while (detail := worker.step(pending)) is not None:
            report.record(claims, detail)
    finally:
        for row in claims.queue():
            claim_id = row["claim_id"]
            if claim_id in worker.attempted - report.reported:
                report.record(claims, claims.detail(claim_id))


def main(runner: Runner = default_runner) -> int:
    """Run a finite queue session with the configured model and local HTTP tools."""
    report = Report()
    code = 0
    try:
        with local_backend(Store(database_path()), runner, backend_url()) as claims:
            drain(claims, runner, report)
        if report.counts["Failed runs"]:
            code = 1
    except KeyboardInterrupt:
        print(
            "Interrupted. Undecided work can be retried in a new session.",
            file=sys.stderr,
        )
        code = 130
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"Queue processing failed: {error}", file=sys.stderr)
        code = 1
    finally:
        report.summary()
    return code
