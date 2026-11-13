"""Backend-owned round evidence, approvals and atomic activation verdicts."""

import uuid

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import Connection, insert, select, update

from backend import configurations, evaluations, improvement_budget, improvement_rules
from backend import settings as platform_settings
from backend.clock import now
from backend.configuration_defaults import DEFAULT_MODEL
from backend.configuration_models import ConfigurationProposal, RecipeProposal
from backend.database import (
    insert_once,
    round_model_calls,
    round_proposals,
    rounds,
    runs,
    settings,
)
from backend.improvement_models import (
    EvaluationQuote,
    HandlerDecision,
    Proposal,
    ProposalRequest,
    Quote,
    Round,
    RoundProgress,
    RoundUsage,
)
from backend.models import Handler, SetName

EVALUATION_COUNTS: dict[SetName, int] = {"evaluation": 1, "held_out": 2}


def row(db: Connection, round_id: str, writable: bool = False):
    """Lock writable round state and refuse every late state mutation."""
    value = improvement_budget.lock_round(db, round_id)
    if writable and value["status"] in ("finished", "stopped"):
        raise HTTPException(409, f"Round is {value['status']}")
    return value


def save(db: Connection, round_id: str, status: str, record: dict) -> None:
    """Write a previously locked round snapshot."""
    db.execute(
        update(rounds)
        .where(rounds.c.round_id == round_id)
        .values(status=status, record=record)
    )


def proposals(db: Connection, round_id: str) -> list[Proposal]:
    """Read approval history in its original proposal order."""
    values = db.execute(
        select(round_proposals.c.record).where(round_proposals.c.round_id == round_id)
    ).scalars()
    return sorted(
        (Proposal(**value) for value in values), key=lambda value: value.created_at
    )


def get_round(db: Connection, round_id: str) -> Round:
    """Return durable progress, aggregate scores and attributed spending."""
    value = (
        db.execute(select(rounds).where(rounds.c.round_id == round_id))
        .mappings()
        .first()
    )
    if value is None:
        raise HTTPException(404, "Round not found")
    return Round(
        **value["record"],
        round_id=round_id,
        status=value["status"],
        proposals=proposals(db, round_id),
        spending=improvement_budget.spending(
            db, round_id, value["record"]["settings"]["spending_limit"]
        ),
    )


def list_rounds(db: Connection) -> list[Round]:
    """Read round history newest first, without evaluation claim details."""
    ids = list(
        db.execute(select(rounds.c.round_id).order_by(rounds.c.number.desc())).scalars()
    )
    return [get_round(db, identity) for identity in ids]


def start(db: Connection, handler: Handler) -> Round:
    """Capture configuration, recipe, catalogue and settings for one new round."""
    if not handler.can_approve_over_limit:
        raise HTTPException(403, "A senior handler must start rounds")
    current = db.execute(select(settings).with_for_update()).mappings().one()
    if db.execute(
        select(rounds.c.round_id).where(rounds.c.status.not_in(("finished", "stopped")))
    ).first():
        raise HTTPException(409, "An improvement round is already open")
    base = configurations.get_configuration(db, current["current_configuration"])
    identity = str(uuid.uuid4())
    record = {
        "thread_id": str(uuid.uuid4()),
        "started_at": now(),
        "started_by": handler.name,
        "finished_at": None,
        "base_version": base.version,
        "base_model": base.model,
        "candidate_version": None,
        "recipe_version": current["current_recipe"],
        "settings": platform_settings.get_settings(db).model_dump(),
        "models_allowed": {
            name: model.model_dump()
            for name, model in platform_settings.allowed_models(db).items()
        },
        "improver_model": base.model
        if base.reasoning_effort == "default"
        else DEFAULT_MODEL,
        "improver_effort": "default"
        if base.reasoning_effort == "default"
        else "medium",
        "progress": 0,
        "before": None,
        "after": None,
        "verdict": None,
        "explanation": None,
    }
    db.execute(
        insert(rounds).values(round_id=identity, status="baseline", record=record)
    )
    return get_round(db, identity)


