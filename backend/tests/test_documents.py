from sqlalchemy import select

from backend.database import documents, engine, settings
from context.catalog import PLANS_FILE, read_yaml


def test_instructions_for_a_claim(client):
    instructions = client.get("/api/configurations/current").json()
    assert "held_out_scores" not in instructions
    assert instructions["version"] == 1
    prompt = instructions["prompt"]
    assert prompt.startswith("# UnderCover Agent: working a claim")
    contents = prompt[prompt.index("## Contents of the policy wording") :]
    assert "\n3. Trip Delay\n" in contents
    assert "## Contents of the claims manual" in contents
    assert contents.endswith("14. Records\n")
    assert "Unattended means out of your sight" not in prompt
    assert "Monkeys, gulls, bears, giraffes" not in prompt


def test_both_documents_and_payout_plans_are_seeded_from_context():
    with engine().connect() as db:
        query = select(documents).order_by(documents.c.position)
        rows = db.execute(query).mappings().all()
        plans, limit = db.execute(
            select(settings.c.plans, settings.c.payout_approval_limit)
        ).one()
    assert [(row["name"], row["version"]) for row in rows] == [
        ("policy_wording", "2026.1"),
        ("claims_manual", "2026.2"),
    ]
    assert rows[1]["text"].startswith("# UnderCover Claims Handling Manual")
    assert "\n14. Records" in rows[1]["contents"]
    assert plans == read_yaml(PLANS_FILE)[1]
    assert limit == 1000
