"""Kali image UAT: indexed label and attached-photo questions must be grounded."""

from office_assistant.internal_evidence.label_facts import concise_packaging_label_answer
from office_assistant.internal_evidence.models import (
    DocumentEvidence,
    ImageReading,
    RoutingCategory,
)
from office_assistant.internal_evidence.router import attached_image_plan, deterministic_plan
from office_assistant.internal_evidence.service import build_evidence_bundle

LABEL = (
    "SİNTETİK MƏHSUL ETİKETİ\n"
    "Məhsul kodu: QA-IMG-682\n"
    "Qutu miqdarı: 8 ədəd\n"
    "Partiya: L-731\n"
    "TEST ONLY - kataloq təsdiqi deyil."
)
QUESTION = "QA-IMG-682 məhsulunun qutu miqdarı və partiya kodu nədir?"
PHOTO_QUESTION = "Şəkildəki məhsul kodu, qutu miqdarı və partiya kodu nədir?"


def _document(text: str = LABEL, *, file_id: int = 682) -> DocumentEvidence:
    return DocumentEvidence(
        chunk_id=f"chunk-{file_id}",
        text=text,
        file_id=file_id,
        filename=f"QA_IMG_{file_id}_sintetik_etiket.png",
        locator={"image_index": 1, "extraction": "tesseract"},
        retrieval_method="hybrid",
        score=0.88,
    )


def test_product_pack_and_partiya_route_directly_to_existing_document_index() -> None:
    plan = deterministic_plan(QUESTION, force_internal=True)
    assert plan is not None
    assert plan.category == RoutingCategory.INTERNAL_DOCUMENT
    assert plan.use_document_search is True
    assert plan.use_structured_tools is False


def test_source_cited_ocr_label_answers_quantity_and_lot_without_catalog_approval() -> None:
    bundle = build_evidence_bundle([], [_document()])
    result = concise_packaging_label_answer(QUESTION, bundle)
    assert result is not None
    answer, cites = result
    assert "QA-IMG-682" in answer
    assert "8 ədəd" in answer
    assert "L-731" in answer
    assert "anbar qalığı və məhsul təsdiqi deyil" in answer
    assert len(cites) == 1
    assert cites[0].source_class == "INTERNAL_DOCUMENT"
    assert cites[0].file_id == 682
    assert "[1]" in answer


def test_explicit_reply_to_image_avoids_clarification_planner() -> None:
    plan = attached_image_plan(PHOTO_QUESTION, has_readable_image=True, force_internal=True)
    assert plan is not None
    assert plan.category == RoutingCategory.INTERNAL_DOCUMENT
    assert plan.use_document_search is False
    assert plan.business_tool_calls == []
    image = ImageReading(filename="telegram_photo_11.jpg", text=LABEL, model="gpt-4o-mini")
    bundle = build_evidence_bundle([], [], images=[image])
    result = concise_packaging_label_answer(PHOTO_QUESTION, bundle)
    assert result is not None
    answer, cites = result
    assert "QA-IMG-682" in answer and "8 ədəd" in answer and "L-731" in answer
    assert "təsdiqlənmiş kataloq və ya anbar məlumatı deyil" in answer
    assert cites[0].source_class == "IMAGE_UNTRUSTED"
    assert cites[0].file_id is None
    assert "[1]" in answer


def test_photo_plan_never_substitutes_for_business_or_inventory_queries() -> None:
    for question in (
        "Bu şəkil bazada təsdiqlənmiş məhsuldurmu?",
        "Bu şəkildəki malın anbar qalığı neçədir?",
        "Bu foto üzrə satış qiyməti nə qədərdir?",
    ):
        assert attached_image_plan(question, has_readable_image=True, force_internal=True) is None
    assert (
        attached_image_plan(PHOTO_QUESTION, has_readable_image=False, force_internal=True) is None
    )
    assert (
        attached_image_plan(PHOTO_QUESTION, has_readable_image=True, force_internal=False) is None
    )


def test_mismatched_sku_or_conflicting_label_values_fail_closed() -> None:
    mismatch = build_evidence_bundle([], [_document()])
    assert (
        concise_packaging_label_answer(
            "QA-IMG-683 məhsulunun qutu miqdarı və partiya kodu nədir?", mismatch
        )
        is None
    )
    conflict = build_evidence_bundle(
        [],
        [
            _document(),
            _document(LABEL.replace("8 ədəd", "9 ədəd"), file_id=683),
        ],
    )
    assert concise_packaging_label_answer(QUESTION, conflict) is None
    assert (
        concise_packaging_label_answer(
            QUESTION, build_evidence_bundle([], [_document(LABEL.replace("Partiya:", "Etiket:"))])
        )
        is None
    )


def test_product_fact_cannot_be_inferred_from_unlabelled_ocr_tokens() -> None:
    unlabeled = "QA-IMG-682 8 L-731 SINTETIK"
    bundle = build_evidence_bundle([], [_document(unlabeled)])
    assert concise_packaging_label_answer(QUESTION, bundle) is None
