"""Generate the OpenAPI contract used by the claim agent's Gateway."""

from functools import cache
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

OPERATIONS = frozenset(
    {
        "get_claim",
        "get_policy",
        "get_claim_history",
        "calculate_payout",
        "record_decision",
        "pay_claim",
    }
)


@cache
def agent_contract(app: FastAPI) -> dict[str, Any]:
    """Build the contract from the claim routes and their referenced models."""
    schema = get_openapi(
        title="UnderCover Agent API",
        version=app.version,
        routes=[
            route
            for route in app.routes
            if isinstance(route, APIRoute) and route.operation_id in OPERATIONS
        ],
    )
    for methods in schema["paths"].values():
        for operation in methods.values():
            operation.pop("security", None)
    schema["components"].pop("securitySchemes", None)
    return schema
