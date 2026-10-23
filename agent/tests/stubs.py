"""Scripted tool-calling models and HTTP transport for automated tests."""

import httpx
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


class StubModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def scripted_model(outcome="approve", payout="420.00"):
    decision = {
        "outcome": outcome,
        "payout": payout,
        "calculation": f"Synthetic test payout: USD {payout}.",
        "explanation": (
            "The submitted evidence supports this outcome under section 3. "
            "Refer to a handler for missing information if evidence is incomplete."
        ),
        "policy_sections": ["3", "10", "11"],
    }
    replies = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": name, "args": {}, "id": name}
                for name in ("read_claim", "read_policy_wording", "read_company")
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "record_decision", "args": decision, "id": "decision"}
            ],
        ),
    ]
    if outcome == "approve":
        replies.append(
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "pay_claim", "args": {"amount": payout}, "id": "payment"}
                ],
            )
        )
    replies.append(AIMessage(content="The claim assessment is complete."))
    return StubModel(messages=iter(replies))


def api_client(client):
    def forward(request):
        response = client.request(
            request.method,
            request.url.path,
            content=request.content,
            headers=request.headers,
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    return httpx.Client(
        base_url="http://testserver", transport=httpx.MockTransport(forward)
    )
