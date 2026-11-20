"""Provide read-only access to the loaded UnderCover governance files."""

import re
from typing import Annotated, Any

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import StringConstraints

from context.catalog import (
    PLANS_FILE,
    TITLES,
    DocumentName,
    PlanName,
    RecordName,
    read_document,
    read_yaml,
)

Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Phrase = Annotated[str, StringConstraints(min_length=1)]

GLOSSARY_SOURCE, GLOSSARY = read_yaml("glossary.yaml")
DICTIONARY_SOURCE, DICTIONARY = read_yaml("data_dictionary.yaml")
PLAN_SOURCE, PLANS = read_yaml(PLANS_FILE)
RULE_SOURCE, RULES = read_yaml("claim_rules.yaml")
REFERENCE_SOURCE, REFERENCE = read_yaml("reference_data.yaml")
DOCUMENTS = {name: read_document(name) for name in TITLES}


def lookup_term(term: Nonempty) -> dict[str, Any]:
    """Find a policy term's definition and the sections it comes from."""
    terms = GLOSSARY["terms"]
    for item in terms:
        if item["term"].casefold() == term.casefold():
            return {**item, "source": GLOSSARY_SOURCE}
    return {"known_terms": [item["term"] for item in terms], "source": GLOSSARY_SOURCE}


def describe_fields(record: RecordName) -> dict[str, Any]:
    """Describe the fields of a claim, policy or previous claim."""
    entry = next(item for item in DICTIONARY["records"] if item["record"] == record)
    return {**entry, "source": DICTIONARY_SOURCE}


def get_plan(plan: PlanName) -> dict[str, Any]:
    """Read one plan's limits, waiting periods, deductible and add-ons."""
    entry = next(item for item in PLANS["plans"] if item["plan"] == plan)
    return {
        **entry,
        "deductible_applies_to": PLANS["deductible_applies_to"],
        "add_ons": PLANS["add_ons"],
        "source": PLAN_SOURCE,
    }


def get_claim_rules() -> dict[str, Any]:
    """Read the rules claims operations applies when handling a claim."""
    return {**RULES, "source": RULE_SOURCE}


def search_reference_data(query: Nonempty) -> dict[str, Any]:
    """Find outside facts containing every word of a query."""
    words = query.casefold().split()
    matches = [
        {"topic": topic["topic"], "fact": fact}
        for topic in REFERENCE["topics"]
        for fact in topic["facts"]
        if all(word in fact.casefold() for word in words)
    ]
    return {"matches": matches, "source": REFERENCE_SOURCE}


def search_documents(query: Phrase) -> dict[str, Any]:
    """Find sections of the wording and manual containing a word or phrase."""
    wanted = query.casefold()
    matches = []
    for document in DOCUMENTS.values():
        for section in document.sections:
            paragraphs = re.split(r"\n\s*\n", section["text"])
            excerpts = [
                paragraph for paragraph in paragraphs if wanted in paragraph.casefold()
            ]
            if excerpts or wanted in section["title"].casefold():
                matches.append(
                    {
                        "document": section["document"],
                        "number": section["number"],
                        "title": section["title"],
                        "excerpts": excerpts,
                        "source": section["source"],
                    }
                )
    return {"matches": matches, "sources": [doc.source for doc in DOCUMENTS.values()]}


def read_section(document: DocumentName, number: int) -> dict[str, Any]:
    """Read one numbered section of the wording or claims manual."""
    entry = DOCUMENTS[document]
    for section in entry.sections:
        if section["number"] == number:
            return dict(section)
    raise ToolError(f"Section {number} of the {entry.title.lower()} not found")


TOOLS = (
    lookup_term,
    describe_fields,
    get_plan,
    get_claim_rules,
    search_reference_data,
    search_documents,
    read_section,
)
