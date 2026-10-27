"""One planning agent works a finite, backend-enforced claim scope."""

from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware.types import InputAgentState
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from pydantic import TypeAdapter

from agent.execution import (
    AgentRunError,
    AttemptBudget,
    Execution,
    QueueStopped,
    Runner,
)
from agent.http import request
from agent.main import backend, model, run, validate_model_config
from agent.middleware import ProviderRetry, QueueContext
from agent.queue_tools import QueueTools
from backend.diagnostics import diagnose, preserve_primary
from backend.harness_models import Run, Scope
from backend.reference_models import InstructionsResponse


def claim_runner(claim_id: str, thread_id: str, *, budget: AttemptBudget) -> str:
    return run(claim_id, thread_id, budget=budget)


def work_queue(tools: QueueTools, registered: Run, llm: BaseChatModel | None) -> None:
    """Plan, delegate and explicitly check for missed work."""
    try:
        validate_model_config()
    except ValueError as failure:
        raise AgentRunError(str(failure)) from failure
    response = request(tools.client, "GET", "/api/agent-instructions")
    response.raise_for_status()
    instructions = InstructionsResponse.model_validate(response.json())
    calls = 10 + 10 * registered.claim_limit
    agent = create_agent(
        llm or model(),
        tools=tools.tools(),
        system_prompt=instructions.queue_text,
        middleware=[QueueContext(tools.execution.stopping, calls), ProviderRetry()],
    )
    state: InputAgentState = {
        "messages": [
            HumanMessage(
                content=f"Work registered run {registered.run_id}. "
                "Choose the order, maintain the plan, delegate helpers "
                "and check completion."
            )
        ]
    }
    while True:
        result = agent.invoke(
            state,
            config={
                "configurable": {"thread_id": registered.run_id},
                "recursion_limit": calls * 10,
            },
        )
        if tools.execution.stopping.is_set():
            raise QueueStopped
        check = tools.check()
        if check.complete:
            return
        state = {
            "messages": [
                *result["messages"],
                HumanMessage(
                    content="The backend found unfinished work. "
                    "Continue using the tools: " + check.model_dump_json()
                ),
            ]
        }


def run_queue(
    scope: Scope | dict[str, object],
    runner: Runner = claim_runner,
    llm: BaseChatModel | None = None,
    execution: Execution | None = None,
) -> Run:
    """Register the scope, work it and persist the factual final result."""
    selected = TypeAdapter(Scope).validate_python(scope)
    context = execution or Execution()
    with backend() as client:
        context.run_id = str(uuid4())
        response = request(
            client,
            "POST",
            "/api/agent-runs",
            {"run_id": context.run_id, "scope": selected.model_dump(mode="json")},
        )
        if response.is_error:
            detail = response.json().get("detail")
            raise AgentRunError(
                detail
                if isinstance(detail, str)
                else "The backend rejected the run scope."
            )
        registered = Run.model_validate(response.json())
        context.run_id = registered.run_id
        tools = QueueTools(client, registered.run_id, runner, context)
        status, detail = "completed", ""
        try:
            if registered.claim_limit:
                work_queue(tools, registered, llm)
        except QueueStopped:
            status, detail = "interrupted", "Queue processing was interrupted."
        except KeyboardInterrupt:
            status, detail = "interrupted", "Queue processing was interrupted."
            raise
        except AgentRunError as failure:
            status, detail = "failed", str(failure)
            raise
        except Exception as failure:
            status = "failed"
            reference = diagnose("Work queue", failure)
            detail = f"Queue agent failed ({type(failure).__name__}). {reference}."
            raise AgentRunError(detail) from failure
        finally:
            if context.result is None:
                with preserve_primary("Finalize queue run"):
                    context.result = tools.finish_run(status, detail)
    if context.result is None:
        raise AgentRunError("The queue's final result could not be saved.")
    return context.result
