"""Claim-bound tools that use only the backend's HTTP API."""

from http import HTTPStatus
from typing import Literal

import httpx
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict


class DecisionFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["approve", "decline", "refer"]
    payout: str
    calculation: str
    explanation: str
    policy_sections: list[str]
    manual_sections: list[str]


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
        def list_reference_sections(
            source: Literal["policy_wording", "claims_manual"],
        ) -> dict:
            """List numbered sections and named notes in one business document."""
            return self.call(
                "List reference sections",
                "GET",
                f"/api/reference-documents/{source}/sections",
            )

        @tool
        def search_references(
            source: Literal["policy_wording", "claims_manual"], query: str
        ) -> dict:
            """Find section IDs using a short query before reading full sections."""
            return self.call(
                "Search reference sections",
                "POST",
                f"{self.path}/reference-search",
                {
                    "thread_id": self.thread_id,
                    "source": source,
                    "query": query,
                },
            )

        @tool
        def read_reference_section(
            source: Literal["policy_wording", "claims_manual"], section_id: str
        ) -> dict:
            """Read a selected section in full and preserve its text on this claim."""
            return self.call(
                "Read reference section",
                "POST",
                f"{self.path}/reference-reads",
                {
                    "thread_id": self.thread_id,
                    "source": source,
                    "section_id": section_id,
                },
            )

        @tool
        def read_company() -> dict:
            """Read the USD payout approval limit and company settings."""
            return self.call("Read payout approval limit", "GET", "/api/company")

        @tool(args_schema=DecisionFields)
        def record_decision(**decision: object) -> dict:
            """Save the outcome, USD payout, calculation, reason and policy sections."""
            return self.call(
                "Save decision",
                "POST",
                f"{self.path}/decision",
                {"thread_id": self.thread_id, **decision},
            )

        @tool
        def pay_claim(amount: str) -> dict:
            """Record a simulated payment of the exact approved USD amount."""
            return self.call(
                "Request simulated payment",
                "POST",
                f"{self.path}/agent-pay",
                {"amount": amount, "thread_id": self.thread_id},
            )

        return [
            read_claim,
            list_reference_sections,
            search_references,
            read_reference_section,
            read_company,
            record_decision,
            pay_claim,
        ]
