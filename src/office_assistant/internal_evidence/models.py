"""Typed plans, evidence, conflicts, and unified citations."""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from office_assistant.ai.provider import TokenUsage
from office_assistant.business_tools.models import BusinessSource


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoutingCategory(StrEnum):
    GENERAL_CHAT = "GENERAL_CHAT"
    INTERNAL_STRUCTURED = "INTERNAL_STRUCTURED"
    INTERNAL_DOCUMENT = "INTERNAL_DOCUMENT"
    INTERNAL_HYBRID = "INTERNAL_HYBRID"
    FILE_DELIVERY = "FILE_DELIVERY"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"


class PlannedToolCall(StrictModel):
    tool_name: str = Field(min_length=1, max_length=100)
    arguments_json: str = Field(default="{}", max_length=4000)

    def parsed_arguments(self) -> dict[str, object]:
        try:
            value: object = json.loads(self.arguments_json)
        except json.JSONDecodeError as error:
            raise ValueError("Tool arguments must be valid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("Tool arguments must be a JSON object")
        raw = cast(dict[object, object], value)
        return {str(key): item for key, item in raw.items()}


def _empty_tool_calls() -> list[PlannedToolCall]:
    return []


def _empty_business_sources() -> list[BusinessSource]:
    return []


class EvidencePlan(StrictModel):
    category: RoutingCategory
    intent: str = Field(max_length=100)
    use_structured_tools: bool
    use_document_search: bool
    allow_general_knowledge: bool = False
    business_tool_calls: list[PlannedToolCall] = Field(
        default_factory=_empty_tool_calls, max_length=4
    )
    requires_clarification: bool = False
    reason_code: str = Field(max_length=100)


class StructuredEvidence(StrictModel):
    tool_name: str
    status: str
    data: object | None = None
    warnings: list[str] = Field(default_factory=list)
    sources: list[BusinessSource] = Field(default_factory=_empty_business_sources)


class DocumentEvidence(StrictModel):
    chunk_id: str
    text: str
    file_id: int
    filename: str
    locator: dict[str, object]
    retrieval_method: str
    score: float


class ImageReading(StrictModel):
    """Text a vision model read off a picture the user supplied for this question.

    Untrusted content, never company state: it has no file id, no committed blob and no path into
    structured extraction (ADR 0017 decision 5, OPEN-QUESTIONS Q20).
    """

    filename: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=8000)
    description: str = Field(default="", max_length=1000)
    model: str = Field(default="", max_length=100)


class EvidenceConflict(StrictModel):
    conflict_type: str
    summary: str
    evidence_ids: list[str]


class UnifiedCitation(StrictModel):
    evidence_id: str
    number: int
    source_class: str
    filename: str | None = None
    file_id: int | None = None
    locator: dict[str, object] | None = None
    label: str
    record_identity: str | None = Field(default=None, exclude=True)
    origin_type: str | None = Field(default=None, exclude=True)


def _empty_structured_evidence() -> list[StructuredEvidence]:
    return []


def _empty_document_evidence() -> list[DocumentEvidence]:
    return []


def _empty_conflicts() -> list[EvidenceConflict]:
    return []


def _empty_citations() -> list[UnifiedCitation]:
    return []


def _empty_image_readings() -> list[ImageReading]:
    return []


class EvidenceBundle(StrictModel):
    structured_results: list[StructuredEvidence] = Field(default_factory=_empty_structured_evidence)
    document_results: list[DocumentEvidence] = Field(default_factory=_empty_document_evidence)
    image_results: list[ImageReading] = Field(default_factory=_empty_image_readings)
    warnings: list[str] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=_empty_conflicts)
    citations: list[UnifiedCitation] = Field(default_factory=_empty_citations)
    evidence_payloads: dict[str, str] = Field(default_factory=dict, exclude=True)


@dataclass(frozen=True, slots=True)
class InternalAnswer:
    answer: str
    route: RoutingCategory
    citations: tuple[UnifiedCitation, ...] = ()
    conflicts: tuple[EvidenceConflict, ...] = ()
    insufficient_evidence: bool = False
    model: str | None = None
    router_usage: TokenUsage = TokenUsage()
    synthesis_usage: TokenUsage = TokenUsage()
    tool_calls: int = 0
