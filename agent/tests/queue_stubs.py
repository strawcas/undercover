"""A scripted queue model that selects tools using only returned test state."""

import json
from typing import Any, Self
from uuid import uuid4

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedQueueModel(BaseChatModel):
    order: list[str] = Field(default_factory=list)
    finish_early: bool = False
    final_replies: int = 0
    pending_claim: str = ""
    calls: list[str] = Field(default_factory=list)
    last_messages: list[BaseMessage] = Field(default_factory=list)
    peak_messages: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted_queue"

    def bind_tools(self, tools, **kwargs) -> Self:
        return self

    def _call(self, name: str, args: dict) -> AIMessage:
        self.calls.append(name)
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": name,
                    "args": args,
                    "id": str(uuid4()),
                }
            ],
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        last = messages[-1]
        self.last_messages = list(messages)
        self.peak_messages = max(self.peak_messages, len(messages))
        if self.finish_early and not self.final_replies:
            self.final_replies += 1
            response = AIMessage(content="The queue is complete.")
        elif not isinstance(last, ToolMessage):
            response = self._call("list_waiting_claims", {})
        else:
            response = self._respond(last)
        return ChatResult(generations=[ChatGeneration(message=response)])

    def _respond(self, last: ToolMessage) -> AIMessage:
        if last.name == "list_waiting_claims":
            return self._choose(json.loads(str(last.content)))
        if last.name == "write_todos":
            return self._call("delegate_claim", {"claim_id": self.pending_claim})
        if (
            last.name == "check_completion"
            and json.loads(str(last.content))["complete"]
        ):
            self.final_replies += 1
            return AIMessage(content="The queue workload is complete.")
        return self._call("list_waiting_claims", {})

    def _choose(self, data: dict) -> AIMessage:
        claims = data["claims"]
        if not claims:
            return self._call("check_completion", {})
        indices = {row["claim_id"]: index for index, row in enumerate(claims)}
        priority = {claim_id: index for index, claim_id in enumerate(self.order)}
        chosen = min(
            claims,
            key=lambda row: (
                row["claim_id"] in data["retryable_claim_ids"],
                priority.get(row["claim_id"], len(priority) + indices[row["claim_id"]]),
            ),
        )["claim_id"]
        self.pending_claim = chosen
        todos = [
            {key: item[key] for key in ("id", "claim_id", "text", "status")}
            for item in data["todos"]
        ]
        if any(item["claim_id"] == chosen for item in todos):
            return self._call("delegate_claim", {"claim_id": chosen})
        todos.append(
            dict(
                id=f"claim:{chosen}",
                claim_id=chosen,
                text=f"Assess {chosen}",
                status="to_do",
            )
        )
        return self._call("write_todos", {"items": todos})
