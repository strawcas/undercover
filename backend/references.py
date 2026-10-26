"""Backend-only section search, reads and decision provenance."""

import json
import re
import sqlite3
from uuid import uuid4

from backend.attempt_guard import require_attempt
from backend.claim_records import append_event, claim_row, now
from backend.errors import ClaimError
from backend.models import DecisionInput, EventInput
from backend.reference_models import ReadInput, SearchInput
from backend.storage import Store

SEARCH_LIMIT = 10
SNIPPET_LENGTH = 240


def find_section(
    connection: sqlite3.Connection, source: str, section_id: str
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM reference_sections WHERE source=? AND section_id=?",
        (source, section_id),
    ).fetchone()
    if row is None:
        raise ClaimError(404, "Reference section not found.")
    return row


def decision_versions(
    connection: sqlite3.Connection, claim_id: str, decision: DecisionInput
) -> dict:
    versions = {}
    for source, ids, key in (
        ("policy_wording", decision.policy_sections, "policy_wording_version"),
        ("claims_manual", decision.manual_sections, "claims_manual_version"),
    ):
        for section_id in ids:
            section = find_section(connection, source, section_id)
            read = connection.execute(
                "SELECT 1 FROM evidence WHERE claim_id=? AND thread_id=? "
                "AND source=? AND version=? AND section_id=?",
                (claim_id, decision.thread_id, source, section["version"], section_id),
            ).fetchone()
            if read is None:
                raise ClaimError(
                    409, "Decision references must be read by the active attempt."
                )
            versions[key] = section["version"]
    return versions


class References:
    """Use the stored allowlisted corpus, never a client-supplied file path."""

    def __init__(self, store: Store):
        self.store = store

    def contents(self, source: str) -> dict:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reference_sections WHERE source=? ORDER BY position",
                (source,),
            ).fetchall()
        return dict(
            source=source,
            version=rows[0]["version"],
            sections=[
                {key: row[key] for key in ("section_id", "title", "parent_id")}
                for row in rows
            ],
        )

    def search(self, claim_id: str, request: SearchInput) -> dict:
        thread = str(request.thread_id)
        terms = set(re.findall(r"\w+", request.query.casefold()))
        with self.store.transaction() as connection:
            require_attempt(connection, claim_row(connection, claim_id), thread)
            rows = connection.execute(
                "SELECT * FROM reference_sections WHERE source=? ORDER BY position",
                (request.source,),
            ).fetchall()
            scored = [
                (sum(term in row["text"].casefold() for term in terms), index, row)
                for index, row in enumerate(rows)
            ]
            scored.sort(key=lambda item: (-item[0], item[1]))
            matches = [
                dict(
                    section_id=row["section_id"],
                    title=row["title"],
                    snippet=row["text"][:SNIPPET_LENGTH],
                )
                for score, _, row in scored
                if score
            ][:SEARCH_LIMIT]
            detail = f"{request.source}: {request.query}. Matches: " + ", ".join(
                item["section_id"] for item in matches
            )
            append_event(
                connection,
                claim_id,
                EventInput(
                    thread_id=thread,
                    action="Search reference sections",
                    status="completed",
                    detail=detail,
                ),
            )
        return dict(
            claim_id=claim_id,
            thread_id=thread,
            source=request.source,
            version=rows[0]["version"],
            query=request.query,
            matches=matches,
        )

    def read(self, claim_id: str, request: ReadInput) -> tuple[dict, bool]:
        thread = str(request.thread_id)
        with self.store.transaction() as connection:
            entry = require_attempt(connection, claim_row(connection, claim_id), thread)
            section = find_section(connection, request.source, request.section_id)
            old = connection.execute(
                "SELECT body FROM evidence WHERE claim_id=? AND thread_id=? "
                "AND source=? "
                "AND version=? AND section_id=?",
                (
                    claim_id,
                    thread,
                    request.source,
                    section["version"],
                    request.section_id,
                ),
            ).fetchone()
            body = (
                json.loads(old["body"])
                if old
                else dict(
                    evidence_id=str(uuid4()),
                    run_id=entry["run_id"],
                    claim_id=claim_id,
                    thread_id=thread,
                    source=request.source,
                    version=section["version"],
                    section_id=request.section_id,
                    title=section["title"],
                    text=section["text"],
                    read_at=now(),
                )
            )
            if old is None:
                connection.execute(
                    "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        body["evidence_id"],
                        claim_id,
                        thread,
                        request.source,
                        section["version"],
                        request.section_id,
                        json.dumps(body),
                    ),
                )
            append_event(
                connection,
                claim_id,
                EventInput(
                    thread_id=thread,
                    action="Read reference section",
                    status="completed",
                    detail=f"{request.source} {section['version']} "
                    f"section {request.section_id}: "
                    f"{section['title']}. Evidence {body['evidence_id']}.",
                ),
            )
            return body, old is None

    def evidence(self, claim_id: str) -> dict:
        with self.store.connect() as connection:
            claim_row(connection, claim_id)
            reads = [
                json.loads(row["body"])
                for row in connection.execute(
                    "SELECT body FROM evidence WHERE claim_id=? ORDER BY rowid",
                    (claim_id,),
                )
            ]
        return dict(claim_id=claim_id, reads=reads)
