"""A stub model, so that tests never call a real model, replies to script it, and
the claim graph on it."""

import itertools
import json

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from agent import api, harness

DEDUCTIBLES = {"economy": 100, "premium": 50, "first": 0}


class StubModel(GenericFakeChatModel):
    """A chat model that gives scripted replies, or raises a scripted error,
    and keeps the prompts it was sent."""

    prompts: list[list[BaseMessage]] = []
    tool_names: list[str] = []

    def bind_tools(self, tools, **kwargs):
        self.tool_names = [tool.name for tool in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(messages)
        reply = next(self.messages)
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            reply = reply(messages)
        message = AIMessage(content=reply) if isinstance(reply, str) else reply
        return ChatResult(generations=[ChatGeneration(message=message)])


CALL_IDS = itertools.count()


def stub(*replies):
    return StubModel(messages=iter(replies))


def call(name, text="", **args):
    """A reply that calls one tool, with an id of its own as a real model gives."""
    tool_call = {"name": name, "args": args, "id": f"call-{next(CALL_IDS)}"}
    return AIMessage(content=text, tool_calls=[tool_call])


def record(decision, payout):
    return call(
        "record_decision",
        decision=decision,
        payout=payout,
        section="Section 3, Trip Delay",
        reason="The delay was covered.",
    )


def calculate(payout, deductible=50):
    """Ask the real calculator for a matching emergency medical payout."""
    return call(
        "calculate_payout",
        benefit="emergency_medical",
        paid_by_others=0,
        lines=[
            {
                "description": "Medical expense",
                "amount": payout + deductible,
                "quantity": 1,
                "kind": "expense",
                "nights": 0,
                "hours": 0,
            }
        ],
    )


def calculate_for_claim(claim_id, payout):
    claim = api.get_claim(claim_id)
    policy = api.get_policy(claim["policy_id"])
    return calculate(payout, DEDUCTIBLES[policy["plan"]])


def calculate_from_policy_reply(payout):
    def calculate_after_reading_policy(messages):
        policy = next(
            json.loads(message.text)
            for message in reversed(messages)
            if isinstance(message, ToolMessage) and message.name == "get_policy"
        )
        return calculate(payout, DEDUCTIBLES[policy["plan"]])

    return calculate_after_reading_policy


def decide(decision, payout):
    """The replies of a claim agent that reads its claim and decides it, which ends
    its work on the claim."""
    replies = [call("get_claim")]
    if decision == "approve":
        replies.append(calculate(payout))
    return [*replies, record(decision, payout)]


def graph_on(model, saver=None):
    """The claim graph on a stub model, saving its progress in memory."""
    return harness.claim_graph(model, saver or InMemorySaver())
