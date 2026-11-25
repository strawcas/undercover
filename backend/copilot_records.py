"""Own conversation access, turn transitions and the durable progress log."""

from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, RowMapping, insert, select, update

from backend.clock import now
from backend.copilot_models import (
    ActionReceipt,
    Conversation,
    ConversationDetail,
    Outcome,
    Step,
    StepUpdate,
    Turn,
    TurnRef,
    Work,
)
from backend.database import (
    conflict_insert,
    copilot_actions,
    copilot_conversations,
    copilot_steps,
    copilot_turns,
)

WORK_LIMIT = timedelta(minutes=20)
STOPPED = "The Copilot stopped after 20 minutes. You can send another message."
TURN_CONFLICT = "This Copilot turn is no longer accepting this request"
ACTIVE = ("working", "waiting")


def list_for(db: Connection, owner: str) -> list[Conversation]:
    """Return the handler's conversations in recent activity order."""
    rows = db.execute(
        select(copilot_conversations)
        .where(copilot_conversations.c.handler_id == owner)
        .order_by(
            copilot_conversations.c.updated_at.desc(),
            copilot_conversations.c.conversation_id,
        )
    ).mappings()
    return [Conversation.model_validate(row) for row in rows]


def create(db: Connection, owner: str) -> Conversation:
    """Create an empty conversation owned by the verified handler."""
    value = Conversation(
        conversation_id=str(uuid4()), handler_id=owner, title=None, updated_at=now()
    )
    db.execute(insert(copilot_conversations).values(**value.model_dump()))
    return value


