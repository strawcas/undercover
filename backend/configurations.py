"""Immutable configuration and recipe records with backend-generated diffs."""

import difflib

from fastapi import HTTPException
from sqlalchemy import Connection, Table, insert, select, update

from backend.clock import now
from backend.configuration_models import (
    ClaimConfiguration,
    Configuration,
    ConfigurationHistory,
    ConfigurationProposal,
    Recipe,
    RecipeHistory,
)
from backend.database import configurations, recipes, settings
from backend.documents import contents
from backend.models import Handler
from backend.settings import allowed_models


def record(db: Connection, table: Table, version: int) -> dict:
    """Load one version without scores or evaluation data."""
    value = db.execute(
        select(table.c.record).where(table.c.version == version)
    ).scalar_one_or_none()
    if value is None:
        raise HTTPException(404, "Version not found")
    return dict(value) | {"version": version}


def current_version(db: Connection) -> int:
    """Return the configuration used by new standalone work."""
    return db.execute(select(settings.c.current_configuration)).scalar_one()


def get_configuration(db: Connection, version: int) -> ClaimConfiguration:
    """Return one claim configuration and prompt with no result access."""
    value = record(db, configurations, version)
    return ClaimConfiguration(
        **value, prompt=f"{value['instructions']}\n{contents(db)}"
    )


def text_diff(old: str, new: str) -> str:
    """Build the same line-based unified diff for either versioned text."""
    return "\n".join(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile="Before",
            tofile="After",
            lineterm="",
        )
    )


def history(db: Connection) -> list[ConfigurationHistory]:
    """Read configuration provenance and changes newest first."""
    rows = list(
        db.execute(select(configurations).order_by(configurations.c.version)).mappings()
    )
    values = {
        row["version"]: Configuration(**row["record"], version=row["version"])
        for row in rows
    }
    documents = contents(db)
    return [
        ConfigurationHistory(
            **value.model_dump(),
            prompt=f"{value.instructions}\n{documents}",
            diff=text_diff(
                values[value.parent_version].instructions
                if value.parent_version is not None
                else "",
                value.instructions,
            ),
            held_out_scores=None,
        )
        for value in reversed(list(values.values()))
    ]


def get_recipe(db: Connection, version: int) -> Recipe:
    """Read one complete improvement recipe."""
    return Recipe(**record(db, recipes, version))


def recipe_history(db: Connection) -> list[RecipeHistory]:
    """Read all recipe versions and their diffs."""
    versions = db.execute(
        select(recipes.c.version).order_by(recipes.c.version)
    ).scalars()
    values = {version: get_recipe(db, version) for version in versions}
    return [
        RecipeHistory(
            **value.model_dump(),
            diff=text_diff(
                values[value.parent_version].recipe
                if value.parent_version is not None
                else "",
                value.recipe,
            ),
        )
        for value in reversed(list(values.values()))
    ]


def validate_model(db: Connection, proposal: ConfigurationProposal) -> None:
    """Refuse a model or effort outside the file-owned catalogue."""
    model = allowed_models(db).get(proposal.model)
    if model is None or proposal.reasoning_effort not in model.reasoning_efforts:
        raise HTTPException(
            422, "Choose a model and reasoning effort from the allowed models"
        )


def append_configuration(
    db: Connection, proposal: ConfigurationProposal, handler: Handler, proposed_by: str
) -> Configuration:
    """Save an approved candidate without activating it."""
    validate_model(db, proposal)
    parent = current_version(db)
    values = proposal.model_dump() | {
        "parent_version": parent,
        "proposed_by": proposed_by,
        "approved_by": handler.name,
        "approved_at": now(),
        "created_at": now(),
    }
    version = db.execute(
        insert(configurations).values(record=values).returning(configurations.c.version)
    ).scalar_one()
    return Configuration(**values, version=version)


def activate_configuration(db: Connection, version: int) -> None:
    """Set the current pointer after the backend's keep verdict."""
    get_configuration(db, version)
    db.execute(update(settings).values(current_configuration=version))
