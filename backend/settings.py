"""Platform settings, immutable snapshots and handler-attributed changes."""

from sqlalchemy import Connection, insert, select, update
from sqlalchemy.sql.selectable import ScalarSelect

from backend.clock import now
from backend.database import setting_changes, settings
from backend.models import Handler
from backend.settings_models import (
    AllowedModel,
    PlatformSettings,
    SettingChange,
    SettingsView,
)


def get_settings(db: Connection) -> PlatformSettings:
    """Read the business settings used for new decisions and rounds."""
    row = db.execute(select(settings)).mappings().one()
    return PlatformSettings(
        **row["improvement"], payout_approval_limit=row["payout_approval_limit"]
    )


def approval_limit(db: Connection) -> float:
    """Return the current limit for a newly recorded decision."""
    return db.execute(select(settings.c.payout_approval_limit)).scalar_one()


def decision_limit() -> ScalarSelect[float]:
    """Capture and lock the limit in the same statement as a decision insert."""
    return (
        select(settings.c.payout_approval_limit)
        .with_for_update(read=True)
        .scalar_subquery()
    )


def allowed_models(db: Connection) -> dict[str, AllowedModel]:
    """Read the file-owned model catalogue as seeded in the database."""
    values = db.execute(select(settings.c.models_allowed)).scalar_one()
    return {name: AllowedModel(**value) for name, value in values.items()}


def view(db: Connection) -> SettingsView:
    """Show settings, allowed models and newest-first change history."""
    changes = db.execute(
        select(setting_changes).order_by(setting_changes.c.id.desc())
    ).mappings()
    return SettingsView(
        values=get_settings(db),
        models_allowed=allowed_models(db),
        changes=[SettingChange(**row) for row in changes],
    )


def change_settings(
    db: Connection, values: PlatformSettings, handler: Handler
) -> SettingsView:
    """Save the new business settings and their audit record together."""
    db.execute(select(settings.c.id).with_for_update()).one()
    before = get_settings(db)
    if before != values:
        db.execute(
            update(settings).values(
                payout_approval_limit=values.payout_approval_limit,
                improvement=values.model_dump(exclude={"payout_approval_limit"}),
            )
        )
        db.execute(
            insert(setting_changes).values(
                handler=handler.name,
                changed_at=now(),
                before=before.model_dump(),
                after=values.model_dump(),
            )
        )
    return view(db)
