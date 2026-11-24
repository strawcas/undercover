from typing import get_args

from agent import context_client, models
from agent.credentials import app_headers
from backend import agent_key
from backend.configuration_defaults import initial_model
from context import catalog, endpoint


def test_independent_packages_agree_on_the_wire_contract():
    assert app_headers() == agent_key.request_headers()
    assert context_client.DEFAULT_URL == endpoint.DEFAULT_URL
    assert get_args(context_client.DocumentName) == get_args(catalog.DocumentName)
    assert get_args(context_client.PlanName) == get_args(catalog.PlanName)
    assert get_args(context_client.RecordName) == get_args(catalog.RecordName)


def test_staged_screening_preserves_the_model_default_and_override(monkeypatch):
    assert models.screening_model() == initial_model("none")
    monkeypatch.setenv("MODEL_NAME", "test/override")
    assert models.screening_model() == initial_model("none")