def validate_proposal(
    db: Connection, round_id: str, request: ProposalRequest
) -> str | None:
    """Return a model-facing refusal before any human approval is requested."""
    value = row(db, round_id, writable=True)
    try:
        if request.kind == "configuration":
            proposal = ConfigurationProposal(**request.arguments)
            model = value["record"]["models_allowed"].get(proposal.model)
            if (
                model is None
                or proposal.reasoning_effort not in model["reasoning_efforts"]
            ):
                return "Choose a model and effort from this round's allowed models"
            if value["record"]["candidate_version"] is not None:
                return "This round already has an approved candidate"
            if value["record"]["before"] is None:
                return "The baseline evaluations must finish first"
        else:
            RecipeProposal(**request.arguments)
            if (
                value["record"]["candidate_version"] is not None
                and value["record"]["verdict"] is None
            ):
                return "Finish the candidate's evaluations before proposing a recipe"
    except ValidationError:
        return "Provide every required proposal field with nonempty text"
    return None


def pending(db: Connection, round_id: str, request: ProposalRequest) -> Round:
    """Persist a checkpointed proposal as waiting for a handler."""
    refusal = validate_proposal(db, round_id, request)
    if refusal:
        raise HTTPException(422, refusal)
    value = row(db, round_id, writable=True)
    existing = proposals(db, round_id)
    identical = next(
        (p for p in existing if p.proposal_id == request.proposal_id), None
    )
    if identical:
        if (
            identical.kind != request.kind
            or identical.arguments != request.arguments
            or identical.decision is not None
        ):
            raise HTTPException(
                409,
                "This proposal has already been decided or differs from its checkpoint",
            )
        return get_round(db, round_id)
    if any(p.decision is None for p in existing):
        raise HTTPException(409, "A proposal is already waiting for approval")
    record = value["record"]
    before = (
        configurations.get_configuration(db, record["base_version"]).instructions
        if request.kind == "configuration"
        else configurations.get_recipe(db, record["recipe_version"]).recipe
    )
    after = request.arguments[
        "instructions" if request.kind == "configuration" else "recipe"
    ]
    proposal = Proposal(
        **request.model_dump(),
        diff=configurations.text_diff(before, after),
        created_at=now(),
        decision=None,
        feedback=None,
        handler=None,
        decided_at=None,
        version=None,
    )
    db.execute(
        insert(round_proposals).values(
            round_id=round_id,
            proposal_id=request.proposal_id,
            record=proposal.model_dump(),
        )
    )
    save(
        db,
        round_id,
        "awaiting_configuration"
        if request.kind == "configuration"
        else "awaiting_recipe",
        record,
    )
    return get_round(db, round_id)


def decide(
    db: Connection, round_id: str, proposal_id: str, decision: HandlerDecision
) -> Round:
    """Record the handler's decision before the service resumes its checkpoint."""
    value = row(db, round_id, writable=True)
    proposal = next(
        (p for p in proposals(db, round_id) if p.proposal_id == proposal_id), None
    )
    if proposal is None:
        raise HTTPException(404, "Proposal not found")
    if proposal.decision is not None or value["status"] not in (
        "awaiting_configuration",
        "awaiting_recipe",
    ):
        raise HTTPException(409, "This proposal is no longer waiting for approval")
    if decision.decision == "reject" and not (decision.reason or "").strip():
        raise HTTPException(422, "Give a reason for rejecting the proposal")
    if not decision.handler.can_approve_over_limit:
        raise HTTPException(403, "A senior handler must decide proposals")
    record = dict(value["record"])
    version = None
    if decision.decision == "approve":
        # Validate against the captured catalogue, rather than later platform changes.
        refusal = validate_proposal(db, round_id, proposal)
        if refusal:
            raise HTTPException(422, refusal)
        if proposal.kind == "configuration":
            approved = configurations.append_configuration(
                db,
                ConfigurationProposal(**proposal.arguments),
                configurations.Approval(
                    record["base_version"], decision.handler, round_id
                ),
                catalogue=get_round(db, round_id).models_allowed,
            )
            version = approved.version
            record["candidate_version"] = version
        else:
            approved_recipe = configurations.append_recipe(
                db,
                RecipeProposal(**proposal.arguments),
                configurations.Approval(
                    record["recipe_version"], decision.handler, round_id
                ),
            )
            version = approved_recipe.version
    updated = proposal.model_copy(
        update={
            "decision": decision.decision,
            "feedback": decision.reason,
            "handler": decision.handler.name,
            "decided_at": now(),
            "version": version,
        }
    )
    db.execute(
        update(round_proposals)
        .where(
            round_proposals.c.round_id == round_id,
            round_proposals.c.proposal_id == proposal_id,
        )
        .values(record=updated.model_dump())
    )
    save(db, round_id, "working", record)
    return get_round(db, round_id)


