"""Question-first, fail-closed answers to unambiguous, explicitly labelled document facts.

Only exact source identifiers and field labels may support deterministic facts.
Never promote a document price into an approved catalog price, posted sale or stock.
"""

import re
from decimal import Decimal, InvalidOperation

from office_assistant.internal_evidence.models import EvidenceBundle, UnifiedCitation
from office_assistant.internal_evidence.query_normalization import (
    normalize_user_query,
    strong_identifiers,
)

_DECIMAL = re.compile(r"\d+(?:[.,]\d{1,4})?")
_WARRANTY = re.compile(
    r"z[əe]man[əe]t\s+m[üu]dd[əe]ti\s*[:|]?\s*(\d{1,3})\s*(ay|il)\b",
    re.IGNORECASE,
)
_BOX = re.compile(
    r"bir\s+qutu(?:dak[ıi]|da)\s+(?:[əe]d[əe]d\s+say[ıi]\s*[:|]?\s*)?"
    r"(\d{1,5})\s*[əe]d[əe]d\b",
    re.IGNORECASE,
)


def _exact_identifier(identifier: str, text: str) -> bool:
    return identifier in strong_identifiers(text)


def _field_price(row: list[str], headings: list[str], field: str) -> str | None:
    for i, heading in enumerate(headings):
        normalized = normalize_user_query(heading)
        if field not in normalized or "azn" not in normalized or i >= len(row):
            continue
        value = row[i].strip()
        if not _DECIMAL.fullmatch(value):
            return None
        try:
            amount = Decimal(value.replace(",", "."))
        except InvalidOperation:
            return None
        if amount < 0:
            return None
        return f"{amount:.2f}".replace(".", ",")
    return None


def _spreadsheet_price(text: str, code: str) -> tuple[str, str] | None:
    """Pair a field-labelled spreadsheet header with an exact-identifier data row.

    Never infer column roles from numeric order alone or an unrelated table.
    """
    lines = [line.strip() for line in text.splitlines() if "|" in line]
    headers: list[str] | None = None
    prices: set[tuple[str, str]] = set()
    for line in lines:
        columns = [item.strip() for item in line.split("|")]
        if len(columns) < 3:
            continue
        if normalize_user_query(columns[0]) in {"mehsul kodu", "mehsul kod"}:
            headers = columns
            continue
        if not headers or len(columns) != len(headers):
            continue
        if code not in strong_identifiers(columns[0]):
            continue
        selling = _field_price(columns, headers, "satis qiymeti")
        cost = _field_price(columns, headers, "maya deyeri")
        if selling is not None and cost is not None:
            prices.add((selling, cost))
    return next(iter(prices)) if len(prices) == 1 else None


def concise_document_answer(
    question: str, bundle: EvidenceBundle
) -> tuple[str, tuple[UnifiedCitation, ...]] | None:
    """Return only directly provable, unambiguous values from the requested SKU.

    None means synthesis is still needed. Conflicting claims are never silently
    resolved by this fast path.
    """
    if bundle.conflicts or any(
        result.status == "CONFLICT" for result in bundle.structured_results
    ):
        return None
    ids = strong_identifiers(question)
    if len(ids) != 1:
        return None
    identifier = ids[0]
    norm = normalize_user_query(question)
    wants_warranty = "zemanet" in norm and "muddet" in norm
    wants_box = "qutu" in norm and any(
        word in norm for word in ("eded", "say", "nece")
    )
    wants_prices = "maya" in norm and "satis" in norm and "qiymet" in norm
    if not (wants_warranty or wants_box or wants_prices):
        return None

    found: dict[str, list[UnifiedCitation]] = {}
    for cite in bundle.citations:
        if cite.source_class != "INTERNAL_DOCUMENT":
            continue
        text = bundle.evidence_payloads.get(cite.evidence_id, "")
        if not _exact_identifier(identifier, text):
            continue
        answer: str | None = None
        if wants_warranty:
            values = {(value, unit.lower()) for value, unit in _WARRANTY.findall(text)}
            if len(values) == 1:
                value, unit = next(iter(values))
                answer = f"{value} {unit}"
        elif wants_box:
            quantities = set(_BOX.findall(text))
            if len(quantities) == 1:
                answer = f"{next(iter(quantities))} ədəd"
        elif wants_prices:
            amounts = _spreadsheet_price(text, identifier)
            if amounts:
                selling, cost = amounts
                answer = f"{selling}|{cost}"
        if answer is not None:
            found.setdefault(answer, []).append(cite)
    if not found or len(found) != 1:
        return None

    value, refs = next(iter(found.items()))
    reference = refs[0].model_copy(update={"number": 1})
    if wants_warranty:
        answer = f"Sənədə əsasən, {identifier} məhsulunun zəmanət müddəti {value}-dır. [1]"
    elif wants_box:
        answer = f"Sənədə əsasən, {identifier} məhsulunun bir qutusunda {value} var. [1]"
    else:
        selling, cost = value.split("|")
        answer = (
            f"{identifier} məhsulunun sənəddə göstərilən qiymətləri:\n"
            f"Satış qiyməti: {selling} AZN\n"
            f"Maya dəyəri: {cost} AZN\n"
            "Bu rəqəmlər sənəddən götürülüb; təsdiqlənmiş satış qiyməti deyil. [1]"
        )
    return answer, (reference,)
