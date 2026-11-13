"""Business-owned settings and their audit records."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Objectives(BaseModel):
    accuracy: float = Field(ge=0, le=1)
    grade: float = Field(ge=0, le=3)


class Measures(Objectives):
    cost_per_claim: Nonnegative
    seconds_per_claim: Nonnegative


class PlatformSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payout_approval_limit: Nonnegative
    objectives: Objectives
    noise: Measures
    tolerance: Measures
    spending_limit: Nonnegative
    concurrency: int = Field(ge=1, le=25)


class AllowedModel(BaseModel):
    reasoning_efforts: list[str]
    input_price: Nonnegative
    output_price: Nonnegative


class SettingChange(BaseModel):
    id: int
    handler: str
    changed_at: str
    before: PlatformSettings
    after: PlatformSettings


class SettingsView(BaseModel):
    values: PlatformSettings
    models_allowed: dict[str, AllowedModel]
    changes: list[SettingChange]
