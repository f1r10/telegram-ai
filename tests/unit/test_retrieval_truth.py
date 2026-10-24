"""Internal retrieval either answers from evidence or says it could not look."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from httpx import Headers
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.ai.provider import LanguageModel, ModelResponse, TokenUsage
from office_assistant.ai.schemas import GroundedModelOutput
from office_assistant.ai.service import (
    GroundedAnswerService,
    RetrievalIndexNotInitialized,
    RetrievalUnavailable,
)
from office_assistant.business_tools.models import BusinessSource, ToolResult, ToolStatus
from office_assistant.business_tools.registry import BusinessToolRegistry
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.integrations.telegram.app import (
    _preflight_knowledge,  # pyright: ignore[reportPrivateUsage]
)
from office_assistant.internal_evidence.document_search import InternalDocumentSearch
from office_assistant.internal_evidence.models import (
    DocumentEvidence,
    EvidencePlan,
    PlannedToolCall,
    RoutingCategory,
    UnifiedCitation,
)
from office_assistant.internal_evidence.router import EvidencePlanner
from office_assistant.internal_evidence.service import (
    NOT_FOUND_INTERNAL_AZ,
    InternalEvidenceService,
)
from office_assistant.knowledge.embeddings import FastEmbedSparseAdapter, FastEmbedTextAdapter
from office_assistant.knowledge.qdrant_store import (
    QdrantKnowledgeStore,
    QdrantProjectionNotInitialized,
    QdrantUnavailable,
)
from office_assistant.knowledge.retrieval import KnowledgeRetrievalService

ALIAS = "document_chunks"
COLLECTION = "document_chunks_v1"

DOCUMENT_PLAN = EvidencePlan(
    category=RoutingCategory.INTERNAL_DOCUMENT,
    intent="WARRANTY_TERM",
    use_structured_tools=False,
    use_document_search=True,
    reason_code="test_document_only",
)


def _not_found(name: str) -> UnexpectedResponse:
    return UnexpectedResponse(404, "Not Found", f"{name} not found".encode(), Headers())


class _Client:
    """Stand-in for AsyncQdrantClient that 404s for names outside ``existing``."""

    def __init__(
        self,
        existing: set[str],
        *,
        get_error: Exception | None = None,
        read_error: Exception | None = None,
    ) -> None:
        self.existing = existing
        self.get_error = get_error
        self.read_error = read_error
        self.read_targets: list[str] = []
        self.delete_targets: list[str] = []

    async def get_collection(self, collection_name: str) -> object:
        if self.get_error is not None:
            raise self.get_error
        if collection_name not in self.existing:
            raise _not_found(collection_name)
        return SimpleNamespace()

    async def query_points(self, collection_name: str, **kwargs: object) -> object:
        self.read_targets.append(collection_name)
        if self.read_error is not None:
            raise self.read_error
        return SimpleNamespace(points=[])

    async def delete(self, collection_name: str, **kwargs: object) -> None:
        self.delete_targets.append(collection_name)


def _store(client: _Client) -> QdrantKnowledgeStore:
    store = QdrantKnowledgeStore.__new__(QdrantKnowledgeStore)
    store.client = cast(AsyncQdrantClient, client)
    store.alias = ALIAS
    store.collection = COLLECTION
    store.timeout = 1
    return store


def test_reads_target_the_alias_the_worker_published() -> None:
    store = _store(_Client({ALIAS, COLLECTION}))
    assert asyncio.run(store.resolve_target()) == ALIAS


def test_reads_fall_back_to_the_physical_collection_when_the_alias_is_missing() -> None:
    client = _Client({COLLECTION})
    asyncio.run(_store(client).search_hybrid([1.0, 0.0, 0.0], ([1], [1.0]), limit=3))
    assert client.read_targets == [COLLECTION]


def test_missing_projection_fails_loud_instead_of_reporting_absence() -> None:
    store = _store(_Client(set()))
    with pytest.raises(QdrantUnavailable):
        asyncio.run(store.resolve_target())
    with pytest.raises(QdrantUnavailable):
        asyncio.run(store.search_dense([1.0, 0.0, 0.0], limit=3))


def test_fresh_index_is_distinguishable_from_unreachable_service() -> None:
    store = _store(_Client(set()))
    with pytest.raises(QdrantProjectionNotInitialized):
        asyncio.run(store.resolve_target())
    with pytest.raises(QdrantProjectionNotInitialized):
        asyncio.run(store.search_dense([1.0, 0.0, 0.0], limit=3))


def test_unreachable_qdrant_is_not_mistaken_for_an_empty_projection() -> None:
    store = _store(_Client(set(), get_error=ConnectionError("connection refused")))
    with pytest.raises(QdrantUnavailable) as failure:
        asyncio.run(store.resolve_target())
    assert "unreachable" in str(failure.value)


def test_read_failure_is_raised_instead_of_returning_no_matches() -> None:
    store = _store(
        _Client(
            {ALIAS},
            read_error=UnexpectedResponse(500, "Internal Server Error", b"boom", Headers()),
        )
    )
    with pytest.raises(QdrantUnavailable):
        asyncio.run(store.search_sparse(([1], [1.0]), limit=3))


def test_delete_is_a_noop_while_no_projection_exists() -> None:
    client = _Client(set())
    asyncio.run(_store(client).delete_file(7))
    assert client.delete_targets == []


def test_delete_targets_the_projection_that_holds_the_points() -> None:
    client = _Client({COLLECTION})
    asyncio.run(_store(client).delete_file(7))
    assert client.delete_targets == [COLLECTION]


class _ModelLoader:
    def __init__(self) -> None:
        self.loaded = False

    def load(self) -> object:
        self.loaded = True
        return SimpleNamespace()


def test_preflight_warms_local_models_and_reports_the_projection() -> None:
    embedder, sparse = _ModelLoader(), _ModelLoader()
    retrieval = KnowledgeRetrievalService(
        _store(_Client({ALIAS})),
        cast(FastEmbedTextAdapter, embedder),
        cast(FastEmbedSparseAdapter, sparse),
    )
    assert asyncio.run(retrieval.preflight()) == ALIAS
    assert embedder.loaded and sparse.loaded


def test_startup_preflight_degrades_instead_of_fatalising_the_bot() -> None:
    retrieval = KnowledgeRetrievalService(
        _store(_Client(set())),
        cast(FastEmbedTextAdapter, _ModelLoader()),
        cast(FastEmbedSparseAdapter, _ModelLoader()),
    )
    assert asyncio.run(_preflight_knowledge(retrieval)) is None


class _Planner:
    def __init__(self, evidence_plan: EvidencePlan) -> None:
        self.evidence_plan = evidence_plan

    async def plan(self, user_id: int, question: str, **kwargs: object) -> object:
        return SimpleNamespace(plan=self.evidence_plan, response=None)


class _Registry:
    names = ("resolve_product_price",)

    def __init__(self, results: list[ToolResult[object]] | None = None) -> None:
        self.results = list(results or [])

    async def execute(self, name: str, context: object, payload: object) -> ToolResult[object]:
        if self.results:
            return self.results.pop(0)
        return ToolResult(status=ToolStatus.OK, data={})


class _FailingSearch:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, value: object) -> list[DocumentEvidence]:
        self.calls += 1
        raise QdrantUnavailable(f"Retrieval projection is missing | alias={ALIAS}")


class _FreshIndexSearch:
    async def search(self, value: object) -> list[DocumentEvidence]:
        raise QdrantProjectionNotInitialized(
            f"Retrieval projection is missing | alias={ALIAS}"
        )


class _EmptySearch:
    async def search(self, value: object) -> list[DocumentEvidence]:
        return []


class _SynthesisModel:
    async def generate(self, **kwargs: object) -> ModelResponse:
        return ModelResponse(
            parsed=GroundedModelOutput(
                answer="Biznes bazasında KA45 qiyməti 125 AZN-dir [E1].",
                citations=["E1"],
                conflict_detected=False,
            ),
            model="test-model",
            response_id="unit-response",
            usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
            latency_ms=1,
        )


class _Harness(InternalEvidenceService):
    async def _persist_turn(
        self,
        user_id: int,
        question: str,
        answer: str,
        citations: tuple[UnifiedCitation, ...] | list[UnifiedCitation],
    ) -> None:
        return None

    async def _save_usage(self, user_id: int, request_type: str, response: ModelResponse) -> None:
        return None


def _service(
    document_search: InternalDocumentSearch,
    plan: EvidencePlan,
    registry: BusinessToolRegistry,
    language_model: LanguageModel | None = None,
) -> _Harness:
    service = _Harness.__new__(_Harness)
    service.session_factory = cast(async_sessionmaker[AsyncSession], SimpleNamespace())
    service.registry = registry
    service.document_search = document_search
    service.planner = cast(EvidencePlanner, _Planner(plan))
    service.language_model = cast(LanguageModel, language_model or SimpleNamespace())
    service.general_service = cast(GroundedAnswerService, SimpleNamespace())
    service.document_limit = 5
    service.source_ttl = timedelta(minutes=60)
    service.source_context_messages = 6
    service.max_tool_calls = 4
    return service


def _actor() -> User:
    return User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)


def test_broken_index_never_claims_the_fact_is_absent() -> None:
    failing = _FailingSearch()
    service = _service(
        cast(InternalDocumentSearch, failing),
        DOCUMENT_PLAN,
        cast(BusinessToolRegistry, _Registry()),
    )
    with pytest.raises(RetrievalUnavailable):
        asyncio.run(service.answer(_actor(), "KA45 zəmanət müddəti nə qədərdir?"))
    assert failing.calls == 1


def test_uninitialized_projection_does_not_claim_absence_or_network_outage() -> None:
    service = _service(
        cast(InternalDocumentSearch, _FreshIndexSearch()),
        DOCUMENT_PLAN,
        cast(BusinessToolRegistry, _Registry()),
    )
    with pytest.raises(RetrievalIndexNotInitialized):
        asyncio.run(service.answer(_actor(), "QA-EMPTY-001 zəmanət müddəti nə qədərdir?"))


def test_healthy_index_without_matches_still_reports_absence() -> None:
    service = _service(
        cast(InternalDocumentSearch, _EmptySearch()),
        DOCUMENT_PLAN,
        cast(BusinessToolRegistry, _Registry()),
    )
    result = asyncio.run(service.answer(_actor(), "ZZ99 zəmanət müddəti nə qədərdir?"))
    assert result.answer == NOT_FOUND_INTERNAL_AZ
    assert result.insufficient_evidence


def test_structured_answer_survives_a_broken_document_index() -> None:
    hybrid_plan = EvidencePlan(
        category=RoutingCategory.INTERNAL_HYBRID,
        intent="PRODUCT_PRICE",
        use_structured_tools=True,
        use_document_search=True,
        business_tool_calls=[
            PlannedToolCall(tool_name="resolve_product_price", arguments_json='{"product":"KA45"}')
        ],
        reason_code="test_hybrid",
    )
    registry = _Registry(
        [
            ToolResult[object](
                status=ToolStatus.OK,
                data={"price": {"price_id": str(uuid4()), "amount": "125", "currency": "AZN"}},
                sources=[BusinessSource(source_type="FILE", file_id=10, filename="prices.xlsx")],
            )
        ]
    )
    service = _service(
        cast(InternalDocumentSearch, _FailingSearch()),
        hybrid_plan,
        cast(BusinessToolRegistry, registry),
        cast(LanguageModel, _SynthesisModel()),
    )
    result = asyncio.run(service.answer(_actor(), "KA45-in qiyməti nədir?"))
    assert "125 AZN" in result.answer
    assert not result.insufficient_evidence
    assert [item.source_class for item in result.citations] == ["STRUCTURED_AUTHORITATIVE"]
