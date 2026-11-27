"""Call UnderCover as the verified handler and report Copilot progress."""

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid5

from agent import backend_client

backend = backend_client.client()
CONVERSATIONS = "/copilot/conversations"
CLAIMS = "/claims"
CUSTOMERS = "/customers"
DISPATCHES = "/dispatches"
EVALUATIONS = "/evaluations"
SETTINGS = "/settings"
RECEIPT_HEADER = "X-UnderCover-Copilot-Receipt"


@dataclass(frozen=True)
class HandlerClient:
    bearer: str = field(repr=False)

    def call(self, method: str, path: str, **request: Any) -> Any:
        """Send the current handler's bearer without the agent credential."""
        headers = request.pop("headers", {}) | {
            "Authorization": f"Bearer {self.bearer}"
        }
        try:
            result = backend_client.call(
                backend, method, path, headers=headers, **request
            )
        except backend_client.Refused as error:
            raise backend_client.Refused(
                error.status_code, self.public(error.detail)
            ) from None
        return self.public(result)

    def public(self, value: Any) -> Any:
        """Remove any bearer value reflected by the app."""
        if self.bearer and value is not None:
            return json.loads(
                json.dumps(value).replace(self.bearer, "[sign-in removed]")
            )
        return value

    def me(self) -> dict[str, Any]:
        """Resolve the verified identity and permissions for this invocation."""
        return self.call("GET", "/me")

    def read(self, path: str, **request: Any) -> Any:
        """Read an authorized app record."""
        return self.call("GET", path, **request)

    def start(self, path: str, values: dict[str, Any]) -> dict[str, Any]:
        """Start work through the handler's app operation."""
        return self.call("POST", path, json=values)

    def review(
        self,
        claim_id: str,
        action: str,
        revision: int,
        headers: dict[str, str],
        **values: Any,
    ) -> Any:
        """Bind the handler review to the specific version of the decision."""
        return self.call(
            "POST",
            f"{record(CLAIMS, claim_id)}/{action}",
            json=values or None,
            headers=headers | {"If-Match": f'"{revision}"'},
        )


def record(collection: str, identity: str) -> str:
    """Address one record without allowing model input to alter the route."""
    return f"{collection}/{quote(identity, safe='')}"


@dataclass(frozen=True)
class Progress:
    app: HandlerClient
    conversation_id: str
    turn_id: str
    stretch_id: str

    @property
    def path(self) -> str:
        return f"{CONVERSATIONS}/{self.conversation_id}/turns/{self.turn_id}"

    def take(self) -> dict[str, Any]:
        """Claim this stretch and load its server-owned work."""
        return self.app.start(f"{self.path}/take", {"stretch_id": self.stretch_id})

    def action_headers(self, action: str) -> dict[str, str]:
        """Bind the operation to this active stretch and a stable action ID."""
        action_id = uuid5(UUID(self.turn_id), action)
        receipt = f"{self.conversation_id}/{self.turn_id}/{self.stretch_id}/{action_id}"
        return {RECEIPT_HEADER: receipt}

    def step(self, step_id: str, update: dict[str, Any]) -> None:
        """Store or complete one progress card."""
        self.app.call(
            "PUT",
            f"{self.path}/steps/{step_id}",
            json=update | {"stretch_id": self.stretch_id},
        )

    def finish(self, **outcome: Any) -> None:
        """Store the answer, approval card or short failure reason."""
        values = (
            dict(answer=None, failure=None, confirmation=None, usage=None) | outcome
        )
        self.app.call(
            "PUT", f"{self.path}/outcome", json=values | {"stretch_id": self.stretch_id}
        )
