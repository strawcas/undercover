"""A section index for the two permitted business documents."""

import re
import sqlite3


def sections(text: str) -> list[dict]:
    """Split numbered headings and named notes without losing source text."""
    version = re.search(r"Version (\d+\.\d+)", text)
    headings = list(re.finditer(r"^## (\d+)\. (.+)$", text, re.MULTILINE))
    if version is None or not headings:
        raise ValueError("Reference documents require a version and numbered sections.")
    result = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[heading.start() : end].rstrip()
        parent = heading[1]
        result.append(
            dict(
                section_id=parent,
                title=heading[2],
                parent_id=None,
                version=version[1],
                text=body,
            )
        )
        notes = list(re.finditer(r"^\*\*(.+?)\.\*\*", body, re.MULTILINE))
        for position, note in enumerate(notes):
            stop = (
                notes[position + 1].start() if position + 1 < len(notes) else len(body)
            )
            suffix = re.sub(r"[^a-z0-9]+", "-", note[1].lower()).strip("-")
            result.append(
                dict(
                    section_id=f"{parent}.{suffix}",
                    title=note[1],
                    parent_id=parent,
                    version=version[1],
                    text=body[note.start() : stop].rstrip(),
                )
            )
    return result


def install(connection: sqlite3.Connection, source: str, text: str) -> None:
    """Replace one document index after validating its complete text."""
    entries = sections(text)
    connection.execute("DELETE FROM reference_sections WHERE source=?", (source,))
    for position, entry in enumerate(entries):
        connection.execute(
            "INSERT INTO reference_sections VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                source,
                entry["section_id"],
                position,
                entry["parent_id"],
                entry["title"],
                entry["version"],
                entry["text"],
            ),
        )
