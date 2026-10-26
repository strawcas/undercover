"""SQLite records for the local claims pilot."""

import json
import sqlite3
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.harness_storage import migrate
from backend.models import Claim, Company, Customer, Handler, Policy
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
    """Open short-lived connections to one SQLite database."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            yield connection

    @contextmanager
    def transaction(self):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def initialize(self, data_dir: Path = ROOT / "data") -> None:
        """Create storage and seed the explicit pilot file allowlist once."""
        with self.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS records "
                "(kind TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY (kind, id))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS claims ("
                "id TEXT PRIMARY KEY, state TEXT NOT NULL, thread_id TEXT, "
                "active INTEGER NOT NULL DEFAULT 0, decision TEXT, "
                "payout_cents INTEGER, sign_off TEXT, payment TEXT, "
                "queue_position INTEGER)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "id INTEGER PRIMARY KEY, claim_id TEXT NOT NULL, "
                "thread_id TEXT NOT NULL, action TEXT NOT NULL, "
                "status TEXT NOT NULL, detail TEXT NOT NULL, "
                "created_at TEXT NOT NULL)"
            )
            self._migrate_positions(connection)
            migrate(connection)
            if not connection.execute("SELECT 1 FROM records LIMIT 1").fetchone():
                self._seed(connection, data_dir)
            self._harness_documents(connection, data_dir)

    def _harness_documents(
        self, connection: sqlite3.Connection, data_dir: Path
    ) -> None:
        for key, path in (
            ("instructions", ROOT / "backend/instructions.md"),
            ("queue_instructions", ROOT / "backend/queue_instructions.md"),
        ):
            connection.execute(
                "INSERT OR REPLACE INTO records VALUES ('settings', ?, ?)",
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
    def _migrate_positions(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(claims)")
        }
        if "queue_position" not in columns:
            connection.execute("ALTER TABLE claims ADD COLUMN queue_position INTEGER")
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

    def _enqueue(self, connection: sqlite3.Connection, claim: dict[str, Any]) -> None:
        record = Claim.model_validate(claim | {"status": "waiting"}).model_dump()
        self._insert(connection, "claim", record["claim_id"], record)
        connection.execute(
            "INSERT INTO claims (id, state, queue_position) "
            "SELECT ?, 'waiting', COALESCE(MAX(queue_position), 0) + 1 FROM claims",
            (record["claim_id"],),
        )

    def _seed(self, connection: sqlite3.Connection, data_dir: Path) -> None:
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
            (ROOT / "backend" / "instructions.md").read_text(encoding="utf-8"),
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
    def _insert(connection: sqlite3.Connection, kind: str, key: str, body: Any):
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
