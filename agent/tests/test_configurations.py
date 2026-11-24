"""Claim model construction uses the run's immutable configuration."""

from langgraph.checkpoint.memory import InMemorySaver

from agent import api, checkpoints, harness, models
from agent.tests.stubs import record, stub
from backend import configurations
from backend.configuration_models import ConfigurationProposal
from backend.database import engine
from backend.models import Handler
from backend.tests.run_requests import configuration, fixture_run


def test_pinned_version_supplies_the_model_and_prompt_after_activation(
    client, monkeypatch
):
    initial = api.get_current_configuration()
    run = fixture_run(configuration())
    handler = Handler(**client.get("/api/me").json())
    with engine().begin() as db:
        candidate = configurations.append_configuration(
            db,
            ConfigurationProposal(
                instructions="A changed general goal.",
                model=initial["model"],
                reasoning_effort="medium",
                reason="Test immutable version",
            ),
            configurations.Approval(1, handler, "Improver"),
        )
        configurations.activate_configuration(db, candidate.version)
    selected = []
    llm = stub(record("refer", 0))

    def build(value):
        selected.append(value)
        return llm

    monkeypatch.setattr(models, "build_model", build)
    monkeypatch.setattr(checkpoints.state, "pool", object())
    monkeypatch.setattr(checkpoints, "PostgresSaver", lambda pool: InMemorySaver())
    harness.execute_claim("CLM-1001", run["run_id"], run["configuration_version"])
    assert selected[0]["version"] == 1
    assert selected[0]["reasoning_effort"] == "low"
    assert llm.prompts[0][0].content == initial["prompt"]
    assert api.get_current_configuration()["version"] == 2
