"""Provider-independent bounded internal document evidence capability."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from office_assistant.internal_evidence.models import DocumentEvidence
from office_assistant.internal_evidence.query_normalization import (
    retrieval_query,
    strong_identifiers,
)
from office_assistant.knowledge.models import RetrievalFilter, RetrievedChunk
from office_assistant.knowledge.retrieval import KnowledgeRetrievalService


class DocumentSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=10)
    file_id: int | None = None


@dataclass(frozen=True, slots=True)
class DocumentRelevancePolicy:
    """Conservative acceptance rules for Qdrant's normalized hybrid RRF scores."""

    min_hybrid_score: float = 0.55
    min_identifier_score: float = 0.30

    def accepts(self, query: str, chunk: RetrievedChunk) -> bool:
        query_ids = set(strong_identifiers(query))
        if query_ids:
            chunk_ids = set(strong_identifiers(chunk.text))
            return bool(query_ids & chunk_ids) and chunk.score >= self.min_identifier_score
        return chunk.score >= self.min_hybrid_score


class InternalDocumentSearch:
    name = "search_internal_documents"
    description = "Search internal company documents for relevant evidence and provenance."

    def __init__(
        self,
        retrieval: KnowledgeRetrievalService | None,
        policy: DocumentRelevancePolicy | None = None,
    ) -> None:
        self.retrieval = retrieval
        self.policy = policy or DocumentRelevancePolicy()

    async def search(self, value: DocumentSearchInput) -> list[DocumentEvidence]:
        if self.retrieval is None:
            raise RuntimeError("Internal document search is unavailable")
        chunks = await self.retrieval.search_internal_knowledge(
            retrieval_query(value.query),
            mode="hybrid",
            filters=RetrievalFilter(file_id=value.file_id) if value.file_id else None,
            limit=value.limit,
        )
        accepted = [item for item in chunks if self.policy.accepts(value.query, item)]
        return [
            DocumentEvidence(
                chunk_id=item.chunk_id,
                text=item.text,
                file_id=item.source_file_id,
                filename=item.source_filename,
                locator=item.locator,
                retrieval_method=item.retrieval_method,
                score=item.score,
            )
            for item in accepted
        ]
