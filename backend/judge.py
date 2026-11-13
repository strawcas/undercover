"""The single OpenRouter Decisions boundary for reference-guided grading."""

import logging
import os
from typing import Any

from openrouter import OpenRouter
from openrouter.components import DecisionsNoulAnswer, DecisionsScoreAnswer

from backend.judge_models import Grade

JEV = "typesafe/jev-1.13"
ORIGIN = os.getenv("OPENROUTER_DECISIONS_URL", "https://openrouter.ai")
GRADES = ("F", "C", "B", "A")
RUBRIC = {
    "F": (
        "different grounds from the handler's reason, or it contradicts "
        "the handler's reason"
    ),
    "C": (
        "partly the same as the handler's reason: one of the handler's grounds "
        "or the section is missing, or it adds a ground the handler did not give"
    ),
    "B": (
        "the same grounds and the same section of the policy wording "
        "as the handler's reason"
    ),
    "A": (
        "the same grounds and section as the handler's reason, and clearly better "
        "expressed: clearer for the customer, with the section and the figures, "
        "shorter, and no jargon"
    ),
}
QUESTIONS: dict[str, Any] = {
    "grade": {
        "type": "score",
        "instructions": (
            "An expert claims handler decided a travel insurance claim and gave "
            "`handler.reason`, resting on `handler.reference`. Grade how well "
            "`agent.rationale` explains the same decision."
        ),
        "criteria": [f"{letter}: {text}" for letter, text in RUBRIC.items()],
    },
    "same_grounds": {
        "type": "noul",
        "instructions": (
            "`agent.rationale` gives the same main grounds for the decision "
            "as `handler.reason`"
        ),
    },
    "same_section": {
        "type": "noul",
        "instructions": (
            "`agent.rationale` refers to the same section of the policy wording "
            "as `handler.reference`"
        ),
    },
}
client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY"), timeout_ms=10_000)
log = logging.getLogger(__name__)


def grade(state: dict[str, Any]) -> Grade:
    """Grade one explanation, retaining unavailable results as ungraded."""
    cost = None
    try:
        response = client.alpha.decisions.create(
            model=JEV,
            state=state,
            questions=QUESTIONS,
            server_url=ORIGIN,
            timeout_ms=10_000,
            retries=None,
        )
        cost = response.usage.cost
        scored = response.answers["grade"]
        grounds = response.answers["same_grounds"]
        section = response.answers["same_section"]
        if not isinstance(scored, DecisionsScoreAnswer) or not scored.probabilities:
            raise ValueError("The judge returned no grade probabilities")
        if not isinstance(grounds, DecisionsNoulAnswer) or not isinstance(
            section, DecisionsNoulAnswer
        ):
            raise ValueError("The judge returned no rationale checks")
        most_likely = max(scored.probabilities, key=scored.probabilities.__getitem__)
        return Grade(
            grade=scored.score,
            letter=GRADES[int(most_likely)],
            same_grounds=grounds.noul,
            same_section=section.noul,
            judge_cost=cost,
        )
    except Exception as error:  # one unavailable judge must not lose the evaluation
        log.warning("Explanation not graded: %s", type(error).__name__)
        return Grade(judge_cost=cost)
