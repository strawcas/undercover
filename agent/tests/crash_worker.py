"""Child process used only by deterministic durability regression tests."""

import os
from dataclasses import dataclass
from threading import Event

from agent.checkpoints import checkpointer
from agent.execution import AttemptBudget
from agent.graph import ClaimWorkflow, build_graph
from agent.lease import ClaimLease
from agent.main import backend
from agent.tests.stubs import scripted_model
from agent.tests.test_graph import initial
from agent.tools import ClaimTools


@dataclass(frozen=True)
class CrashCase:
    mode: str
    claim_id: str
    thread_id: str
    url: str
    path: str


def run(case: CrashCase, ready):
    mode, claim_id, thread_id = case.mode, case.claim_id, case.thread_id
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("LANGFUSE_SECRET_KEY", None)
    os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    os.environ["UNDERCOVER_DB_PATH"] = case.path
    os.environ["UNDERCOVER_BACKEND_URL"] = case.url

    def hold():
        ready.set()
        Event().wait(30)
        raise RuntimeError("The test did not terminate its worker.")

    class CrashWorkflow(ClaimWorkflow):
        def decide(self, state, config):
            result = super().decide(state, config)
            if mode == "decision":
                hold()
            return result

        def pay(self, state):
            result = super().pay(state)
            if mode == "payment":
                hold()
            return result

    budget = AttemptBudget()
    with (
        backend() as client,
        ClaimLease(client, claim_id, thread_id, budget),
        checkpointer() as saver,
    ):
        workflow = CrashWorkflow(
            ClaimTools(client, claim_id, thread_id, budget), scripted_model()
        )
        graph = build_graph(workflow, saver)
        instructions = client.get("/api/agent-instructions").json()["text"]
        graph.invoke(
            initial(claim_id, thread_id, instructions),
            {"configurable": {"thread_id": thread_id}},
            durability="sync",
            interrupt_after=["tools"] if mode == "checkpoint" else None,
        )
        hold()
