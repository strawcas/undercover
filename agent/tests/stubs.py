"""Scripted tool-calling models and HTTP transport for automated tests."""

import json
from typing import Any

import httpx
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field


class StubModel(GenericFakeChatModel):
    first_messages: list[BaseMessage] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        def structured(messages, config):
            reply = self.invoke(messages, config=config)
            return schema.model_validate_json(reply.text)

        return RunnableLambda(structured)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if not self.first_messages:
            self.first_messages = list(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def scripted_model(
    outcome="approve",
    payout="420.00",
    policy_sections=("3", "10", "11"),
    manual_sections=("6",),
):
    decision = {
        "outcome": outcome,
        "payout": payout,
        "calculation": f"Synthetic test payout: USD {payout}.",
        "explanation": (
            "The submitted evidence supports this outcome under section 3. "
            "Refer to a handler for missing information if evidence is incomplete."
        ),
        "policy_sections": list(policy_sections),
        "manual_sections": list(manual_sections),
    }
    replies = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": name, "args": {}, "id": name}
                for name in ("read_claim", "read_company")
            ],
        ),
    ]
    replies[1:1] = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_references",
                    "args": {"source": "claims_manual", "query": "payout"},
                    "id": "search",
                },
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_reference_section",
                    "args": {"source": source, "section_id": key},
                    "id": f"{source}-{key}",
                }
                for source, keys in (
                    ("policy_wording", policy_sections),
                    ("claims_manual", manual_sections),
                )
                for key in keys
            ],
        ),
    ]
    replies.append(AIMessage(content="The evidence supports the final assessment."))
    replies.append(AIMessage(content=json.dumps(decision)))
    return StubModel(messages=iter(replies))


def api_client(client, seen=None, after_response=None):
    def forward(request):
        if seen is not None:
            seen.append((request.method, request.url.path))
        response = client.request(
            request.method,
            request.url.path,
            content=request.content,
            headers=request.headers,
        )
        if after_response is not None:
            after_response(request, response)
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    return httpx.Client(
        base_url="http://testserver", transport=httpx.MockTransport(forward)
    )
