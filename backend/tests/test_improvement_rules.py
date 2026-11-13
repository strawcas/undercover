"""Hand-calculated absolute quality and relative efficiency comparisons."""

import pytest

from backend.database import engine
from backend.improvement_rules import verdict
from backend.settings import get_settings
from backend.tests.improvement_support import scores


@pytest.mark.parametrize(
    ("before", "after", "kept", "reason"),
    [
        (scores(), scores(0.81), True, "accuracy"),
        (scores(), scores(0.8), False, "No measure"),
        (scores(), scores(0.5, 2), False, "floor"),
        (scores(), scores(0.9, 0.9), False, "floor"),
        (scores(), scores(0.81, 1.5, 0.00201), False, "cost_per_claim"),
        (scores(), scores(0.81, 1.5, 0.002, 12.5), True, "accuracy"),
        (scores(), scores(0.81, 1.5, 0.001, 12.51), False, "seconds_per_claim"),
        (scores(), scores(0.7, 1.65), False, "No measure"),
        (scores(), scores(0.7, 1.651), True, "grade"),
        (scores(cost=0), scores(0.9, cost=0.00001), False, "cost_per_claim"),
        (scores(cost=0, seconds=0), scores(0.9, cost=0, seconds=0), True, "accuracy"),
        (scores(), scores(0.9, grade=None), False, "unavailable"),
        (scores(cost=None), scores(0.9), False, "unavailable"),
        (None, scores(), False, "incomplete"),
    ],
)
def test_backend_verdict(before, after, kept, reason):
    with engine().connect() as db:
        result, explanation = verdict(before, after, get_settings(db))
    assert result is kept
    assert reason in explanation
