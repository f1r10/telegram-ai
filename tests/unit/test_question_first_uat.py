"""User-observed PDF/DOCX/XLSX UAT reply quality and provenance regression."""

from office_assistant.internal_evidence.models import DocumentEvidence, RoutingCategory
from office_assistant.internal_evidence.router import deterministic_plan
from office_assistant.internal_evidence.service import (
    _deterministic_summary,  # pyright: ignore[reportPrivateUsage]
    build_evidence_bundle,
)
from office_assistant.internal_evidence.short_answers import concise_document_answer


def _doc(filename: str, file_id: int, text: str) -> DocumentEvidence:
    return DocumentEvidence(
        chunk_id=f"c{file_id}",
        text=text,
        file_id=file_id,
        filename=filename,
        locator={"page": 1},
        retrieval_method="hybrid",
        score=0.9,
    )


PDF = (
    "SINAQ SƏNƏDİ | PDF\n"
    "Məhsul kodu:\nQA-PDF-318\n"
    "Zəmanət müddəti:\n27 ay\n"
    "Məhsulun rəngi:\nnarıncı\n"
    "Quraşdırma növü:\ndivar montajı\n"
    "TEST ONLY: inventar, stok və satış deyil."
)
WORD = (
    "SINAQ SƏNƏDİ | Word\nQA-DOCX-624 məhsulunun təsviri\n"
    "Məhsul kodu | QA-DOCX-624\n"
    "Uzunluq | 240 sm\n"
    "Bir qutudakı ədəd sayı | 6 ədəd\n"
    "Material | alüminium\n"
    "Bu qiymət və anbar təsdiqi deyil."
)
XLSX = (
    "Sheet: Mehsullar Range: A1:E1\n"
    "Məhsul kodu | Satış qiyməti (AZN) | Maya dəyəri (AZN) | "
    "Təklif qiyməti (AZN) | Zəmanət (ay)\n"
    "Sheet: Mehsullar Range: A2:E2\n"
    "QA-XLSX-951 | 96.5 | 71.25 | 110 | 14\n"
    "Sheet: Mehsullar Range: A3:E3\n"
    "QA-XLSX-952 | 129.9 | 82.4 | 139 | 21\n"
    "TEST - real/aktiv qiymət deyil."
)


def test_pdf_warranty_answer_is_short_grounded_and_exact() -> None:
    bundle = build_evidence_bundle([], [_doc("Sinaq_PDF_QA-PDF-318.pdf", 318, PDF)])
    question = "QA-PDF-318 məhsulunun zəmanət müddəti neçə aydır?"
    result = concise_document_answer(question, bundle)
    assert result is not None
    answer, citations = result
    assert "27 ay" in answer
    assert "QA-PDF-318" in answer
    assert "[1]" in answer
    assert "narıncı" not in answer and "montaj" not in answer
    assert len(citations) == 1 and citations[0].file_id == 318


def test_word_box_quantity_has_no_router_model_call_and_answers_exactly() -> None:
    question = "QA-DOCX-624 məhsulundan bir qutuda neçə ədəd var?"
    plan = deterministic_plan(question, force_internal=True)
    assert plan is not None
    assert plan.category == RoutingCategory.INTERNAL_DOCUMENT
    assert plan.use_document_search and not plan.use_structured_tools
    bundle = build_evidence_bundle([], [_doc("Sinaq_Word_QA-DOCX-624.docx", 624, WORD)])
    result = concise_document_answer(question, bundle)
    assert result is not None
    answer, cites = result
    assert "6 ədəd" in answer
    assert "240 sm" not in answer
    assert len(cites) == 1 and cites[0].file_id == 624


def test_excel_prices_have_separate_explicit_roles_and_unapproved_state() -> None:
    question = "QA-XLSX-951 məhsulunun maya dəyəri və satış qiyməti neçə AZN-dir?"
    bundle = build_evidence_bundle([], [_doc("Sinaq_Excel_QA-XLSX-951.xlsx", 951, XLSX)])
    result = concise_document_answer(question, bundle)
    assert result is not None
    answer, citations = result
    assert "Satış qiyməti: 96,50 AZN" in answer
    assert "Maya dəyəri: 71,25 AZN" in answer
    assert "təsdiqlənmiş satış qiyməti deyil" in answer
    assert "129,90" not in answer and "139" not in answer
    assert len(citations) == 1 and citations[0].file_id == 951


def test_no_value_crossing_to_another_sku() -> None:
    only_other = build_evidence_bundle([], [_doc("other.xlsx", 952, XLSX)])
    assert (
        concise_document_answer(
            "QA-XLSX-953 məhsulunun maya dəyəri və satış qiyməti neçədir?", only_other
        )
        is None
    )
    assert (
        concise_document_answer(
            "QA-OTHER-951 məhsulunun maya dəyəri və satış qiyməti neçədir?", only_other
        )
        is None
    )


def test_ambiguity_and_mislabelled_columns_fail_closed() -> None:
    question = "QA-XLSX-951 maya dəyəri və satış qiyməti neçədir?"
    ambiguous = build_evidence_bundle(
        [],
        [
            _doc("a.xlsx", 951, XLSX),
            _doc("b.xlsx", 952, XLSX.replace("71.25", "75.25")),
        ],
    )
    assert concise_document_answer(question, ambiguous) is None
    no_roles = XLSX.replace("Satış qiyməti (AZN)", "Qiymət A (AZN)")
    bundle = build_evidence_bundle([], [_doc("unlabelled.xlsx", 951, no_roles)])
    assert concise_document_answer(question, bundle) is None


def test_synthesis_fallback_never_dumps_raw_excel_or_source_text() -> None:
    question = "QA-XLSX-951 məhsulunun qiyməti necə hesablanır?"
    bundle = build_evidence_bundle([], [_doc("table.xlsx", 951, XLSX)])
    answer, citations = _deterministic_summary(bundle, question)
    assert "Sənəd (" not in answer
    assert "Sheet:" not in answer
    assert "Məhsul kodu |" not in answer
    assert "96.5 | 71.25" not in answer
    assert "mənbə tapıldı" in answer
    assert len(citations) == 1


def test_synthesis_fallback_never_dumps_authoritative_business_json() -> None:
    from office_assistant.internal_evidence.models import StructuredEvidence

    bundle = build_evidence_bundle(
        [
            StructuredEvidence(
                tool_name="get_customer_proposals",
                status="OK",
                data={"private_customer": "CONFIDENTIAL_TOKEN", "total": "12"},
            )
        ],
        [],
    )
    answer, cites = _deterministic_summary(bundle, "Son təklif")
    assert "CONFIDENTIAL_TOKEN" not in answer
    assert "{" not in answer
    assert "mənbə tapıldı" in answer or "mənbə" in answer
    assert len(cites) == 1
