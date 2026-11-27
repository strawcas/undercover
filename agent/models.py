"""Construct OpenRouter models from backend-owned configurations."""

import os
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_SCREEN_MODEL = "openai/gpt-6-luna"


def screening_model() -> dict[str, str]:
    """Use the course's screen or the installation's shared model override."""
    override = os.getenv("MODEL_NAME")
    return {
        "model": override or DEFAULT_SCREEN_MODEL,
        "reasoning_effort": "default" if override else "none",
    }


def build_model(
    configuration: dict[str, Any],
    *,
    parallel_tool_calls: bool | None = None,
    max_retries: int = 5,
) -> ChatOpenAI:
    """Build exactly the model and effort pinned to this work."""
    effort = configuration["reasoning_effort"]
    return ChatOpenAI(
        model=configuration["model"],
        reasoning_effort=None if effort == "default" else effort,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        timeout=120,
        max_retries=max_retries,
        model_kwargs={}
        if parallel_tool_calls is None
        else {"parallel_tool_calls": parallel_tool_calls},
    )
