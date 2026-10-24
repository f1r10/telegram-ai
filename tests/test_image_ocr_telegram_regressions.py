"""Offline regressions for Telegram image OCR and packaging facts (synthetic only)."""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from office_assistant.infrastructure.database.models import User
from office_assistant.internal_evidence.document_search import InternalDocumentSearch
from office_assistant.internal_evidence.label_facts import concise_packaging_label_answer
from office_assistant.internal_evidence.models import (
    DocumentEvidence,
    EvidenceBundle,
    ImageReading,
    RoutingCategory,
    UnifiedCitation,
)
from office_assistant.internal_evidence.router import (
    EvidencePlanner,
    attached_image_plan,
    deterministic_plan,
)
from office_assistant.internal_evidence.service import (
    InternalEvidenceService,
    build_evidence_bundle,
)

QUESTION = "QA-IMG-682 məhsulunun qutu miqdarı və partiya kodu nədir?"
CAPTION = "Şəkildəki məhsul kodunu, qutu miqdarını və partiya kodunu oxu."
LABEL = (
    "SİNTETİK MƏHSUL ETİKETİ\n"
    "Məhsul kodu: QA-IMG-682\n"
    "Qutu miqdarı: 8 ədəd\n"
    "Partiya: L-731"
)


def _document(text: str = LABEL, *, file_id: int = 6) -> DocumentEvidence:
    return DocumentEvidence(
        chunk_id=f"test-{file_id}",
        text=text,
        file_id=file_id,
        filename="QA_IMG_682_sintetik_etiket.png",
        locator={"kind": "image_ocr"},
        retrieval_method="hybrid",
        score=0.9,
    )


def _image(text: str = LABEL) -> ImageReading:
    return ImageReading(
        filename="telegram_photo_123.jpg",
        text=text,
        model="synthetic-fixture",
    )


def _bundle_with_documents(*documents: DocumentEvidence) -> EvidenceBundle:
    return build_evidence_bundle([], list(documents))


def _bundle_with_image(image: ImageReading) -> EvidenceBundle:
    return build_evidence_bundle([], [], images=[image])


def _assert_answer(
    result: tuple[str, tuple[UnifiedCitation, ...]] | None,
    *,
    source: str,
) -> None:
    assert result is not None
    answer, citations = result
    assert "QA-IMG-682" in answer
    assert "8 ədəd" in answer
    assert "L-731" in answer
    assert len(citations) == 1
    assert citations[0].source_class == source


def test_code_specific_box_and_lot_question_routes_to_documents() -> None:
    plan = deterministic_plan(QUESTION, force_internal=True)
    assert plan is not None
    assert plan.category is RoutingCategory.INTERNAL_DOCUMENT
    assert plan.use_document_search
    assert not plan.business_tool_calls


@pytest.mark.parametrize(
    "query",
    [
        CAPTION,
        "Etiketdə məhsul kodunu, qutu miqdarını və partiyanı oxu.",
    ],
)
def test_attached_image_read_bypasses_generic_router(query: str) -> None:
    plan = attached_image_plan(query, has_readable_image=True, force_internal=True)
    assert plan is not None
    assert plan.intent == "ATTACHED_IMAGE_LABEL_READ"
    assert not plan.use_document_search
    assert not plan.use_structured_tools


def test_ocr_label_answers_with_original_source() -> None:
    _assert_answer(
        concise_packaging_label_answer(QUESTION, _bundle_with_documents(_document())),
        source="INTERNAL_DOCUMENT",
    )


def test_vision_reading_answers_with_untrusted_image_source() -> None:
    _assert_answer(
        concise_packaging_label_answer(CAPTION, _bundle_with_image(_image())),
        source="IMAGE_UNTRUSTED",
    )


@pytest.mark.parametrize(
    "label",
    [
        "- **Məhsul kodu**: **QA-IMG-682**\n"
        "• **Qutu miqdarı**： **8 ədəd**\n"
        "• **Partiya** — **L-731**",
        "Məhsul kodu:\nQA-IMG-682\nQutu miqdarı: 8\nPartiya:\nL-731",
    ],
)
def test_formatting_variations_do_not_erase_visible_fields(label: str) -> None:
    _assert_answer(
        concise_packaging_label_answer(CAPTION, _bundle_with_image(_image(label))),
        source="IMAGE_UNTRUSTED",
    )


def test_mismatched_identifier_and_conflicting_sources_fail_closed() -> None:
    assert (
        concise_packaging_label_answer(
            "QA-IMG-999 məhsulunun qutu miqdarı və partiya kodu nədir?",
            _bundle_with_documents(_document()),
        )
        is None
    )
    different_batch = LABEL.replace("L-731", "L-999")
    assert (
        concise_packaging_label_answer(
            QUESTION,
            _bundle_with_documents(_document(), _document(different_batch, file_id=7)),
        )
        is None
    )


def _service(*, documents: list[DocumentEvidence]) -> InternalEvidenceService:
    # Only the deterministic answer branch is exercised: no credentials, DB, model or vendor.
    service = object.__new__(InternalEvidenceService)
    service.planner = cast(
        EvidencePlanner,
        Mock(plan=AsyncMock(side_effect=AssertionError("generic planner must be skipped"))),
    )
    service.document_search = cast(
        InternalDocumentSearch,
        Mock(search=AsyncMock(return_value=documents)),
    )
    service.document_limit = 5
    return service


def test_baza_uses_indexed_ocr_even_without_a_product_catalog_row() -> None:
    service = _service(documents=[_document()])
    actor = cast(User, SimpleNamespace(id=1))
    with (
        patch.object(
            InternalEvidenceService, "_collect_structured", new_callable=AsyncMock
        ) as structured,
        patch.object(InternalEvidenceService, "_persist_turn", new_callable=AsyncMock),
    ):
        structured.return_value = ([], 0)
        answer = asyncio.run(service.answer(actor, QUESTION, force_internal=True))
    assert answer.route is RoutingCategory.INTERNAL_DOCUMENT
    assert "8 ədəd" in answer.answer
    assert "L-731" in answer.answer
    assert len(answer.citations) == 1
    structured.assert_awaited_once()


def test_photo_caption_answers_without_catalog_or_qdrant() -> None:
    service = _service(documents=[])
    actor = cast(User, SimpleNamespace(id=1))
    with (
        patch.object(
            InternalEvidenceService, "_collect_structured", new_callable=AsyncMock
        ) as structured,
        patch.object(InternalEvidenceService, "_persist_turn", new_callable=AsyncMock),
    ):
        structured.return_value = ([], 0)
        answer = asyncio.run(
            service.answer(actor, CAPTION, force_internal=True, image_readings=[_image()])
        )
    assert answer.route is RoutingCategory.INTERNAL_DOCUMENT
    assert "QA-IMG-682" in answer.answer
    assert "L-731" in answer.answer
    assert answer.citations[0].source_class == "IMAGE_UNTRUSTED"
    cast(AsyncMock, service.document_search.search).assert_not_awaited()
