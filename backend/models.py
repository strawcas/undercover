"""The shapes of the backend's requests and responses."""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

Status = Literal[
    "waiting", "in_progress", "waiting_for_sign_off", "decided", "left_for_handler"
]
DecisionKind = Literal["approve", "decline", "refer"]
StepKind = Literal["tool_call", "tool_result", "message", "reading", "error"]
TodoStatus = Literal["to_do", "in_progress", "done", "left_for_handler"]
DocumentName = Literal["policy_wording", "claims_manual"]
Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]
LineKind = Literal[
    "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
]


class Item(BaseModel):
    description: str
    category: str
    amount: float


class Document(BaseModel):
    name: str
    type: str
    content: str


class Decision(BaseModel):
    decision: DecisionKind
    payout: float
    section: str
    reason: str
    decided_at: str
    signed_off_by: str | None
    signed_off_at: str | None


class Payment(BaseModel):
    amount: float
    paid_at: str
    signed_off_by: str | None
    signed_off_at: str | None


class ClaimSummary(BaseModel):
    claim_id: str
    customer_name: str
    claim_type: str
    summary: str
    amount_claimed: float
    submitted_on: str
    status: Status
    decision: DecisionKind | None
    payout: float | None


class Claim(BaseModel):
    claim_id: str
    policy_id: str
    customer_id: str
    customer_name: str
    claim_type: str
    submitted_on: str
    incident_date: str
    incident_location: str
    summary: str
    story: str
    items: list[Item]
    amount_claimed: float
    documents: list[Document]
    status: Status
    thread_id: str | None
    attempts: int
    decision: Decision | None
    payment: Payment | None


class Trip(BaseModel):
    destination: str
    departs_on: str
    returns_on: str
    trip_cost: float


class Policy(BaseModel):
    policy_id: str
    customer_id: str
    plan: str
    add_ons: list[str]
    purchased_on: str
    sold_through: str
    trip: Trip
    travelers: list[str]
    status: str


class PreviousClaim(BaseModel):
    claim_id: str
    filed_on: str
    incident_date: str
    claim_type: str
    summary: str
    outcome: str
    amount_paid: float


class Handler(BaseModel):
    handler_id: str
    name: str
    role: str
    can_approve_over_limit: bool


class Step(BaseModel):
    step_id: int
    thread_id: str
    kind: StepKind
    content: str
    created_at: str


class Instructions(BaseModel):
    queue: str
    claim: str


class Section(BaseModel):
    document: DocumentName
    document_title: str
    number: int
    title: str
    text: str


class SectionMatch(BaseModel):
    document: DocumentName
    number: int
    title: str
    excerpts: list[str]


class StartRequest(BaseModel):
    thread_id: str = Field(min_length=1)


class StepRequest(BaseModel):
    kind: StepKind
    content: str = Field(min_length=1)


class DecisionRequest(BaseModel):
    decision: DecisionKind
    payout: float = Field(
        ge=0, description="In US dollars, 0 for a decline or referral"
    )
    section: str = Field(
        min_length=1, description="The sections of the wording and the manual relied on"
    )
    reason: str = Field(
        min_length=1, description="Two or three sentences explaining the decision"
    )

    @model_validator(mode="after")
    def check_payout(self) -> Self:
        """An approval pays something, and a decline or referral pays nothing."""
        if self.decision == "approve" and self.payout == 0:
            raise ValueError("An approval needs a payout above zero")
        if self.decision != "approve" and self.payout != 0:
            raise ValueError("A decline or a referral has a payout of zero")
        return self


class PaymentRequest(BaseModel):
    amount: float = Field(gt=0, description="The approved payout in US dollars")


class SignOffRequest(BaseModel):
    handler_id: str


class Plan(BaseModel):
    plan: str
    name: str
    trip_cancellation_max: float
    trip_delay_waiting_hours: int
    trip_delay_max: float
    baggage_delay_waiting_hours: int
    baggage_delay_max: float
    baggage_max: float
    per_item_limit: float
    passport_max: float
    emergency_medical_max: float
    deductible: float


class AddOn(BaseModel):
    add_on: str
    name: str
    effect: str
    electronics_per_item_limit: float | None = None


class Plans(BaseModel):
    plans: list[Plan]
    deductible_applies_to: list[Benefit]
    add_ons: list[AddOn]


class Line(BaseModel):
    description: str = Field(min_length=1)
    amount: float = Field(ge=0, description="The total claimed, before limits")
    quantity: int = Field(ge=1, description="How many separate items")
    kind: LineKind
    nights: int = Field(ge=0, description="For a hotel, and 0 otherwise")
    hours: float = Field(
        ge=0, description="For meals without receipts, and 0 otherwise"
    )


class PayoutRequest(BaseModel):
    benefit: Benefit
    lines: list[Line] = Field(
        description="The covered lines, with any excluded left out"
    )
    paid_by_others: float = Field(ge=0, description="Paid or refunded by others")


class CalculationStep(BaseModel):
    step: str
    amount: float


class Payout(BaseModel):
    calculation: list[CalculationStep]
    payout: float
    needs_sign_off: bool


class FailureRequest(BaseModel):
    reason: str = Field(min_length=1, description="What went wrong")


class Todo(BaseModel):
    content: str = Field(min_length=1)
    status: TodoStatus


class Run(BaseModel):
    run_id: str
    cap: int
    status: Literal["running", "finished"]
    started_at: str
    finished_at: str | None
    summary: str | None
    todos: list[Todo]


class RunRequest(BaseModel):
    cap: int = Field(ge=1, description="The most claims the run can work")


class TodosRequest(BaseModel):
    todos: list[Todo]


class FinishRequest(BaseModel):
    summary: str = Field(min_length=1, description="The summary for the handlers")


class ModelCallRequest(BaseModel):
    reply_id: str = Field(min_length=1)
    run_id: str | None
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    cost: float | None = Field(ge=0)


class WorkSliceRequest(BaseModel):
    run_id: str | None
    seconds: float = Field(ge=0, allow_inf_nan=False)
