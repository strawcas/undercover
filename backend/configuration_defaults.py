"""Initial model configuration shared by seeding and platform settings."""

import os

DEFAULT_MODEL = "openai/gpt-6-luna"


def initial_model() -> dict[str, str]:
    """Resolve the explicit free-model override before creating version one."""
    override = os.getenv("MODEL_NAME")
    return {
        "model": override or DEFAULT_MODEL,
        "reasoning_effort": "default" if override else "low",
    }
