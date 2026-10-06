"""Provider-independent structured response contracts."""

from pydantic import BaseModel, Field


class GroundedModelOutput(BaseModel):
    answer: str = Field(min_length=1, max_length=12000)
    citations: list[str] = Field(default_factory=list, max_length=8)
    insufficient_evidence: bool = False
    conflict_detected: bool = False


class GeneralModelOutput(BaseModel):
    answer: str = Field(min_length=1, max_length=12000)
