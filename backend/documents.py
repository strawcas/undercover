"""Read the seeded document records used by the app."""

from sqlalchemy import Connection, select

from backend.database import documents

CLAIMS_MANUAL = "claims_manual"


def contents(db: Connection) -> str:
    """Return the numbered contents of both seeded documents."""
    query = select(documents.c.contents).order_by(documents.c.position)
    return "\n\n".join(db.execute(query).scalars()) + "\n"


def manual_version(db: Connection) -> str:
    """Return the seeded manual version for new decision records."""
    query = select(documents.c.version).where(documents.c.name == CLAIMS_MANUAL)
    return db.execute(query).scalar_one()
