"""The UnderCover Agent."""

import os
from contextlib import nullcontext
from http import HTTPStatus

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from ollama import ResponseError
from pydantic import SecretStr

from agent.checkpoints import checkpointer
from agent.execution import AgentRunError, AttemptBudget
from agent.graph import ClaimState, ClaimWorkflow, build_graph
from agent.http import RequestOptions, request
from agent.lease import ClaimLease
from agent.tools import ClaimTools, DecisionFields
from agent.tracing import claim_trace
from backend.claim_models import ClaimDetail
from backend.reference_models import InstructionsResponse

BACKEND_URL = "http://localhost:8000"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_OLLAMA_NUM_CTX = 16384


def ollama_context_length() -> int:
    """Read the context budget used for policy evidence and tool history."""
    try:
        length = int(os.getenv("OLLAMA_NUM_CTX", str(DEFAULT_OLLAMA_NUM_CTX)))
        if length <= 0:
            raise ValueError
    except ValueError as error:
        raise ValueError("OLLAMA_NUM_CTX must be a positive integer.") from error
    return length


def backend() -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(
        base_url=os.getenv("UNDERCOVER_BACKEND_URL", BACKEND_URL), timeout=30
    )


def model_provider() -> str:
    """Read and validate the selected model provider."""
    provider = os.getenv("MODEL_PROVIDER", "openrouter").strip().lower()
    if provider not in {"openrouter", "ollama"}:
        raise ValueError("MODEL_PROVIDER must be openrouter or ollama.")
    return provider


def validate_model_config() -> None:
    """Check required settings without contacting either model provider."""
    if model_provider() == "ollama":
        if not os.getenv("MODEL_NAME", "").strip():
            raise ValueError("Set MODEL_NAME in .env to a downloaded Ollama model.")
        ollama_context_length()
    elif not os.getenv("OPENROUTER_API_KEY"):
        raise ValueError("Set OPENROUTER_API_KEY in .env before processing claims.")


def model() -> BaseChatModel:
    """Return the selected OpenRouter or local Ollama chat model."""
    validate_model_config()
    if model_provider() == "ollama":
        return ChatOllama(
            model=os.environ["MODEL_NAME"].strip(),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0,
            num_ctx=ollama_context_length(),
            client_kwargs={"timeout": 120},
        )
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort="low" if (name or DEFAULT_MODEL) == DEFAULT_MODEL else None,
        max_retries=0,
        timeout=120,
    )


def run(
    claim_id: str,
    thread_id: str,
    llm: BaseChatModel | None = None,
    *,
    budget: AttemptBudget | None = None,
    saver: BaseCheckpointSaver | None = None,
) -> str:
    """Run the agent on one claim and return its final reply."""
    limits = budget or AttemptBudget()
    limits.check()
    selected = llm or model
    with (
        backend() as client,
        ClaimLease(client, claim_id, thread_id, limits) as lease,
        claim_trace(
            claim_id,
            thread_id,
            lease.entry.run_id if lease.entry else "",
            lease.entry.attempt_number if lease.entry else 0,
        ) as callbacks,
    ):
        if lease.entry and lease.entry.status in {"paused", "completed"}:
            return (
                "Assessment complete; waiting for handler sign-off."
                if lease.entry.status == "paused"
                else "The claim assessment is complete."
            )
        response = request(
            client,
            "GET",
            "/api/agent-instructions",
            options=RequestOptions(budget=limits),
        )
        response.raise_for_status()
        instructions = InstructionsResponse.model_validate(response.json())
        tools = ClaimTools(client, claim_id, thread_id, limits)
        tools.event(
            "Read agent instructions", "completed", "Loaded backend instructions."
        )
        with nullcontext(saver) if saver is not None else checkpointer() as checkpoints:
            graph = build_graph(ClaimWorkflow(tools, selected), checkpoints)
            config: RunnableConfig = {
                "configurable": {"thread_id": thread_id},
                "callbacks": callbacks,
                "recursion_limit": 400,
            }
            snapshot = graph.get_state(config)
            initial: ClaimState | Command | None
            if snapshot.values:
                claim = tools.read_claim()
                if snapshot.interrupts and claim.get("sign_off"):
                    initial = Command(resume={"claim_id": claim_id})
                elif snapshot.interrupts:
                    return "Assessment complete; waiting for handler sign-off."
                else:
                    initial = None
            else:
                existing = ClaimDetail.model_validate(tools.read_claim())
                decision = (
                    DecisionFields.model_validate(
                        {
                            key: getattr(existing.decision, key)
                            for key in DecisionFields.model_fields
                        }
                    )
                    if existing.decision
                    else None
                )
                initial = ClaimState(
                    messages=[HumanMessage(content=f"Decide claim {claim_id}.")],
                    claim_id=claim_id,
                    thread_id=thread_id,
                    run_id=lease.entry.run_id if lease.entry else "",
                    attempt_number=lease.entry.attempt_number if lease.entry else 0,
                    instructions=instructions.text,
                    decision=decision.model_dump(mode="json") if decision else None,
                    saved_claim=existing.model_dump(mode="json") if decision else None,
                    model_calls=0,
                )
            try:
                result = graph.invoke(initial, config=config, durability="sync")
                while result.get("__interrupt__"):
                    stored = request(client, "GET", f"/api/claims/{claim_id}")
                    stored.raise_for_status()
                    if not stored.json()["sign_off"]:
                        lease.mode = "pause"
                        break
                    result = graph.invoke(
                        Command(resume={"claim_id": claim_id}),
                        config=config,
                        durability="sync",
                    )
            except ResponseError as error:
                raise AgentRunError(ollama_failure(error.status_code)) from error
    return (
        "Assessment complete; waiting for handler sign-off."
        if result.get("__interrupt__")
        else "The claim assessment is complete."
    )


def retry_advice() -> str:
    return (
        "After fixing the configuration, run uv run -m agent to retry eligible "
        "Waiting claims. Two failed assessments require handler review; this "
        "command cannot retry Handler review claims."
    )


def ollama_failure(status: int) -> str:
    """Explain known Ollama failures without exposing provider response bodies."""
    if status == HTTPStatus.NOT_FOUND:
        return (
            "Ollama could not find the configured model or API endpoint. Check "
            "MODEL_NAME against ollama list and OLLAMA_BASE_URL, pull the exact "
            "model tag if needed. " + retry_advice()
        )
    if status == HTTPStatus.BAD_REQUEST:
        return (
            "Ollama rejected the request. Check that the selected model supports "
            "tool calling and review the Ollama server log. " + retry_advice()
        )
    return (
        f"Ollama request failed (HTTP {status}). Check the Ollama server log "
        "and available memory. " + retry_advice()
    )
