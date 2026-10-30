"""Bounded, observational notification after a committed handler sign-off."""

import httpx

from backend.config import agent_url
from backend.diagnostics import diagnose


def notify_sign_off(claim_id: str) -> None:
    """Wake the agent without making delivery a condition of saved sign-off."""
    try:
        response = httpx.post(f"{agent_url()}/claims/{claim_id}/resume", timeout=1)
        response.raise_for_status()
    except Exception as error:
        diagnose("Notify saved sign-off", error)
