"""Read the seeded document records used by the app."""

from sqlalchemy import Connection, select

from backend.database import documents


def contents(db: Connection) -> str:
    """Return the numbered contents of both seeded documents."""
    query = select(documents.c.contents).order_by(documents.c.position)
    return "\n\n".join(db.execute(query).scalars()) + "\n"
