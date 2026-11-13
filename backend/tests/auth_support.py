"""Local asymmetric tokens shared by offline backend tests."""

import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "http://test-auth/auth/v1"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_JWK = RSAAlgorithm.to_jwk(PRIVATE_KEY.public_key(), as_dict=True) | {
    "kid": "test"
}
EMAILS = {
    "HND-01": "dana.whitfield@undercover.example",
    "HND-02": "marcus.oyelaran@undercover.example",
    "HND-03": "priya.raman@undercover.example",
}


def token(handler_id="HND-01", **claims):
    """Sign a short-lived test token without contacting Supabase."""
    payload = {
        "sub": handler_id,
        "email": EMAILS.get(handler_id, "unknown@example.com"),
        "iss": ISSUER,
        "aud": "authenticated",
        "exp": int(time.time()) + 600,
    } | claims
    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256", headers={"kid": "test"})


def authorization(handler_id="HND-01", **claims):
    """Return a bearer header for a handler action."""
    return {"Authorization": f"Bearer {token(handler_id, **claims)}"}
