"""Provider-independent structured response contracts."""

from pydantic import BaseModel, Field


class GroundedModelOutput(BaseModel):
    answer: str = Field(min_length=1, max_length=12000)
    citations: list[str] = Field(default_factory=list, max_length=8)
    insufficient_evidence: bool = False
    conflict_detected: bool = False


class GeneralModelOutput(BaseModel):
    answer: str = Field(min_length=1, max_length=12000)


class ImageTranscriptionOutput(BaseModel):
    """What an image shows: text copied verbatim, plus a description so a text-less
    photo still yields content instead of an empty result."""

    transcription: str = Field(default="", max_length=8000)
    description: str = Field(min_length=1, max_length=1000)
