"""HTTP contracts for targeted policy and manual reads."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.harness_models import RequiredInput
from backend.models import PaymentInput

Source = Literal["policy_wording", "claims_manual"]


class SearchInput(RequiredInput):
    thread_id: UUID
    source: Source
    query: str = Field(min_length=1, max_length=200)


class ReadInput(RequiredInput):
    thread_id: UUID
    source: Source
    section_id: str = Field(min_length=1)


class AgentPaymentInput(PaymentInput, RequiredInput):
    thread_id: UUID


class Section(BaseModel):
    section_id: str
    title: str
    parent_id: str | None


class Contents(BaseModel):
    source: Source
    version: str
    sections: list[Section]


class Match(BaseModel):
    section_id: str
    title: str
    snippet: str


class SearchResponse(BaseModel):
    claim_id: str
    thread_id: str
    source: Source
    version: str
    query: str
    matches: list[Match]


class Evidence(BaseModel):
    evidence_id: str
    run_id: str
    claim_id: str
    thread_id: str
    source: Source
    version: str
    section_id: str
    title: str
    text: str
    read_at: str


class EvidenceResponse(BaseModel):
    claim_id: str
    reads: list[Evidence]


class PaymentResponse(BaseModel):
    amount: str
    created_at: str
    simulated: Literal[True]


class InstructionsResponse(BaseModel):
    text: str
    queue_text: str