def owned(
    db: Connection, owner: str, conversation_id: str, *, lock: bool = False
) -> RowMapping:
    """Read an owned conversation and serialize its mutations when requested."""
    table = copilot_conversations
    statement = (
        update(table).values(updated_at=table.c.updated_at).returning(*table.c)
        if lock
        else select(table)
    )
    row = (
        db.execute(
            statement.where(
                table.c.conversation_id == conversation_id, table.c.handler_id == owner
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Conversation not found")
    return row


def expire(db: Connection, turn_id: str | None, at: str) -> None:
    """Stop elapsed working stretches while leaving confirmations pending."""
    table = copilot_turns
    db.execute(
        update(table)
        .where(
            table.c.turn_id == turn_id,
            table.c.status == "working",
            table.c.deadline <= at,
        )
        .values(status="stopped", failure=STOPPED)
    )


def detail(db: Connection, owner: str, conversation_id: str) -> ConversationDetail:
    """Load the handler's messages, answers, cards and pending confirmation."""
    conversation = owned(db, owner, conversation_id)
    at = now()
    table = copilot_turns
    if db.scalar(
        select(table.c.turn_id).where(
            table.c.turn_id == conversation["current_turn"],
            table.c.status == "working",
            table.c.deadline <= at,
        )
    ):
        locked = owned(db, owner, conversation_id, lock=True)
        expire(db, locked["current_turn"], now())
    turns = list(
        db.execute(
            select(table)
            .where(table.c.conversation_id == conversation_id)
            .order_by(table.c.created_at, table.c.turn_id)
        ).mappings()
    )
    steps = copilot_steps
    by_turn: dict[str, list[Step]] = {}
    for row in db.execute(
        select(steps)
        .join(table)
        .where(table.c.conversation_id == conversation_id)
        .order_by(steps.c.started_at, steps.c.step_id)
    ).mappings():
        by_turn.setdefault(row["turn_id"], []).append(
            Step.model_validate(row["record"])
        )
    return ConversationDetail(
        **conversation,
        turns=[Turn(**row, steps=by_turn.get(row["turn_id"], [])) for row in turns],
    )


def new_stretch(at: str) -> dict:
    """Set the start, lease and identity of an unclaimed working stretch."""
    return {
        "started_at": at,
        "deadline": (datetime.fromisoformat(at) + WORK_LIMIT).isoformat(),
        "stretch_id": str(uuid4()),
        "status": "working",
        "claimed": False,
    }


def admit(
    db: Connection, owner: str, conversation_id: str, message: str
) -> tuple[str, str]:
    """Admit a message only when the previous turn can no longer advance."""
    conversation = owned(db, owner, conversation_id, lock=True)
    at = now()
    table = copilot_turns
    current = conversation["current_turn"]
    expire(db, current, at)
    if db.scalar(select(table.c.status).where(table.c.turn_id == current)) in ACTIVE:
        raise HTTPException(409, "Finish the current Copilot turn first")
    turn_id = str(uuid4())
    stretch = new_stretch(at)
    db.execute(
        insert(table).values(
            **stretch,
            turn_id=turn_id,
            conversation_id=conversation_id,
            message=message,
            created_at=at,
        )
    )
    title = conversation["title"] or " ".join(message.split())[:100]
    db.execute(
        update(copilot_conversations)
        .where(copilot_conversations.c.conversation_id == conversation_id)
        .values(title=title, updated_at=at, current_turn=turn_id)
    )
    return turn_id, stretch["stretch_id"]


def answer_confirmation(
    db: Connection, owner: str, conversation_id: str, turn_id: str, confirmed: bool
) -> str:
    """Accept one answer and give the resumed work a new working lease."""
    conversation = owned(db, owner, conversation_id, lock=True)
    table = copilot_turns
    stretch = new_stretch(now())
    if (
        conversation["current_turn"] != turn_id
        or db.execute(
            update(table)
            .where(table.c.turn_id == turn_id, table.c.status == "waiting")
            .values(**stretch, confirmed=confirmed)
        ).rowcount
        != 1
    ):
        raise HTTPException(409, TURN_CONFLICT)
    db.execute(
        update(copilot_conversations)
        .where(copilot_conversations.c.conversation_id == conversation_id)
        .values(updated_at=stretch["started_at"])
    )
    return stretch["stretch_id"]


def working(
    db: Connection,
    owner: str,
    ref: TurnRef,
    stretch_id: str,
    *,
    claimed: bool,
) -> RowMapping:
    """Validate the worker's lease under the same lock used by admission."""
    conversation = owned(db, owner, ref.conversation_id, lock=True)
    table = copilot_turns
    row = (
        db.execute(
            select(table).where(
                table.c.turn_id == ref.turn_id,
                table.c.status == "working",
                table.c.stretch_id == stretch_id,
                table.c.claimed.is_(claimed),
                table.c.deadline > now(),
            )
        )
        .mappings()
        .one_or_none()
    )
    if conversation["current_turn"] != ref.turn_id or row is None:
        raise HTTPException(409, TURN_CONFLICT)
    return row


def claim(
    db: Connection, owner: str, conversation_id: str, turn_id: str, stretch_id: str
) -> Work:
    """Give one worker the message and its most recent completed history."""
    row = working(
        db, owner, TurnRef(conversation_id, turn_id), stretch_id, claimed=False
    )
    table = copilot_turns
    db.execute(update(table).where(table.c.turn_id == turn_id).values(claimed=True))
    prior = db.scalar(
        select(table.c.turn_id)
        .where(table.c.conversation_id == conversation_id, table.c.status == "done")
        .order_by(table.c.created_at.desc(), table.c.turn_id.desc())
        .limit(1)
    )
    return Work(**row, prior_turn_id=prior)


def take_action(db: Connection, owner: str, receipt: ActionReceipt) -> RowMapping:
    """Require an active Copilot lease inside the action's own transaction."""
    return working(db, owner, receipt.ref, receipt.stretch_id, claimed=True)


def require_confirmation(turn: RowMapping, claim: RowMapping) -> None:
    """Match the payout being settled to the saved and confirmed decision."""
    card = turn["confirmation"]
    if (
        not turn["confirmed"]
        or not card
        or (card["claim_id"], card["revision"])
        != (claim["claim_id"], claim["revision"] or 0)
    ):
        raise HTTPException(409, "This payout has no active Copilot confirmation")


def saved_action(db: Connection, receipt: ActionReceipt) -> str | None:
    """Find the run or original claim already committed for this handler action."""
    return db.scalar(
        select(copilot_actions.c.reference).where(
            copilot_actions.c.turn_id == receipt.ref.turn_id,
            copilot_actions.c.action_id == receipt.action_id,
        )
    )


def save_action(db: Connection, receipt: ActionReceipt, reference: str) -> None:
    """Bind the committed change to this action before contacting the service."""
    db.execute(
        insert(copilot_actions).values(
            turn_id=receipt.ref.turn_id,
            action_id=receipt.action_id,
            reference=reference,
        )
    )


def record_step(
    db: Connection,
    owner: str,
    ref: TurnRef,
    step_id: str,
    step: StepUpdate,
) -> None:
    """Add a progress card or complete its existing working entry."""
    working(db, owner, ref, str(step.stretch_id), claimed=True)
    table = copilot_steps
    query = table.c.turn_id == ref.turn_id, table.c.step_id == step_id
    prior = db.execute(select(table).where(*query)).mappings().one_or_none()
    if prior and (
        prior["record"]["status"] != "working"
        or prior["record"]["stretch_id"] != str(step.stretch_id)
    ):
        raise HTTPException(409, "This Copilot step is already complete")
    value = Step(
        **step.model_dump(),
        step_id=step_id,
        started_at=prior["started_at"] if prior else now(),
    )
    statement = conflict_insert(db, table).values(
        turn_id=ref.turn_id,
        step_id=step_id,
        started_at=value.started_at,
        record=value.model_dump(mode="json") | {"stretch_id": str(step.stretch_id)},
    )
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[table.c.turn_id, table.c.step_id],
            set_={"record": statement.excluded.record},
        )
    )


def finish(
    db: Connection, owner: str, conversation_id: str, turn_id: str, outcome: Outcome
) -> None:
    """Close the active stretch once or publish its durable approval card."""
    working(
        db,
        owner,
        TurnRef(conversation_id, turn_id),
        str(outcome.stretch_id),
        claimed=True,
    )
    if (outcome.status == "waiting") != (outcome.confirmation is not None):
        raise HTTPException(422, "A waiting turn requires a confirmation")
    values = outcome.model_dump(mode="json", exclude={"stretch_id"})
    db.execute(
        update(copilot_turns).where(copilot_turns.c.turn_id == turn_id).values(**values)
    )


def launch_failed(
    db: Connection,
    owner: str,
    ref: TurnRef,
    stretch_id: str,
    reason: str,
) -> None:
    """Undo a definite launch rejection only while no worker has taken it."""
    conversation = owned(db, owner, ref.conversation_id, lock=True)
    if conversation["current_turn"] != ref.turn_id:
        return
    table = copilot_turns
    query = (
        table.c.turn_id == ref.turn_id,
        table.c.stretch_id == stretch_id,
        table.c.claimed.is_(False),
        table.c.status == "working",
    )
    row = db.execute(select(table.c.confirmation).where(*query)).first()
    if row is not None:
        status = "waiting" if row.confirmation is not None else "failed"
        db.execute(
            update(table)
            .where(*query)
            .values(
                status=status,
                failure=reason if status == "failed" else None,
                confirmed=None,
            )
        )
