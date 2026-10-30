"""Business records on SQLAlchemy Core, using SQLite or hosted Postgres."""

import json
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from backend.config import database_identity
from backend.database import Connection
from backend.diagnostics import preserve_primary
from backend.harness_storage import migrate
from backend.models import Claim, Company, Customer, Handler, Policy
from backend.ownership import DatabaseOwner, PostgresOwner
from backend.reference_index import install

ROOT = Path(__file__).resolve().parents[1]
SEED_FILES = (
    "claims.json",
    "policies.json",
    "customers.json",
    "plans.json",
    "handlers.json",
    "company.json",
    "policy_wording.md",
    "claims_manual.md",
)


def encode(value: Any) -> Any:
    """Store source monetary decimals as cents inside JSON records."""
    if isinstance(value, Decimal):
        return {"usd_cents": int(value * 100)}
    if isinstance(value, list):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    return value


def decode(value: Any) -> Any:
    """Expose exact USD strings for stored monetary values."""
    if isinstance(value, dict) and set(value) == {"usd_cents"}:
        return f"{Decimal(value['usd_cents']) / 100:.2f}"
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, dict):
        return {key: decode(item) for key, item in value.items()}
    return value


class Store:
    """Bound connections and preserve a single portable business query surface."""

    def __init__(self, path: Path | str):
        self.postgres = isinstance(path, str) and path.startswith(
            ("postgres://", "postgresql://", "postgresql+psycopg://")
        )
        self.path = Path(path).resolve() if not self.postgres else ROOT / "postgres"
        target = (
            make_url(str(path).replace("postgres://", "postgresql://", 1)).set(
                drivername="postgresql+psycopg"
            )
            if self.postgres
            else URL.create("sqlite", database=str(self.path))
        )
        self.target = target
        self.identity = database_identity(
            target.render_as_string(hide_password=False) if self.postgres else self.path
        )
        self.slots = BoundedSemaphore(4)
        options: dict[str, Any] = (
            {"pool_size": 4, "max_overflow": 0}
            if self.postgres
            else {"poolclass": NullPool}
        )
        self.engine = create_engine(
            target,
            **options,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"options": "-c search_path=undercover", "connect_timeout": 10}
            if self.postgres
            else {"timeout": 10, "check_same_thread": False},
        )

    @contextmanager
    def connect(self):
        with self.slots:
            raw = self.engine.connect()
            try:
                yield Connection(raw)
            finally:
                with preserve_primary("Close database connection"):
                    raw.close()

    @contextmanager
    def transaction(self):
        with self.connect() as connection:
            if self.postgres:
                connection.execute("SELECT pg_advisory_xact_lock(70914002)")
            else:
                connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                with preserve_primary("Roll back database transaction"):
                    connection.rollback()
                raise

    def initialize(self, data_dir: Path = ROOT / "data") -> None:
        """Create storage and seed the explicit product file allowlist once."""
        with self.transaction() as connection:
            if self.postgres:
                connection.execute("CREATE SCHEMA IF NOT EXISTS undercover")
            migrate(connection)
            self._migrate_positions(connection)
            if not connection.execute("SELECT 1 FROM records LIMIT 1").fetchone():
                self._seed(connection, data_dir)
            self._harness_documents(connection, data_dir)

    def dispose(self) -> None:
        self.engine.dispose()

    def owner(self, *, maintenance: bool = False):
        return (
            PostgresOwner(self.engine, maintenance=maintenance)
            if self.postgres
            else DatabaseOwner(self.path)
        )

    def _harness_documents(self, connection: Connection, data_dir: Path) -> None:
        for key, path in (
            ("instructions", ROOT / "agent/instructions.md"),
            ("queue_instructions", ROOT / "agent/queue_instructions.md"),
        ):
            connection.execute(
                "INSERT INTO records (kind,id,body) VALUES ('settings', ?, ?) "
                "ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
                (key, json.dumps(path.read_text(encoding="utf-8"))),
            )
        if not connection.execute(
            "SELECT 1 FROM records WHERE kind='settings' AND id='manual'"
        ).fetchone():
            self._insert(
                connection,
                "settings",
                "manual",
                (data_dir / "claims_manual.md").read_text(encoding="utf-8"),
            )
        for source, key in (("policy_wording", "wording"), ("claims_manual", "manual")):
            body = connection.execute(
                "SELECT body FROM records WHERE kind='settings' AND id=?", (key,)
            ).fetchone()["body"]
            install(connection, source, json.loads(body))

    @staticmethod
    def _migrate_positions(connection: Connection) -> None:
        position = connection.execute(
            "SELECT COALESCE(MAX(queue_position), 0) FROM claims"
        ).fetchone()[0]
        for row in connection.execute(
            "SELECT id FROM claims WHERE queue_position IS NULL ORDER BY id"
        ).fetchall():
            position += 1
            connection.execute(
                "UPDATE claims SET queue_position=? WHERE id=?", (position, row["id"])
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS queue_positions "
            "ON claims(queue_position)"
        )

    def add_claim(self, claim: dict[str, Any]) -> None:
        """Append a waiting claim with existing linked records to the queue."""
        policy = self.record("policy", claim["policy_id"])
        self.record("customer", claim["customer_id"])
        if policy["customer_id"] != claim["customer_id"]:
            raise ValueError("The claim's policy must belong to its customer.")
        with self.transaction() as connection:
            self._enqueue(connection, claim)

    def _enqueue(self, connection: Connection, claim: dict[str, Any]) -> None:
        record = Claim.model_validate(claim | {"status": "waiting"}).model_dump()
        self._insert(connection, "claim", record["claim_id"], record)
        connection.execute(
            "INSERT INTO claims (id, state, queue_position) "
            "SELECT ?, 'waiting', COALESCE(MAX(queue_position), 0) + 1 FROM claims",
            (record["claim_id"],),
        )

    def _seed(self, connection: Connection, data_dir: Path) -> None:
        sources: dict[str, Any] = {}
        for name in SEED_FILES:
            content = (data_dir / name).read_text(encoding="utf-8")
            sources[name] = (
                json.loads(content, parse_float=Decimal)
                if name.endswith(".json")
                else content
            )
        self._validate_links(sources)
        claims = sources["claims.json"]
        policy_ids = {claim["policy_id"] for claim in claims}
        customer_ids = {claim["customer_id"] for claim in claims}
        for claim in claims:
            self._enqueue(connection, claim)
        linked = (
            ("policy", "policies.json", "policy_id", policy_ids, Policy),
            ("customer", "customers.json", "customer_id", customer_ids, Customer),
        )
        for kind, filename, key, permitted, model in linked:
            for record in sources[filename]:
                if record[key] in permitted:
                    model.model_validate(record)
                    self._insert(connection, kind, record[key], record)
        for handler in sources["handlers.json"]:
            Handler.model_validate(handler)
        company = Company.model_validate(sources["company.json"])
        self._insert(connection, "settings", "company", company.model_dump())
        self._insert(connection, "settings", "plans", sources["plans.json"])
        self._insert(connection, "settings", "handlers", sources["handlers.json"])
        self._insert(connection, "settings", "wording", sources["policy_wording.md"])
        self._insert(connection, "settings", "manual", sources["claims_manual.md"])
        self._insert(
            connection,
            "settings",
            "instructions",
            (ROOT / "agent" / "instructions.md").read_text(encoding="utf-8"),
        )

    @staticmethod
    def _validate_links(sources: dict[str, Any]) -> None:
        policies = {row["policy_id"]: row for row in sources["policies.json"]}
        customers = {row["customer_id"] for row in sources["customers.json"]}
        plans = {row["plan"] for row in sources["plans.json"]["plans"]}
        add_ons = {row["add_on"] for row in sources["plans.json"]["add_ons"]}
        for claim in sources["claims.json"]:
            policy = policies.get(claim["policy_id"])
            if policy is None or claim["customer_id"] not in customers:
                raise ValueError(f"Missing policy or customer for {claim['claim_id']}.")
            if policy["customer_id"] != claim["customer_id"]:
                raise ValueError(f"Policy/customer mismatch for {claim['claim_id']}.")
            if policy["plan"] not in plans or not set(policy["add_ons"]) <= add_ons:
                raise ValueError(f"Unknown plan or add-on for {claim['claim_id']}.")

    @staticmethod
    def _insert(connection: Connection, kind: str, key: str, body: Any):
        connection.execute(
            "INSERT INTO records (kind, id, body) VALUES (?, ?, ?)",
            (kind, key, json.dumps(encode(body))),
        )

    def record(self, kind: str, key: str) -> Any:
        """Read one seed record without touching business-data files."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT body FROM records WHERE kind = ? AND id = ?", (kind, key)
            ).fetchone()
        if row is None:
            raise KeyError(key)
        return decode(json.loads(row["body"]))

    def records(self, kind: str) -> list[Any]:
        """Read claims in queue order and other records in identifier order."""
        with self.connect() as connection:
            if kind == "claim":
                rows = connection.execute(
                    "SELECT r.body FROM records r JOIN claims c ON c.id=r.id "
                    "WHERE r.kind='claim' ORDER BY c.queue_position"
                ).fetchall()
                return [decode(json.loads(row["body"])) for row in rows]
            rows = connection.execute(
                "SELECT body FROM records WHERE kind = ? ORDER BY id", (kind,)
            ).fetchall()
        return [decode(json.loads(row["body"])) for row in rows]
