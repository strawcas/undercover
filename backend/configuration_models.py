"""Versioned claim configurations and improvement recipes."""

from pydantic import BaseModel, ConfigDict, Field

from backend.models import Scores


class ConfigurationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instructions: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RecipeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class VersionMetadata(BaseModel):
    version: int
    parent_version: int | None
    proposed_by: str
    approved_by: str | None
    approved_at: str | None
    created_at: str
    reason: str


class Configuration(VersionMetadata, ConfigurationProposal):
    pass


class ClaimConfiguration(Configuration):
    prompt: str


class ConfigurationHistory(Configuration):
    diff: str
    prompt: str
    held_out_scores: Scores | None


class Recipe(VersionMetadata):
    recipe: str = Field(min_length=1)


class RecipeHistory(Recipe):
    diff: str
