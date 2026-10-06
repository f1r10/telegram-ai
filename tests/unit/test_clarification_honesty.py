"""Clarification text must name the element the plan actually found missing."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.ai.provider import LanguageModel
from office_assistant.ai.service import GroundedAnswerService
from office_assistant.business_tools.registry import BusinessToolRegistry
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.internal_evidence.document_search import InternalDocumentSearch
from office_assistant.internal_evidence.models import EvidencePlan, RoutingCategory
from office_assistant.internal_evidence.router import EvidencePlanner, deterministic_plan
from office_assistant.internal_evidence.service import (
    CLARIFICATION_CUSTOMER_AZ,
    CLARIFICATION_GENERIC_AZ,
    InternalEvidenceService,
)


class _Planner:
    def __init__(self, evidence_plan: EvidencePlan) -> None:
        self.evidence_plan = evidence_plan

    async def plan(self, user_id: int, question: str, **kwargs: object) -> object:
        return SimpleNamespace(plan=self.evidence_plan, response=None)


class _ForbiddenRegistry:
    names = ("resolve_product_price",)

    async def execute(self, name: str, context: object, payload: object) -> object:
        raise AssertionError(f"Business tool {name} ran for a clarification turn")


class _ForbiddenSearch:
    async def search(self, value: object) -> list[object]:
        raise AssertionError("Document search ran for a clarification turn")


class _Harness(InternalEvidenceService):
    async def _persist_turn(
        self,
        user_id: int,
        question: str,
        answer: str,
        citations: object,
    ) -> None:
        return None

    async def _save_usage(self, user_id: int, request_type: str, response: object) -> None:
        return None


def _service(evidence_plan: EvidencePlan) -> _Harness:
    service = _Harness.__new__(_Harness)
    service.session_factory = cast(async_sessionmaker[AsyncSession], SimpleNamespace())
    service.registry = cast(BusinessToolRegistry, _ForbiddenRegistry())
    service.document_search = cast(InternalDocumentSearch, _ForbiddenSearch())
    service.planner = cast(EvidencePlanner, _Planner(evidence_plan))
    service.language_model = cast(LanguageModel, SimpleNamespace())
    service.general_service = cast(GroundedAnswerService, SimpleNamespace())
    service.document_limit = 5
    service.source_ttl = timedelta(minutes=60)
    service.source_context_messages = 6
    service.max_tool_calls = 4
    return service


def _actor() -> User:
    return User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)


def _plan(*, reason_code: str, requires_clarification: bool = True) -> EvidencePlan:
    return EvidencePlan(
        category=RoutingCategory.CLARIFICATION_REQUIRED,
        intent="PRODUCT_PRICE",
        use_structured_tools=True,
        use_document_search=False,
        requires_clarification=requires_clarification,
        reason_code=reason_code,
    )


def test_unrelated_clarification_is_not_blamed_on_a_missing_customer() -> None:
    result = asyncio.run(
        _service(_plan(reason_code="product_identifier_unclear")).answer(
            _actor(), "KA45S qiymeti necedi"
        )
    )
    assert result.answer == CLARIFICATION_GENERIC_AZ
    assert result.route == RoutingCategory.CLARIFICATION_REQUIRED
    assert "müştəri" not in result.answer.lower()


def test_customer_wording_is_used_only_for_the_reason_code_that_needs_it() -> None:
    mapped = asyncio.run(
        _service(_plan(reason_code="customer_required_for_proposal_comparison")).answer(
            _actor(), "son iki təklifin fərqi nədir?"
        )
    )
    assert mapped.answer == CLARIFICATION_CUSTOMER_AZ

    plan = deterministic_plan("son 2 teklif arasinda ferq necedi")
    assert plan is not None
    routed = asyncio.run(_service(plan).answer(_actor(), "son 2 teklif arasinda ferq necedi"))
    assert routed.answer == CLARIFICATION_CUSTOMER_AZ


def test_clarification_category_alone_stops_before_any_evidence_collection() -> None:
    result = asyncio.run(
        _service(_plan(reason_code="ambiguous_request", requires_clarification=False)).answer(
            _actor(), "onu ver"
        )
    )
    assert result.answer == CLARIFICATION_GENERIC_AZ
    assert result.route == RoutingCategory.CLARIFICATION_REQUIRED