def progress(db: Connection, round_id: str, request: RoundProgress) -> Round:
    """Record observable work without allowing a service-supplied verdict."""
    value = row(db, round_id, writable=True)
    save(db, round_id, request.status, value["record"] | {"progress": request.progress})
    return get_round(db, round_id)


def baseline(db: Connection, round_id: str) -> dict:
    """Return only missing baseline runs and freeze the aggregate when complete."""
    value = row(db, round_id, writable=True)
    record = dict(value["record"])
    version = record["base_version"]
    missing = [
        name
        for name, count in EVALUATION_COUNTS.items()
        for _ in range(
            max(0, count - len(evaluations.version_evaluations(db, version, name)))
        )
    ]
    if not missing:
        scores = evaluations.version_scores(db, version, "held_out")
        record["before"] = scores.model_dump() if scores else None
        save(db, round_id, "working", record)
    return {"missing": missing, "configuration_version": version}


def evaluation_quote(db: Connection, round_id: str, request: EvaluationQuote):
    """Refuse unrelated versions or evaluations beyond the prescribed sequence."""
    value = row(db, round_id, writable=True)
    record = value["record"]
    if request.configuration_version not in (
        record["base_version"],
        record["candidate_version"],
    ):
        raise HTTPException(422, "The version does not belong to this round")
    needed = 1 if request.set_name == "evaluation" else 2
    count = len(
        evaluations.version_evaluations(
            db, request.configuration_version, request.set_name
        )
    )
    if count >= needed:
        raise HTTPException(
            409, "The prescribed evaluations for this version are complete"
        )
    configuration = configurations.get_configuration(db, request.configuration_version)
    return improvement_budget.quote(db, round_id, configuration.model, request.set_name)


def sequence_quote(db: Connection, round_id: str) -> Quote:
    """Check all remaining candidate tests before spending on the first one."""
    value = row(db, round_id, writable=True)
    record = value["record"]
    candidate = record["candidate_version"]
    if candidate is None:
        raise HTTPException(409, "A handler must approve a candidate before testing")
    quotes = []
    for name, required in EVALUATION_COUNTS.items():
        missing = max(
            0, required - len(evaluations.version_evaluations(db, candidate, name))
        )
        quotes.extend(
            evaluation_quote(
                db,
                round_id,
                EvaluationQuote(set_name=name, configuration_version=candidate),
            )
            for _ in range(missing)
        )
    remaining = improvement_budget.spending(
        db, round_id, record["settings"]["spending_limit"]
    ).remaining
    estimate = (
        sum(quote.estimate for quote in quotes if quote.estimate is not None)
        if all(quote.estimate is not None for quote in quotes)
        else None
    )
    stopped = (
        db.execute(
            select(runs.c.run_id).where(
                runs.c.configuration_version == candidate, runs.c.status == "stopped"
            )
        ).first()
        is not None
    )
    allowed = (
        not stopped
        and all(quote.allowed for quote in quotes)
        and estimate is not None
        and estimate <= remaining
    )
    reason = (
        "Candidate sequence is affordable"
        if allowed
        else (
            f"Candidate sequence refused: estimated ${estimate:.4f}, "
            f"remaining ${remaining:.4f}"
            if estimate is not None
            else "Candidate sequence has no reliable estimate"
        )
    )
    if stopped:
        reason = "A candidate evaluation stopped; the candidate cannot be verified"
    if not allowed:
        save(db, round_id, value["status"], record | {"evaluation_refusal": reason})
    return Quote(estimate=estimate, remaining=remaining, allowed=allowed, reason=reason)


