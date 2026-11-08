"""The UnderCover database, seeded from the business data in data/.

The same tables and queries serve Postgres on Supabase, when DATABASE_URL is set, and
SQLite otherwise, as in the tests.
"""

import json
import os
from functools import cache
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Connection,
    Engine,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    insert,
    inspect,
    select,
)
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCUMENTS = {"policy_wording": "Policy wording", "claims_manual": "Claims manual"}
SCHEMA = "undercover"
CONNECTIONS = 4

metadata = MetaData()


def insert_once(db: Connection, table: Table, values: dict[str, Any]) -> None:
    """Atomically insert a record unless its primary key was already saved."""
    dialect_insert = (
        postgres_insert if db.dialect.name == "postgresql" else sqlite_insert
    )
    db.execute(dialect_insert(table).values(**values).on_conflict_do_nothing())


settings = Table(
    "settings",
    metadata,
    Column("payout_approval_limit", Float, nullable=False),
    Column("plans", JSON, nullable=False),
)
documents = Table(
    "documents",
    metadata,
    Column("name", Text, primary_key=True),
    Column("position", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("text", Text, nullable=False),
)
handlers = Table(
    "handlers",
    metadata,
    Column("handler_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("can_approve_over_limit", Boolean, nullable=False),
)
customers = Table(
    "customers",
    metadata,
    Column("customer_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("record", JSON, nullable=False),
)
policies = Table(
    "policies",
    metadata,
    Column("policy_id", Text, primary_key=True),
    Column("record", JSON, nullable=False),
)
claims = Table(
    "claims",
    metadata,
    Column("claim_id", Text, primary_key=True),
    Column("customer_id", Text, ForeignKey("customers.customer_id"), nullable=False),
    Column("record", JSON, nullable=False),
    Column("status", Text, nullable=False),
    Column("thread_id", Text),
    Column("attempts", Integer, nullable=False),
)
decisions = Table(
    "decisions",
    metadata,
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("decision", Text, nullable=False),
    Column("payout", Float, nullable=False),
    Column("section", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("decided_at", Text, nullable=False),
    Column("signed_off_by", Text),
    Column("signed_off_at", Text),
)
payments = Table(
    "payments",
    metadata,
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("amount", Float, nullable=False),
    Column("paid_at", Text, nullable=False),
)
steps = Table(
    "steps",
    metadata,
    Column("step_id", Integer, primary_key=True),
    Column("claim_id", Text, ForeignKey("claims.claim_id"), nullable=False),
    Column("thread_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)
runs = Table(
    "runs",
    metadata,
    Column("number", Integer, primary_key=True),
    Column("run_id", Text, nullable=False, unique=True),
    Column("cap", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text),
    Column("summary", Text),
)
todos = Table(
    "todos",
    metadata,
    Column("run_id", Text, ForeignKey("runs.run_id"), primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("content", Text, nullable=False),
    Column("status", Text, nullable=False),
)
model_calls = Table(
    "model_calls",
    metadata,
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("thread_id", Text, primary_key=True),
    Column("reply_id", Text, primary_key=True),
    Column("run_id", Text, ForeignKey("runs.run_id")),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("total_tokens", Integer),
    Column("cost", Float),
)
work_slices = Table(
    "work_slices",
    metadata,
    Column("slice_id", Text, primary_key=True),
    Column("claim_id", Text, ForeignKey("claims.claim_id"), nullable=False),
    Column("thread_id", Text, nullable=False),
    Column("run_id", Text, ForeignKey("runs.run_id")),
    Column("seconds", Float, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

claim_view = (
    select(
        claims.c.claim_id,
        claims.c.record,
        claims.c.status,
        claims.c.thread_id,
        claims.c.attempts,
        customers.c.name.label("customer_name"),
        decisions.c.decision,
        decisions.c.payout,
        decisions.c.section,
        decisions.c.reason,
        decisions.c.decided_at,
        decisions.c.signed_off_by,
        decisions.c.signed_off_at,
        payments.c.amount,
        payments.c.paid_at,
    )
    .join(customers, customers.c.customer_id == claims.c.customer_id)
    .outerjoin(decisions, decisions.c.claim_id == claims.c.claim_id)
    .outerjoin(payments, payments.c.claim_id == claims.c.claim_id)
)


def database_url() -> URL:
    """Return Postgres at DATABASE_URL, through psycopg and in our own schema, or
    SQLite at UNDERCOVER_DB or undercover.db."""
    if url := os.getenv("DATABASE_URL"):
        postgres = make_url(url).set(drivername="postgresql+psycopg")
        return postgres.update_query_dict({"options": f"-c search_path={SCHEMA}"})
    path = os.getenv("UNDERCOVER_DB", str(ROOT / "undercover.db"))
    return URL.create("sqlite", database=path)


def on_postgres(url: URL) -> bool:
    """Say whether a database is Postgres rather than SQLite."""
    return url.get_backend_name() == "postgresql"


@cache
def engine_for(url: URL) -> Engine:
    """Open a database: Postgres with at most CONNECTIONS connections, or SQLite with
    a connection for each request."""
    if on_postgres(url):
        return create_engine(
            url, pool_size=CONNECTIONS, max_overflow=0, pool_pre_ping=True
        )
    return create_engine(url, poolclass=NullPool)


def engine() -> Engine:
    """Return the backend's database."""
    return engine_for(database_url())


def location(db: Engine) -> str:
    """Say where a database is, for a person to read."""
    if on_postgres(db.url):
        return f"the {SCHEMA} schema on Postgres"
    return Path(str(db.url.database)).name


def is_seeded(db: Engine) -> bool:
    """Say whether the database has its tables."""
    return inspect(db).has_table("claims")


def load(name: str) -> Any:
    """Read one of the JSON files in data/."""
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def seed(db: Engine) -> dict[str, int]:
    """Build the tables afresh from data/ and count what they hold."""
    company = load("company.json")
    handler_rows = load("handlers.json")
    customer_rows = load("customers.json")
    policy_rows = load("policies.json")
    claim_rows = load("claims.json")
    with db.begin() as connection:
        if on_postgres(db.url):
            connection.execute(CreateSchema(SCHEMA, if_not_exists=True))
        metadata.drop_all(connection)
        metadata.create_all(connection)
        connection.execute(
            insert(settings),
            {
                "payout_approval_limit": company["payout_approval_limit"],
                "plans": load("plans.json"),
            },
        )
        connection.execute(
            insert(documents),
            [
                {
                    "name": name,
                    "position": position,
                    "title": title,
                    "text": (DATA / f"{name}.md").read_text(encoding="utf-8"),
                }
                for position, (name, title) in enumerate(DOCUMENTS.items())
            ],
        )
        connection.execute(
            insert(handlers),
            [
                {
                    "handler_id": h["handler_id"],
                    "name": h["name"],
                    "role": h["role"],
                    "can_approve_over_limit": h["can_approve_over_limit"],
                }
                for h in handler_rows
            ],
        )
        connection.execute(
            insert(customers),
            [
                {"customer_id": c["customer_id"], "name": c["name"], "record": c}
                for c in customer_rows
            ],
        )
        connection.execute(
            insert(policies),
            [{"policy_id": p["policy_id"], "record": p} for p in policy_rows],
        )
        connection.execute(
            insert(claims),
            [
                {
                    "claim_id": c["claim_id"],
                    "customer_id": c["customer_id"],
                    "record": c,
                    "status": "waiting",
                    "thread_id": None,
                    "attempts": 0,
                }
                for c in claim_rows
            ],
        )
    return {
        "claims": len(claim_rows),
        "policies": len(policy_rows),
        "customers": len(customer_rows),
        "handlers": len(handler_rows),
    }
