"""The UnderCover database, seeded from the business data in data/.

The same tables and queries serve Postgres on Supabase, when DATABASE_URL is set, and
SQLite otherwise, as in the tests.
"""

import json
import os
from functools import cache
from importlib import import_module
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
    event,
    false,
    insert,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import Insert as PostgresInsert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import Insert as SQLiteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, CreateTable

from backend.clock import now
from backend.configuration_defaults import initial_model

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCHEMA = "undercover"
CONNECTIONS = 4
LAMBDA_MARKER = "AWS_LAMBDA_FUNCTION_NAME"

metadata = MetaData()


def conflict_insert(db: Connection, table: Table) -> PostgresInsert | SQLiteInsert:
    """Build an insert with the database's native conflict handling."""
    dialect_insert = (
        postgres_insert if db.dialect.name == "postgresql" else sqlite_insert
    )
    return dialect_insert(table)


def insert_once(
    db: Connection, table: Table, values: dict[str, Any] | list[dict[str, Any]]
) -> None:
    """Atomically insert a record unless its primary key was already saved."""
    db.execute(conflict_insert(db, table).values(values).on_conflict_do_nothing())


settings = Table(
    "settings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("payout_approval_limit", Float, nullable=False),
    Column("plans", JSON, nullable=False),
    Column("improvement", JSON, nullable=False),
    Column("models_allowed", JSON, nullable=False),
    Column(
        "current_configuration",
        Integer,
        ForeignKey("configurations.version"),
        nullable=False,
    ),
    Column("current_recipe", Integer, ForeignKey("recipes.version"), nullable=False),
)
configurations = Table(
    "configurations",
    metadata,
    Column("version", Integer, primary_key=True),
    Column("record", JSON, nullable=False),
)
recipes = Table(
    "recipes",
    metadata,
    Column("version", Integer, primary_key=True),
    Column("record", JSON, nullable=False),
)
setting_changes = Table(
    "setting_changes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("handler", Text, nullable=False),
    Column("changed_at", Text, nullable=False),
    Column("before", JSON, nullable=False),
    Column("after", JSON, nullable=False),
)
documents = Table(
    "documents",
    metadata,
    Column("name", Text, primary_key=True),
    Column("position", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("contents", Text, nullable=False),
)
handlers = Table(
    "handlers",
    metadata,
    Column("handler_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("email", Text, nullable=False, unique=True),
    Column("can_approve_over_limit", Boolean, nullable=False),
)
copilot_conversations = Table(
    "copilot_conversations",
    metadata,
    Column("conversation_id", Text, primary_key=True),
    Column(
        "handler_id",
        Text,
        ForeignKey("handlers.handler_id"),
        nullable=False,
        index=True,
    ),
    Column("title", Text),
    Column("updated_at", Text, nullable=False),
    Column("current_turn", Text),
)
copilot_turns = Table(
    "copilot_turns",
    metadata,
    Column("turn_id", Text, primary_key=True),
    Column(
        "conversation_id",
        Text,
        ForeignKey("copilot_conversations.conversation_id"),
        nullable=False,
        index=True,
    ),
    Column("message", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("deadline", Text, nullable=False),
    Column("stretch_id", Text, nullable=False),
    Column("claimed", Boolean, nullable=False),
    Column("answer", Text),
    Column("failure", Text),
    Column("confirmation", JSON(none_as_null=True)),
    Column("confirmed", Boolean),
    Column("usage", JSON(none_as_null=True)),
)
copilot_steps = Table(
    "copilot_steps",
    metadata,
    Column("turn_id", Text, ForeignKey("copilot_turns.turn_id"), primary_key=True),
    Column("step_id", Text, primary_key=True),
    Column("started_at", Text, nullable=False),
    Column("record", JSON, nullable=False),
)
copilot_actions = Table(
    "copilot_actions",
    metadata,
    Column("turn_id", Text, ForeignKey("copilot_turns.turn_id"), primary_key=True),
    Column("action_id", Text, primary_key=True),
    Column("reference", Text, nullable=False),
)
COPILOT_TABLES = [copilot_conversations, copilot_turns, copilot_steps, copilot_actions]
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
    Column("latest_calculation", JSON),
    Column("memory_pending", Boolean, nullable=False, server_default=false()),
    Column("attempts", Integer, nullable=False),
    Column("evaluation_id", Text, ForeignKey("runs.run_id")),
    Column("source_id", Text),
)
work_gate = Table(
    "work_gate",
    metadata,
    Column("id", Integer, primary_key=True),
)
decisions = Table(
    "decisions",
    metadata,
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("decision", Text, nullable=False),
    Column("payout", Float, nullable=False),
    Column("section", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("calculation", JSON),
    Column("decided_by", Text, nullable=False),
    Column("decided_at", Text, nullable=False),
    Column("manual_version", Text, nullable=False),
    Column("approval_limit", Float, nullable=False),
    Column("gateway_hold", Text),
    Column("signed_off_by", Text),
    Column("signed_off_at", Text),
)
claim_reviews = Table(
    "claim_reviews",
    metadata,
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("history", JSON, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("owner", Text),
    Column("run_id", Text),
    Column("heartbeat_at", Text),
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
    Column("content", JSON, nullable=False),
    Column("created_at", Text, nullable=False),
)
rounds = Table(
    "rounds",
    metadata,
    Column("number", Integer, primary_key=True),
    Column("round_id", Text, nullable=False, unique=True),
    Column("status", Text, nullable=False),
    Column("record", JSON, nullable=False),
)
round_proposals = Table(
    "round_proposals",
    metadata,
    Column("round_id", Text, ForeignKey("rounds.round_id"), primary_key=True),
    Column("proposal_id", Text, primary_key=True),
    Column("record", JSON, nullable=False),
)
round_model_calls = Table(
    "round_model_calls",
    metadata,
    Column("round_id", Text, ForeignKey("rounds.round_id"), primary_key=True),
    Column("reply_id", Text, primary_key=True),
    Column("cost", Float),
    Column("record", JSON, nullable=False),
)
runs = Table(
    "runs",
    metadata,
    Column("number", Integer, primary_key=True),
    Column("run_id", Text, nullable=False, unique=True),
    Column("kind", Text, nullable=False),
    Column("concurrency", Integer, nullable=False),
    Column("model", Text, nullable=False),
    Column(
        "configuration_version",
        Integer,
        ForeignKey("configurations.version"),
        nullable=False,
    ),
    Column("reasoning_effort", Text),
    Column("round_id", Text, ForeignKey("rounds.round_id")),
    Column("grading_started", Boolean, nullable=False, default=False),
    Column("scores", JSON),
    Column("set_name", Text),
    Column("tiny", Boolean),
    Column("status", Text, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text),
    Column("summary", Text),
    Column("heartbeat_at", Text),
    Column("stop_reason", Text),
)
run_claims = Table(
    "run_claims",
    metadata,
    Column("run_id", Text, ForeignKey("runs.run_id"), primary_key=True),
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("position", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("error", Text),
    Column("result", JSON),
)
evaluation_grades = Table(
    "evaluation_grades",
    metadata,
    Column("run_id", Text, ForeignKey("runs.run_id"), primary_key=True),
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("record", JSON, nullable=False),
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
evaluation_sources = Table(
    "evaluation_sources",
    metadata,
    Column("set_name", Text, primary_key=True),
    Column("source_id", Text, primary_key=True),
    Column("record", JSON, nullable=False),
)
evaluation_labels = Table(
    "evaluation_labels",
    metadata,
    Column("set_name", Text, primary_key=True),
    Column("source_id", Text, primary_key=True),
    Column("tiny", Boolean, nullable=False),
    Column("record", JSON, nullable=False),
)

claim_view = (
    select(
        claims.c.claim_id,
        claims.c.record,
        claims.c.status,
        claims.c.thread_id,
        claims.c.latest_calculation,
        claims.c.memory_pending,
        claims.c.attempts,
        claims.c.evaluation_id,
        claims.c.source_id,
        claim_reviews.c.history.label("returned_decisions"),
        claim_reviews.c.revision,
        claim_reviews.c.owner.label("review_owner"),
        claim_reviews.c.run_id.label("review_run_id"),
        runs.c.status.label("review_run_status"),
        claim_reviews.c.heartbeat_at.label("review_heartbeat"),
        customers.c.name.label("customer_name"),
        decisions.c.decision,
        decisions.c.payout,
        decisions.c.section,
        decisions.c.reason,
        decisions.c.calculation,
        decisions.c.decided_by,
        decisions.c.decided_at,
        decisions.c.manual_version,
        decisions.c.approval_limit,
        decisions.c.gateway_hold,
        select(settings.c.payout_approval_limit)
        .scalar_subquery()
        .label("current_limit"),
        decisions.c.signed_off_by,
        decisions.c.signed_off_at,
        payments.c.amount,
        payments.c.paid_at,
    )
    .join(customers, customers.c.customer_id == claims.c.customer_id)
    .outerjoin(decisions, decisions.c.claim_id == claims.c.claim_id)
    .outerjoin(payments, payments.c.claim_id == claims.c.claim_id)
    .outerjoin(claim_reviews, claim_reviews.c.claim_id == claims.c.claim_id)
    .outerjoin(runs, runs.c.run_id == claim_reviews.c.run_id)
)


def database_url() -> URL:
    """Return Postgres at DATABASE_URL, through psycopg and in our own schema, or
    SQLite at UNDERCOVER_DB or undercover.db."""
    if url := os.getenv("DATABASE_URL"):
        postgres = make_url(url).set(drivername="postgresql+psycopg")
        if running_on_lambda():
            return postgres
        return postgres.update_query_dict({"options": f"-c search_path={SCHEMA}"})
    path = os.getenv("UNDERCOVER_DB", str(ROOT / "undercover.db"))
    return URL.create("sqlite", database=path)


def on_postgres(url: URL) -> bool:
    """Say whether a database is Postgres rather than SQLite."""
    return url.get_backend_name() == "postgresql"


@cache
def engine_for(url: URL, *, lambda_runtime: bool = False) -> Engine:
    """Open a database: Postgres with at most CONNECTIONS connections, or SQLite with
    a connection for each request."""
    if on_postgres(url):
        if lambda_runtime:
            db = create_engine(
                url, poolclass=NullPool, connect_args={"prepare_threshold": None}
            )
            event.listen(db, "begin", set_transaction_schema)
            return db
        return create_engine(
            url,
            pool_size=CONNECTIONS,
            max_overflow=0,
            pool_pre_ping=True,
            connect_args={"application_name": f"undercover-backend-{os.getpid()}"},
        )
    return create_engine(url, poolclass=NullPool)


def set_transaction_schema(db: Connection) -> None:
    """Select the schema within the Supabase pooler's current transaction."""
    db.exec_driver_sql(f"SET LOCAL search_path TO {SCHEMA}")


def running_on_lambda() -> bool:
    """Use Lambda's marker to select the transaction pooler settings."""
    return LAMBDA_MARKER in os.environ


def engine() -> Engine:
    """Return the backend's database."""
    return engine_for(database_url(), lambda_runtime=running_on_lambda())


def location(db: Engine) -> str:
    """Say where a database is, for a person to read."""
    if on_postgres(db.url):
        return f"the {SCHEMA} schema on Postgres"
    return Path(str(db.url.database)).name


def is_seeded(db: Engine) -> bool:
    """Say whether the database has its tables."""
    return inspect(db).has_table("claims")


def upgrade(db: Engine) -> None:
    """Add Runtime and Copilot tables to a previously seeded business database."""
    with db.begin() as connection:
        connection.execute(CreateTable(claim_reviews, if_not_exists=True))
        connection.execute(CreateTable(work_gate, if_not_exists=True))
        insert_once(connection, work_gate, {"id": 1})
        connection.execute(update(work_gate).where(work_gate.c.id == 1).values(id=1))
        metadata.create_all(connection, tables=COPILOT_TABLES)
        existing = {
            column["name"] for column in inspect(connection).get_columns("runs")
        }
        if "heartbeat_at" not in existing:
            connection.execute(text("ALTER TABLE runs ADD COLUMN heartbeat_at TEXT"))
        if "stop_reason" not in existing:
            connection.execute(text("ALTER TABLE runs ADD COLUMN stop_reason TEXT"))


def load(name: str) -> Any:
    """Read one of the JSON files in data/."""
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def clear_copilot_checkpoints(db: Connection) -> None:
    """Remove Copilot turn threads from the shared LangGraph checkpoint tables."""
    catalog = inspect(db)
    if not catalog.has_table(copilot_turns.name):
        return
    for name in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        if catalog.has_table(name):
            table = Table(name, MetaData(), Column("thread_id", Text))
            db.execute(table.delete().where(table.c.thread_id.like("copilot:%")))


def seed(db: Engine) -> dict[str, int]:
    """Build the tables afresh from the seed and governance files."""
    catalog = import_module("context.catalog")
    _, plans = catalog.read_yaml(catalog.PLANS_FILE)
    source_documents = [catalog.read_document(name) for name in catalog.TITLES]
    company = load("company.json")
    handler_rows = load("handlers.json")
    customer_rows = load("customers.json") + load("security/customers.json")
    policy_rows = load("policies.json") + load("security/policies.json")
    evaluation_rows = load("claims.json")
    claim_rows = evaluation_rows + load("security/claims.json")
    with db.begin() as connection:
        if on_postgres(db.url):
            connection.execute(CreateSchema(SCHEMA, if_not_exists=True))
        connection.execute(text("DROP TABLE IF EXISTS todos"))
        clear_copilot_checkpoints(connection)
        metadata.drop_all(connection)
        metadata.create_all(connection)
        connection.execute(insert(work_gate).values(id=1))
        configuration = initial_model()
        provenance = {
            "parent_version": None,
            "reason": "Initial version",
            "proposed_by": "UnderCover",
            "approved_by": None,
            "approved_at": None,
            "created_at": now(),
        }
        instruction_dir = ROOT / "backend" / "instructions"
        connection.execute(
            insert(configurations).values(
                record=provenance
                | configuration
                | {
                    "instructions": (instruction_dir / "claim.md").read_text(
                        encoding="utf-8"
                    )
                },
            )
        )
        connection.execute(
            insert(recipes).values(
                record=provenance
                | {
                    "recipe": (instruction_dir / "recipe.md").read_text(
                        encoding="utf-8"
                    )
                },
            )
        )
        allowed = (
            load("allowed_models.json")
            if configuration["reasoning_effort"] != "default"
            else {
                configuration["model"]: {
                    "reasoning_efforts": ["default"],
                    "input_price": 0,
                    "output_price": 0,
                }
            }
        )
        connection.execute(
            insert(settings),
            {
                "id": 1,
                "payout_approval_limit": company["payout_approval_limit"],
                "plans": plans,
                "improvement": load("improvement_settings.json"),
                "models_allowed": allowed,
                "current_configuration": 1,
                "current_recipe": 1,
            },
        )
        connection.execute(
            insert(documents),
            [
                {
                    "name": document.name,
                    "position": position,
                    "title": document.title,
                    "text": document.text,
                    "version": document.version,
                    "contents": document.contents(),
                }
                for position, document in enumerate(source_documents)
            ],
        )
        connection.execute(
            insert(handlers),
            [
                {
                    "handler_id": h["handler_id"],
                    "name": h["name"],
                    "role": h["role"],
                    "email": h["email"],
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
        for set_name, sources, labels in (
            ("evaluation", evaluation_rows, load("evals/decisions.json")),
            (
                "held_out",
                load("evals/held_out_claims.json"),
                load("evals/held_out_decisions.json"),
            ),
        ):
            connection.execute(
                insert(evaluation_sources),
                [
                    {"set_name": set_name, "source_id": row["claim_id"], "record": row}
                    for row in sources
                ],
            )
            connection.execute(
                insert(evaluation_labels),
                [
                    {
                        "set_name": set_name,
                        "source_id": row["claim_id"],
                        "tiny": row["tiny"],
                        "record": row,
                    }
                    for row in labels
                ],
            )
    return {
        "claims": len(claim_rows),
        "policies": len(policy_rows),
        "customers": len(customer_rows),
        "handlers": len(handler_rows),
    }
