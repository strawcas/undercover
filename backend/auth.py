"""Verify Supabase sign-ins and resolve UnderCover handler permissions."""

import os
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from backend.database import handlers
from backend.dependencies import Database
from backend.models import Handler

SUPABASE_URL = os.getenv("VITE_SUPABASE_URL", "http://127.0.0.1:8005")
keys = jwt.PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")
bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def current_handler(credentials: Credentials, db: Database) -> Handler:
    """Resolve a verified access token to a seeded handler."""
    if credentials is None:
        raise HTTPException(401, "Sign in to continue")
    try:
        key = keys.get_signing_key_from_jwt(credentials.credentials)
        payload = jwt.decode(
            credentials.credentials,
            key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            issuer=f"{SUPABASE_URL}/auth/v1",
            options={"require": ["exp", "sub", "email"]},
        )
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as error:
        raise HTTPException(
            401, "Your sign-in expired or is invalid. Sign in again"
        ) from error
    row = (
        db.execute(select(handlers).where(handlers.c.email == payload["email"]))
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(403, "This account is not an UnderCover handler")
    return Handler(**row)


CurrentHandler = Annotated[Handler, Depends(current_handler)]


def senior_handler(handler: CurrentHandler) -> Handler:
    """Require authority to sign off payouts over the limit."""
    if not handler.can_approve_over_limit:
        raise HTTPException(
            403,
            f"{handler.name} cannot perform this action. A senior handler is required",
        )
    return handler


SeniorHandler = Annotated[Handler, Depends(senior_handler)]
