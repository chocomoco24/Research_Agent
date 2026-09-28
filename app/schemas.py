"""Pydantic models: validate incoming JSON and shape outgoing JSON."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunCreate(BaseModel):
    goal: str = Field(min_length=5, max_length=1000, examples=["What changed in Python 3.13? Top 5 points."])

    @field_validator("goal")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if len(v.strip()) < 5:
            raise ValueError("goal must contain at least 5 non-space characters")
        return v.strip()


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # allow building from SQLAlchemy objects

    step_number: int
    type: str
    content: Any
    created_at: datetime


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    goal: str
    status: str
    input_tokens: int
    output_tokens: int
    created_at: datetime


class RunDetail(RunSummary):
    final_answer: str | None
    steps: list[StepOut]


class RunCreated(BaseModel):
    id: int
    status: str
