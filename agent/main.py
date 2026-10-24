"""The UnderCover Agent."""

import os

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent.tools import ClaimTools

load_dotenv()

BACKEND_URL = "http://localhost:8000"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"


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
        )
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
    )


def run(claim_id: str, thread_id: str, llm: BaseChatModel | None = None) -> str:
    """Run the agent on one claim and return its final reply."""
    with backend() as client:
        response = client.get("/api/agent-instructions")
        response.raise_for_status()
        tools = ClaimTools(client, claim_id, thread_id)
        tools.event(
            "Read agent instructions", "completed", "Loaded backend instructions."
        )
        agent = create_agent(
            llm or model(), tools=tools.tools(), system_prompt=response.json()["text"]
        )
        result = agent.invoke(
            {"messages": [{"role": "user", "content": f"Decide claim {claim_id}."}]},
            config={"configurable": {"thread_id": thread_id}, "recursion_limit": 40},
        )
    return result["messages"][-1].text
