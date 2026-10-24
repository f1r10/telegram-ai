"""Source-scoped, label-based packaging facts for exact SKU and attached image questions.

This does not confirm product identity, stock, prices, sales or a catalog match.
No cross-source values are combined. Conflicting source observations fail closed.
"""

import re
from dataclasses import dataclass

from office_assistant.internal_evidence.models import EvidenceBundle, UnifiedCitation
from office_assistant.internal_evidence.query_normalization import normalize_user_query

# OCR and Vision often insert bullets, Markdown emphasis or non-ASCII colons.
# Accept formatting variation while requiring explicit field labels and one
# unambiguous code, quantity and batch in the SAME source payload.
_CODE_FIELD = re.compile(
    r"(?im)^[ \t]*(?:[-•]\s*)?(?:\*\*)?(?:məhsul|mehsul)\s+kodu(?:\*\*)?"
    r"\s*[:：|–—-]?[ \t]*\s*(?:\*\*)?"
    r"([A-Z]{1,8}(?:-[A-Z]{1,8})?-\d{1,8}[A-Z0-9-]*)\b"
)
_QUANTITY_FIELD = re.compile(
    r"(?im)^[ \t]*(?:[-•]\s*)?(?:\*\*)?qutu\s+"
    r"(?:miqdar[ıi]|[əe]d[əe]d(?:\s+say[ıi])?)(?:\*\*)?"
    r"\s*[:：|–—-]?[ \t]*\s*(?:\*\*)?(\d{1,6})\b"
)
_BATCH_FIELD = re.compile(
    r"(?im)^[ \t]*(?:[-•]\s*)?(?:\*\*)?"
    r"(?:partiya(?:\s+kodu)?|lot(?:\s+number)?|batch(?:\s+number)?)(?:\*\*)?"
    r"\s*[:：|–—-]?[ \t]*\s*(?:\*\*)?([A-Z]{1,6}-[A-Z0-9]{1,15})\b"
)
_QUESTION_CODE = re.compile(
    r"(?<![A-Za-z0-9-])([A-Za-z]{1,8}(?:-[A-Za-z]{1,8})?-\d{1,8}[A-Za-z0-9-]*)"
    r"(?![A-Za-z0-9-])",
)
_IMAGE_MARKERS = ("sekildeki", "fotodaki", "bu sekil", "bu foto")


@dataclass(frozen=True, slots=True)
class LabelFacts:
    product_code: str
    box_quantity: int
    batch_code: str


def _facts(text: str) -> LabelFacts | None:
    # Ambiguity across rows or repeated fields cannot be silently reconciled.
    codes = {value.upper() for value in _CODE_FIELD.findall(text)}
    amounts = {int(value) for value in _QUANTITY_FIELD.findall(text)}
    batches = {value.upper() for value in _BATCH_FIELD.findall(text)}
    if len(codes) != 1 or len(amounts) != 1 or len(batches) != 1:
        return None
    quantity = next(iter(amounts))
    if not 0 < quantity <= 100000:
        return None
    return LabelFacts(next(iter(codes)), quantity, next(iter(batches)))


def concise_packaging_label_answer(
    question: str, bundle: EvidenceBundle
) -> tuple[str, tuple[UnifiedCitation, ...]] | None:
    if bundle.conflicts or any(
        item.status in {"OK", "CONFLICT"} for item in bundle.structured_results
    ):
        return None
    normalized = normalize_user_query(question)
    wants_box = "qutu" in normalized and any(
        term in normalized for term in ("miqdar", "eded", "say", "nece")
    )
    wants_batch = "partiya" in normalized or "batch" in normalized
    if not (wants_box and wants_batch):
        return None
    image_question = any(marker in normalized for marker in _IMAGE_MARKERS) or (
        bool(bundle.image_results)
        and any(term in normalized for term in ("oxu", "etiketde", "etiketin"))
        and not _QUESTION_CODE.search(question)
    )
    asked_ids = {value.upper() for value in _QUESTION_CODE.findall(question)}
    if not image_question and len(asked_ids) != 1:
        return None
    if image_question and len(asked_ids) > 1:
        return None

    matches: dict[LabelFacts, list[UnifiedCitation]] = {}
    for citation in bundle.citations:
        if image_question and citation.source_class != "IMAGE_UNTRUSTED":
            continue
        if not image_question and citation.source_class != "INTERNAL_DOCUMENT":
            continue
        payload = bundle.evidence_payloads.get(citation.evidence_id, "")
        facts = _facts(payload)
        if facts is None:
            continue
        if asked_ids and facts.product_code not in asked_ids:
            continue
        matches.setdefault(facts, []).append(citation)
    if len(matches) != 1:
        return None
    facts, citations = next(iter(matches.items()))
    cite = citations[0].model_copy(update={"number": 1})
    if image_question:
        answer = (
            "Şəkildə oxunan məlumat:\n"
            f"Məhsul kodu: {facts.product_code}\n"
            f"Qutu miqdarı: {facts.box_quantity} ədəd\n"
            f"Partiya kodu: {facts.batch_code}\n"
            "Bu məlumat yalnız şəkildə görünən etiketə əsaslanır; "
            "təsdiqlənmiş kataloq və ya anbar məlumatı deyil. [1]"
        )
    else:
        answer = (
            f"{facts.product_code} məhsulunun sənəddə göstərilən məlumatları:\n"
            f"Qutu miqdarı: {facts.box_quantity} ədəd\n"
            f"Partiya kodu: {facts.batch_code}\n"
            "Bu məlumat sənədin mətnidir; anbar qalığı və məhsul təsdiqi deyil. [1]"
        )
    return answer, (cite,)
