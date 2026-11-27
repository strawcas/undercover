from fastapi import FastAPI
from pydantic import BaseModel

from api_rules import rule_breaks


def test_every_operation_follows_the_api_rules(client):
    assert rule_breaks(client.get("/openapi.json").json()) == []


class Inner(BaseModel):
    name: str
    size: int = 1


class Outer(BaseModel):
    inners: list[Inner]


def test_an_optional_field_inside_a_body_breaks_the_rules():
    app = FastAPI()

    @app.post("/things", operation_id="add_things")
    def add_things(request: Outer) -> None:
        """Add some things."""

    assert rule_breaks(app.openapi()) == ["POST /things: Inner.size is optional"]
