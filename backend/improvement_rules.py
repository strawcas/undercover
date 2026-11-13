"""The lab's pure keep/rollback rule, owned by the backend."""

from math import isclose

from backend.models import Scores
from backend.settings_models import PlatformSettings

RULE_DESCRIPTION = (
    "Keep only a fully evaluated candidate meeting both accuracy and grade floors, "
    "with at least one measure improving strictly beyond noise and no measure "
    "regressing beyond tolerance. Accuracy and grade use absolute differences; "
    "cost and time use fractions of the baseline. Missing required measures, "
    "including grade, or stopped/unaffordable testing mean rollback."
)

MEASURES = {
    "accuracy": "accuracy",
    "grade": "grade",
    "cost_per_claim": "model_cost_per_claim",
    "seconds_per_claim": "seconds_per_claim",
}


def verdict(
    before: Scores | None, after: Scores | None, settings: PlatformSettings
) -> tuple[bool, str]:
    """Require floors, one strict improvement and no excessive regression."""
    if before is None or after is None:
        return False, "Whole-set evaluations are incomplete"
    if any(
        getattr(scores, field) is None
        for scores in (before, after)
        for field in MEASURES.values()
    ):
        return (
            False,
            "A required measure is unavailable; ungraded versions cannot be kept",
        )
    if after.grade is None:
        return False, "A version without a grade cannot be kept"
    if (
        after.accuracy < settings.objectives.accuracy
        or after.grade < settings.objectives.grade
    ):
        return False, "The candidate missed an accuracy or grade floor"
    improved = []
    for name, field in MEASURES.items():
        old, new = getattr(before, field), getattr(after, field)
        relative = name in ("cost_per_claim", "seconds_per_claim")
        gain = old - new if relative else new - old
        scale = old if relative else 1
        tolerance = getattr(settings.tolerance, name) * scale
        noise = getattr(settings.noise, name) * scale
        if gain < -tolerance and not isclose(gain, -tolerance, abs_tol=1e-12):
            return False, f"{name} regressed beyond tolerance"
        if gain > noise and not isclose(gain, noise, abs_tol=1e-12):
            improved.append(name)
    return (
        (True, "Improved beyond noise: " + ", ".join(improved))
        if improved
        else (False, "No measure improved beyond noise")
    )
