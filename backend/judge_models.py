"""Saved explanation grades and their separate model cost."""

from typing import Literal

from pydantic import BaseModel, Field


class Grade(BaseModel):
    grade: float | None = Field(default=None, ge=0, le=3)
    letter: Literal["F", "C", "B", "A"] | None = None
    same_grounds: float | None = Field(default=None, ge=0, le=1)
    same_section: float | None = Field(default=None, ge=0, le=1)
    judge_cost: float | None = Field(default=None, ge=0)


def undecided() -> Grade:
    """Give an undecided closed claim F without making a judge request."""
    return Grade(grade=0, letter="F", same_grounds=0, same_section=0, judge_cost=0)
