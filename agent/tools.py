"""Claim-bound tools that use only the backend's HTTP API."""

from http import HTTPStatus
from typing import Literal

import httpx
from langchain_core.tools import BaseTool, tool


class ClaimTools:
    """Share an HTTP client and run identifiers across agent tools."""

    def __init__(self, client: httpx.Client, claim_id: str, thread_id: str):
        self.client = client
        self.path = f"/api/claims/{claim_id}"
        self.thread_id = thread_id

    def event(self, action: str, status: str, detail: str) -> None:
        response = self.client.post(
            f"{self.path}/events",
            json={
                "thread_id": self.thread_id,
                "action": action,
                "status": status,
                "detail": detail,
            },
        )
        response.raise_for_status()

    def call(self, action: str, method: str, path: str, body: dict | None = None):
        self.event(action, "started", "Requesting backend operation.")
        response = self.client.request(method, path, json=body)
        if response.is_error:
            status = (
                "blocked" if response.status_code == HTTPStatus.CONFLICT else "failed"
            )
            self.event(action, status, f"Backend returned {response.status_code}.")
            return {"error": response.json(), "status": response.status_code}
        self.event(action, "completed", "Backend operation completed.")
        return response.json()

    def tools(self) -> list[BaseTool]:
        @tool
        def read_claim() -> dict:
            """Read the claim, documents, policy, plan, add-ons and history."""
            return self.call("Read claim and customer history", "GET", self.path)

        @tool
        def read_policy_wording() -> dict:
            """Read the full policy wording and version for deciding this claim."""
            return self.call("Read policy wording", "GET", "/api/policy-wording")

        @tool
        def read_company() -> dict:
            """Read the USD payout approval limit and company settings."""
            return self.call("Read payout approval limit", "GET", "/api/company")

        @tool
        def record_decision(
            outcome: Literal["approve", "decline", "refer"],
            payout: str,
            calculation: str,
            explanation: str,
            policy_sections: list[str],
        ) -> dict:
            """Save the outcome, USD payout, calculation, reason and policy sections."""
            return self.call(
                "Save decision",
                "POST",
                f"{self.path}/decision",
                {
                    "thread_id": self.thread_id,
                    "outcome": outcome,
                    "payout": payout,
                    "calculation": calculation,
                    "explanation": explanation,
                    "policy_sections": policy_sections,
                },
            )

        @tool
        def pay_claim(amount: str) -> dict:
            """Record a simulated payment of the exact approved USD amount."""
            return self.call(
                "Request simulated payment",
                "POST",
                f"{self.path}/pay",
                {"amount": amount},
            )

        return [
            read_claim,
            read_policy_wording,
            read_company,
            record_decision,
            pay_claim,
        ]
