"""Claim-bound tools that use only the backend's HTTP API."""

from http import HTTPStatus
from threading import Lock
from typing import Literal

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, JsonValue

from agent.execution import AgentRunError, AttemptBudget
from agent.financial import write
from agent.http import request
from backend.diagnostics import diagnose


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

    def __init__(
        self,
        client: httpx.Client,
        claim_id: str,
        thread_id: str,
        budget: AttemptBudget | None = None,
    ):
        self.client = client
        self.path = f"/api/claims/{claim_id}"
        self.thread_id = thread_id
        self.budget = budget or AttemptBudget()
        self.calls = 0
        self.lock = Lock()

    def event(self, action: str, status: str, detail: str) -> None:
        self.budget.check()
        try:
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
        except httpx.HTTPError as error:
            # Events are append-only, so a lost response must not replay them.
            diagnose("Save claim activity", error)

    def model_retry(self, attempt: int) -> None:
        self.event("Retry model request", "started", f"Transport retry {attempt} of 2.")

    def call(
        self, action: str, method: str, path: str, body: dict[str, object] | None = None
    ) -> dict[str, JsonValue]:
        self.budget.check()
        with self.lock:
            if self.calls >= self.budget.tool_calls:
                raise AgentRunError("Assessment tool-call limit exceeded.")
            self.calls += 1
        self.event(action, "started", "Requesting backend operation.")
        if path.endswith(("/decision", "/agent-pay")) and body is not None:
            response = write(self.client, self.path, path, body, self.budget)
        else:
            response = request(
                self.client,
                method,
                path,
                body,
                budget=self.budget,
            )
            retries = response.extensions.get("undercover_retries", 0)
            if retries:
                self.event(action, "started", f"Transport retry {retries} of 2.")
        if response.is_error:
            status = (
                "blocked" if response.status_code == HTTPStatus.CONFLICT else "failed"
            )
            self.event(action, status, f"Backend returned {response.status_code}.")
            return {"error": response.json(), "status": response.status_code}
        self.event(action, "completed", "Backend operation completed.")
        return response.json()

    def read_claim(self) -> dict[str, JsonValue]:
        """Read the claim, documents, policy, plan, add-ons and history."""
        return self.call("Read claim and customer history", "GET", self.path)

    def list_reference_sections(
        self, source: Literal["policy_wording", "claims_manual"]
    ) -> dict[str, JsonValue]:
        """List numbered sections and named notes in one business document."""
        return self.call(
            "List reference sections",
            "GET",
            f"/api/reference-documents/{source}/sections",
        )

    def search_references(
        self, source: Literal["policy_wording", "claims_manual"], query: str
    ) -> dict[str, JsonValue]:
        """Find section IDs using a short query before reading full sections."""
        return self.call(
            "Search reference sections",
            "POST",
            f"{self.path}/reference-search",
            {"thread_id": self.thread_id, "source": source, "query": query},
        )

    def read_reference_section(
        self, source: Literal["policy_wording", "claims_manual"], section_id: str
    ) -> dict[str, JsonValue]:
        """Read a selected section in full and preserve its text on this claim."""
        return self.call(
            "Read reference section",
            "POST",
            f"{self.path}/reference-reads",
            {"thread_id": self.thread_id, "source": source, "section_id": section_id},
        )

    def read_company(self) -> dict[str, JsonValue]:
        """Read the USD payout approval limit and company settings."""
        return self.call("Read payout approval limit", "GET", "/api/company")

    def record_decision(self, **decision: object) -> dict[str, JsonValue]:
        """Save the outcome, USD payout, calculation, reason and policy sections."""
        return self.call(
            "Save decision",
            "POST",
            f"{self.path}/decision",
            {"thread_id": self.thread_id, **decision},
        )

    def pay_claim(self, amount: str) -> dict[str, JsonValue]:
        """Record a simulated payment of the exact approved USD amount."""
        return self.call(
            "Request simulated payment",
            "POST",
            f"{self.path}/agent-pay",
            {"amount": amount, "thread_id": self.thread_id},
        )

    def tools(self) -> list[BaseTool]:
        tools: list[BaseTool] = [
            StructuredTool.from_function(method)
            for method in (
                self.read_claim,
                self.list_reference_sections,
                self.search_references,
                self.read_reference_section,
                self.read_company,
            )
        ]
        tools.extend(
            [
                StructuredTool.from_function(
                    self.record_decision, args_schema=DecisionFields
                ),
                StructuredTool.from_function(self.pay_claim),
            ]
        )
        return tools
