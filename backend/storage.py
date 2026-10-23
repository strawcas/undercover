"""SQLite records for the local claims pilot."""

import json
import sqlite3
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.models import Claim, Company, Customer, Handler, Policy

ROOT = Path(__file__).resolve().parents[1]
SEED_FILES = (
    "claims.json",
    "policies.json",
    "customers.json",
    "plans.json",
    "handlers.json",
    "company.json",
    "policy_wording.md",
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
                "payout_cents INTEGER, sign_off TEXT, payment TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "id INTEGER PRIMARY KEY, claim_id TEXT NOT NULL, "
                "thread_id TEXT NOT NULL, action TEXT NOT NULL, "
                "status TEXT NOT NULL, detail TEXT NOT NULL, "
                "created_at TEXT NOT NULL)"
            )
            if connection.execute("SELECT 1 FROM records LIMIT 1").fetchone():
                return
            self._seed(connection, data_dir)

    def _seed(self, connection: sqlite3.Connection, data_dir: Path) -> None:
        sources: dict[str, Any] = {}
        for name in SEED_FILES:
            content = (data_dir / name).read_text(encoding="utf-8")
            sources[name] = (
                json.loads(content, parse_float=Decimal)
                if name.endswith(".json")
                else content
            )
        claims = sources["claims.json"]
        policy_ids = {claim["policy_id"] for claim in claims}
        customer_ids = {claim["customer_id"] for claim in claims}
        for claim in claims:
            claim["status"] = "waiting"
            Claim.model_validate(claim)
            self._insert(connection, "claim", claim["claim_id"], claim)
            connection.execute(
                "INSERT INTO claims (id, state) VALUES (?, 'waiting')",
                (claim["claim_id"],),
            )
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
        self._insert(
            connection,
            "settings",
            "instructions",
            (ROOT / "backend" / "instructions.md").read_text(encoding="utf-8"),
        )

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
        """Read all records of one kind in identifier order."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT body FROM records WHERE kind = ? ORDER BY id", (kind,)
            ).fetchall()
        return [decode(json.loads(row["body"])) for row in rows]
