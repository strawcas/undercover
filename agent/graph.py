"""A claim's visible reasoning, decision, approval and payment workflow."""

import json
from collections.abc import Callable
from time import sleep
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from pydantic import JsonValue, ValidationError

from agent.execution import AgentRunError
from agent.http import MAX_RETRIES
from agent.middleware import transient_failure
from agent.tools import ClaimTools, DecisionFields
from backend.claim_models import ClaimDetail


class ClaimState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    claim_id: str
    thread_id: str
    run_id: str
    attempt_number: int
    instructions: str
    decision: dict[str, JsonValue] | None
    saved_claim: dict[str, JsonValue] | None
    model_calls: int


class ClaimWorkflow:
    """Keep business ordering in named nodes and backend enforcement in HTTP."""

    def __init__(
        self, tools: ClaimTools, model: BaseChatModel | Callable[[], BaseChatModel]
    ):
        self.tools = tools
        self._model = model
        self.bound_tools = tools.tools()
        self.model_calls = 0

    @property
    def model(self) -> BaseChatModel:
        if not isinstance(self._model, BaseChatModel):
            self._model = self._model()
        return self._model

    def invoke_model(
        self, runnable: Runnable, messages: list[AnyMessage], config: RunnableConfig
    ):
        for attempt in range(MAX_RETRIES + 1):
            self.tools.budget.check()
            usage = self.tools.reserve_call("model")
            self.model_calls = usage.model_calls
            try:
                result = runnable.invoke(messages, config=config)
                self.tools.budget.check()
                if (
                    isinstance(result, AIMessage)
                    and result.response_metadata.get("done_reason") == "length"
                ):
                    raise AgentRunError(
                        "Ollama stopped at its context or generation limit. Increase "
                        "OLLAMA_NUM_CTX in .env. Run uv run -m agent to retry eligible "
                        "Waiting claims. Two failed assessments require handler review."
                    )
                return result
            except Exception as error:
                if attempt == MAX_RETRIES or not transient_failure(error):
                    raise
                self.tools.model_retry(attempt + 1)
                sleep(0.25 * 2**attempt)
        raise AssertionError("Unreachable provider retry state")

    @staticmethod
    def messages(state: ClaimState) -> list[AnyMessage]:
        return [SystemMessage(content=state["instructions"]), *state["messages"]]

    def agent(self, state: ClaimState, config: RunnableConfig) -> dict[str, Any]:
        self.tools.event(
            "Assess claim",
            "started",
            "Reasoning from the claim and selected policy evidence.",
        )
        reply = self.invoke_model(
            self.model.bind_tools(self.bound_tools), self.messages(state), config
        )
        self.tools.event("Assess claim", "completed", "The reasoning step completed.")
        return {"messages": [reply], "model_calls": self.model_calls}

    def decide(self, state: ClaimState, config: RunnableConfig) -> dict[str, Any]:
        existing = ClaimDetail.model_validate(self.tools.read_claim())
        if existing.decision:
            decision = DecisionFields.model_validate(
                {
                    key: getattr(existing.decision, key)
                    for key in DecisionFields.model_fields
                }
            )
            return {
                "decision": decision.model_dump(mode="json"),
                "saved_claim": existing.model_dump(mode="json"),
                "model_calls": self.tools.usage.model_calls,
            }
        try:
            answer = self.invoke_model(
                self.model.with_structured_output(DecisionFields),
                [
                    *self.messages(state),
                    HumanMessage(
                        content="Provide the final structured assessment, including "
                        "the payout calculation and the sections you read."
                    ),
                ],
                config,
            )
            decision = DecisionFields.model_validate(answer)
        except ValidationError:
            return {
                "messages": [
                    HumanMessage(
                        content="The structured decision was invalid. Correct it "
                        "using the policy evidence and the payout tool."
                    )
                ],
                "decision": None,
            }
        result = self.tools.record_decision(**decision.model_dump())
        if "error" in result:
            return {
                "messages": [
                    HumanMessage(
                        content="The backend refused the decision: "
                        + json.dumps(result)
                    )
                ],
                "decision": None,
            }
        saved = ClaimDetail.model_validate(self.tools.read_claim())
        return {
            "decision": decision.model_dump(mode="json"),
            "saved_claim": saved.model_dump(mode="json"),
            "model_calls": self.model_calls,
        }

    def approval(self, state: ClaimState) -> dict[str, Any]:
        claim = ClaimDetail.model_validate(self.tools.read_claim())
        if claim.sign_off is None:
            self.tools.event(
                "Wait for handler sign-off",
                "blocked",
                "The approved payout is held until a handler signs off.",
            )
            interrupt(
                {
                    "claim_id": state["claim_id"],
                    "thread_id": state["thread_id"],
                    "decision": state["decision"],
                }
            )
        claim = ClaimDetail.model_validate(self.tools.read_claim())
        if claim.sign_off is None:
            raise AgentRunError("No handler sign-off is saved for this payout.")
        return {"saved_claim": claim.model_dump(mode="json")}

    def pay(self, state: ClaimState) -> dict[str, Any]:
        claim = ClaimDetail.model_validate(self.tools.read_claim())
        if claim.decision is None or claim.decision.outcome != "approve":
            raise AgentRunError("Only a saved approval can reach the payment step.")
        result = self.tools.pay_claim(claim.decision.payout)
        if "error" in result:
            raise AgentRunError("The backend refused the simulated payment.")
        return {
            "saved_claim": ClaimDetail.model_validate(
                self.tools.read_claim()
            ).model_dump(mode="json")
        }


def after_agent(state: ClaimState) -> str:
    message = state["messages"][-1]
    return (
        "tools" if isinstance(message, AIMessage) and message.tool_calls else "decide"
    )


def after_decide(state: ClaimState) -> str:
    if state["decision"] is None:
        return "agent"
    claim = ClaimDetail.model_validate(state["saved_claim"])
    if claim.decision is None or claim.decision.outcome != "approve":
        return END
    return "approval" if claim.status == "awaiting_sign_off" else "pay"


def build_graph(workflow: ClaimWorkflow, saver: BaseCheckpointSaver):
    builder = StateGraph(ClaimState)
    builder.add_node("agent", workflow.agent)
    builder.add_node("tools", ToolNode(workflow.bound_tools))
    builder.add_node("decide", workflow.decide)
    builder.add_node("approval", workflow.approval)
    builder.add_node("pay", workflow.pay)
    builder.add_conditional_edges(
        START, initial_step, ["agent", "approval", "pay", END]
    )
    builder.add_conditional_edges("agent", after_agent, ["tools", "decide"])
    builder.add_edge("tools", "agent")
    builder.add_conditional_edges(
        "decide", after_decide, ["agent", "approval", "pay", END]
    )
    builder.add_edge("approval", "pay")
    builder.add_edge("pay", END)
    return builder.compile(checkpointer=saver)


def initial_step(state: ClaimState) -> str:
    return after_decide(state) if state["decision"] is not None else "agent"
