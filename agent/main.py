"""The UnderCover Agent."""

import os
from http import HTTPStatus
from typing import Any

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from ollama import ResponseError
from pydantic import SecretStr

from agent.execution import AgentRunError, AttemptBudget
from agent.http import request
from agent.middleware import ProviderRetry
from agent.tools import ClaimTools
from backend.reference_models import InstructionsResponse

load_dotenv()

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
) -> str:
    """Run the agent on one claim and return its final reply."""
    limits = budget or AttemptBudget()
    with backend() as client:
        response = request(client, "GET", "/api/agent-instructions", budget=limits)
        response.raise_for_status()
        instructions = InstructionsResponse.model_validate(response.json())
        tools = ClaimTools(client, claim_id, thread_id, limits)
        tools.event(
            "Read agent instructions", "completed", "Loaded backend instructions."
        )
        middleware: list[AgentMiddleware[Any, Any, Any]] = [
            ProviderRetry(tools.model_retry, limits),
            ModelCallLimitMiddleware(
                run_limit=limits.model_calls, exit_behavior="error"
            ),
        ]
        agent = create_agent(
            llm or model(),
            tools=tools.tools(),
            system_prompt=instructions.text,
            middleware=middleware,
        )
        try:
            result = agent.invoke(
                {
                    "messages": [
                        {"role": "user", "content": f"Decide claim {claim_id}."}
                    ]
                },
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": 400,
                },
            )
        except ResponseError as error:
            raise AgentRunError(ollama_failure(error.status_code)) from error
    reply = result["messages"][-1]
    if reply.response_metadata.get("done_reason") == "length":
        raise AgentRunError(
            "Ollama stopped at its context or generation limit. Increase "
            "OLLAMA_NUM_CTX in .env. " + retry_advice()
        )
    return reply.text


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
