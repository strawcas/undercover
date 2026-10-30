"""An isolated notification receiver for browser tests on port 8003."""

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from backend.claim_models import ClaimDetail, Health, Recorded, SignOff


class Notification(BaseModel):
    claim_id: str
    sign_off: SignOff


app = FastAPI(title="UnderCover test notification receiver")
notifications: list[Notification] = []


@app.get("/health", operation_id="standin_health", response_model=Health)
def health():
    """Check the isolated browser-test receiver."""
    return {"status": "ok"}


@app.post(
    "/claims/{claim_id}/resume",
    operation_id="standin_resume",
    response_model=Recorded,
    status_code=202,
)
def resume(claim_id: str):
    """Record a notification only after its backend sign-off is saved."""
    response = httpx.get(f"http://127.0.0.1:8011/api/claims/{claim_id}", timeout=2)
    response.raise_for_status()
    claim = ClaimDetail.model_validate(response.json())
    if claim.sign_off is None or claim.status != "in_progress":
        raise HTTPException(409, "Sign-off must be saved before notification.")
    notifications.append(Notification(claim_id=claim_id, sign_off=claim.sign_off))
    return {"status": "recorded"}


@app.get(
    "/notifications",
    operation_id="standin_notifications",
    response_model=list[Notification],
)
def recorded():
    """Read the saved notifications for browser assertions."""
    return notifications


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8003, log_level="warning")
