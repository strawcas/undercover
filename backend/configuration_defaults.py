"""Initial model configuration shared by seeding and platform settings."""

import os

DEFAULT_MODEL = "openai/gpt-6-luna"


def initial_model(effort: str = "low") -> dict[str, str]:
    """Resolve the model override or use Luna with the caller's default effort."""
    override = os.getenv("MODEL_NAME")
    return {
        "model": override or DEFAULT_MODEL,
        "reasoning_effort": "default" if override else effort,
    }
