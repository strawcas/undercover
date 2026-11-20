"""Read the governance sources and index their numbered document sections."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

import yaml

ROOT = Path(__file__).resolve().parent
PLANS_FILE = "plans.yaml"
DocumentName = Literal["policy_wording", "claims_manual"]
RecordName = Literal["claim", "policy", "previous_claim"]
PlanName = Literal["economy", "premium", "first"]
TITLES: dict[DocumentName, str] = {
    "policy_wording": "Policy wording",
    "claims_manual": "Claims manual",
}
HEADING = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)


class Source(TypedDict):
    file: str
    system: str
    owner: str
    reviewed: str


class Section(TypedDict):
    document: DocumentName
    document_title: str
    number: int
    title: str
    text: str
    source: Source


@dataclass(frozen=True)
class Document:
    name: DocumentName
    title: str
    text: str
    version: str
    source: Source
    sections: tuple[Section, ...]

    def contents(self) -> str:
        """List the numbered headings for the claim configuration."""
        lines = [
            f"{section['number']}. {section['title']}" for section in self.sections
        ]
        return f"## Contents of the {self.title.lower()}\n\n" + "\n".join(lines)


def read_yaml(filename: str) -> tuple[Source, dict[str, Any]]:
    """Read a YAML file and its provenance."""
    data = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
    return source_of(filename, data.pop("source")), data


def source_of(filename: str, metadata: dict[str, Any]) -> Source:
    """Name a governance file and the system responsible for it."""
    return Source(
        file=filename,
        system=metadata["system"],
        owner=metadata["owner"],
        reviewed=metadata["reviewed"],
    )


def read_document(name: DocumentName) -> Document:
    """Read a document's front matter and prepare its sections."""
    filename, title = f"{name}.md", TITLES[name]
    _, front, body = (ROOT / filename).read_text(encoding="utf-8").split("---", 2)
    metadata = yaml.safe_load(front)
    source = source_of(filename, metadata["source"])
    text = body.strip() + "\n"
    headings = list(HEADING.finditer(text))
    ends = [heading.start() for heading in headings[1:]] + [len(text)]
    sections: tuple[Section, ...] = tuple(
        {
            "document": name,
            "document_title": title,
            "number": int(heading[1]),
            "title": heading[2],
            "text": text[heading.end() : end].strip(),
            "source": source,
        }
        for heading, end in zip(headings, ends, strict=True)
    )
    return Document(name, title, text, str(metadata["version"]), source, sections)