def apply_verdict(db: Connection, round_id: str) -> Round:
    """Apply the backend's rule and activate a kept version atomically."""
    value = row(db, round_id, writable=True)
    record = dict(value["record"])
    if record["verdict"] is not None:
        return get_round(db, round_id)
    candidate = record["candidate_version"]
    complete = (
        candidate is not None
        and len(evaluations.version_evaluations(db, candidate, "evaluation")) >= 1
        and len(evaluations.version_evaluations(db, candidate, "held_out"))
        >= EVALUATION_COUNTS["held_out"]
    )
    after = (
        evaluations.version_scores(db, candidate, "held_out")
        if candidate is not None
        else None
    )
    before = get_round(db, round_id).before
    keep, reason = improvement_rules.verdict(
        before, after if complete else None, get_round(db, round_id).settings
    )
    if not complete and record.get("evaluation_refusal"):
        reason = record["evaluation_refusal"]
    if keep and candidate is not None:
        configurations.activate_configuration(db, candidate)
    record.update(
        after=after.model_dump() if after else None,
        verdict="kept" if keep else "rolled_back",
        explanation=reason,
    )
    save(db, round_id, "working", record)
    return get_round(db, round_id)


def finish(db: Connection, round_id: str, stopped: bool, reason: str) -> Round:
    """Close a round, retaining any already committed activation verdict."""
    value = row(db, round_id, writable=True)
    record = dict(value["record"])
    if record["candidate_version"] is not None and record["verdict"] is None:
        record.update(verdict="rolled_back", explanation=reason)
    elif record["explanation"] is None:
        record["explanation"] = reason
    record["finished_at"] = now()
    save(db, round_id, "stopped" if stopped else "finished", record)
    return get_round(db, round_id)


def record_usage(db: Connection, round_id: str, usage: RoundUsage) -> dict[str, str]:
    """Retain each received provider charge once, even after a handler stops work."""
    row(db, round_id)
    insert_once(
        db,
        round_model_calls,
        {
            "round_id": round_id,
            "reply_id": usage.reply_id,
            "cost": usage.cost,
            "record": usage.model_dump(),
        },
    )
    return {"status": "recorded"}


def report(db: Connection, round_id: str) -> dict:
    """Expose development examples and held-out aggregates, never held-out claims."""
    current = get_round(db, round_id)
    effective_version = configurations.current_version(db)
    return {
        "round": current.model_dump(),
        "configuration": configurations.get_configuration(
            db, effective_version
        ).model_dump(),
        "recipe": configurations.get_recipe(db, current.recipe_version).model_dump(),
        "evaluation_examples": evaluations.development_examples(db, effective_version),
        "rule": improvement_rules.RULE_DESCRIPTION,
        "scores": {
            str(version): {
                name: scores.model_dump()
                if (scores := evaluations.version_scores(db, version, name))
                else None
                for name in ("evaluation", "held_out")
            }
            for version in (current.base_version, current.candidate_version)
            if version is not None
        },
        "earlier_rounds": [
            {
                "round_id": item.round_id,
                "base_version": item.base_version,
                "candidate_version": item.candidate_version,
                "before": item.before.model_dump() if item.before else None,
                "after": item.after.model_dump() if item.after else None,
                "verdict": item.verdict,
                "explanation": item.explanation,
            }
            for item in list_rounds(db)
            if item.round_id != round_id
        ],
    }


def recover(db: Connection) -> list[str]:
    """Preserve pending approvals and close abandoned active stretches."""
    identities = list(
        db.execute(
            select(rounds.c.round_id).where(
                rounds.c.status.in_(("baseline", "working", "evaluating"))
            )
        ).scalars()
    )
    for identity in identities:
        finish(
            db, identity, True, "Agent service restarted before this stretch finished"
        )
    return identities
