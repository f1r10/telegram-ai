"""A photo that matches a picture inside a document must answer from that document.

The owner's requirement is automatic: send the picture and the reply already carries what
the source file says about the asked subject, instead of waiting for a "Mənbə N" press.
Quoting is deterministic and fail-closed — an unrelated or unreadable document adds
nothing, and a similarity score never becomes a price, a code or a stock claim.
"""

# Test the private Telegram adapter boundary without exporting it as public API.
# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast

import pytest
from telegram.ext import ContextTypes

from office_assistant.integrations.telegram import handlers, messages
from office_assistant.integrations.telegram.photo_source_facts import (
    MAX_QUOTES_PER_FILE,
    MAX_SOURCE_FILES,
    render_photo_source_facts,
)
from office_assistant.internal_evidence.document_search import DocumentSearchInput
from office_assistant.internal_evidence.models import DocumentEvidence
from office_assistant.internal_evidence.service import InternalEvidenceService

CATALOGUE_TEXT = (
    "VKLUX KATALOQ\n"
    "KONUS 75 SM 2,5KG PLASTİK - 4 ƏDED QUTU\n"
    "DELINATOR 75 SM ORTA REZİN\n"
    "ÖLÇÜ: 75 SM | ÖN RƏNG: AĞ\n"
    "BAĞLANTI TELEFONU: +994 50 000 00 00\n"
    "ŞİRKƏT ÜNVANI: Bakı, 28 May küçəsi 12"
)


def _evidence(
    chunk_id: str,
    file_id: int,
    filename: str,
    text: str = CATALOGUE_TEXT,
) -> DocumentEvidence:
    return DocumentEvidence(
        chunk_id=chunk_id,
        text=text,
        file_id=file_id,
        filename=filename,
        locator={"page": 1},
        retrieval_method="hybrid",
        score=0.61,
    )


def test_only_lines_about_the_asked_subject_are_quoted() -> None:
    note = render_photo_source_facts(
        "Konus 75 sm var bizdə?",
        [_evidence("c1", 6, "VKLUX-KATALOQ.pdf")],
    )
    assert note is not None
    assert "KONUS 75 SM 2,5KG PLASTİK" in note
    assert "VKLUX-KATALOQ.pdf" in note
    # A contact number and the company address are not the asked subject.
    assert "Bakı" not in note
    assert "+994" not in note
    assert messages.PHOTO_SOURCE_FACTS_DISCLAIMER in note


def test_a_document_without_the_subject_produces_no_block() -> None:
    assert (
        render_photo_source_facts(
            "Konus 75 sm var bizdə?",
            [_evidence("c1", 6, "DIGER.pdf", text="ANBAR SIYAHI 2026\nMÖVCUD: 14 ƏDƏD")],
        )
        is None
    )


def test_a_question_without_a_subject_produces_no_block() -> None:
    assert render_photo_source_facts("Bu nədir?", [_evidence("c1", 6, "A.pdf")]) is None


def test_quotes_stay_bounded_per_file_and_across_files() -> None:
    first = [_evidence(f"a{i}", 6, "BIR.pdf", text=f"KONUS {i} SM PLASTİK") for i in range(4)]
    second = [_evidence(f"b{i}", 7, "IKI.pdf", text=f"KONUS {i} SM REZİN") for i in range(4)]
    note = render_photo_source_facts("Konus neçə sm?", [*first, *second])
    assert note is not None
    assert note.count("   •") == MAX_QUOTES_PER_FILE * MAX_SOURCE_FILES
    assert "BIR.pdf" in note
    assert "IKI.pdf" in note
    bounded = render_photo_source_facts(
        "Konus neçə sm?", [*first, *second, _evidence("c", 8, "UC.pdf", text="KONUS 90 SM")]
    )
    assert bounded is not None
    assert "UC.pdf" not in bounded


def test_a_long_line_is_truncated_rather_than_dumped() -> None:
    note = render_photo_source_facts(
        "Konus 75 sm var bizdə?",
        [_evidence("c1", 6, "UZUN.pdf", text="KONUS " + "75 SM " * 400)],
    )
    assert note is not None
    assert "…" in note
    assert len(note) < 1200


def test_an_identifier_question_matches_the_record_carrying_it() -> None:
    note = render_photo_source_facts(
        "QA-KA45 neçəyədir?",
        [_evidence("c1", 9, "QIYMET.pdf", text="QA-KA45 SATIŞ QİYMETİ 129 AZN")],
    )
    assert note is not None
    assert "QA-KA45" in note


class _Search:
    def __init__(self, by_file: dict[int, list[DocumentEvidence]], failing: int | None = None):
        self.by_file = by_file
        self.failing = failing
        self.calls: list[DocumentSearchInput] = []

    async def search(self, value: DocumentSearchInput) -> list[DocumentEvidence]:
        self.calls.append(value)
        if self.failing is not None and value.file_id == self.failing:
            raise RuntimeError("qdrant unavailable")
        return self.by_file.get(cast(int, value.file_id), [])


def _service(search: _Search) -> InternalEvidenceService:
    unused = cast(object, None)
    return InternalEvidenceService(
        unused,  # type: ignore[arg-type]
        unused,  # type: ignore[arg-type]
        search,  # type: ignore[arg-type]
        unused,  # type: ignore[arg-type]
        unused,  # type: ignore[arg-type]
        unused,  # type: ignore[arg-type]
    )


def test_source_facts_reads_each_matched_file_once_for_the_question() -> None:
    search = _Search(
        {
            6: [_evidence("c1", 6, "A.pdf", text="KONUS 75 SM")],
            7: [_evidence("c2", 7, "B.pdf", text="KONUS 90 SM")],
        }
    )
    facts = asyncio.run(_service(search).photo_source_facts("Konus neçə sm?", [6, 7, 6, 0, -3]))
    assert [item.filename for item in facts] == ["A.pdf", "B.pdf"]
    assert [call.file_id for call in search.calls] == [6, 7]
    assert all(call.query == "Konus neçə sm?" for call in search.calls)


def test_source_facts_keeps_other_files_when_one_is_unreadable() -> None:
    search = _Search({7: [_evidence("c2", 7, "B.pdf", text="KONUS 90 SM")]}, failing=6)
    facts = asyncio.run(_service(search).photo_source_facts("Konus neçə sm?", [6, 7]))
    assert [item.filename for item in facts] == ["B.pdf"]


def _patch_services(monkeypatch: pytest.MonkeyPatch, service: object) -> object:
    def fake_services(_context: ContextTypes.DEFAULT_TYPE) -> object:
        return SimpleNamespace(internal_evidence=service)

    monkeypatch.setattr(handlers, "_services", fake_services)
    return SimpleNamespace(user_data={})


def _note(context: object, question: str | None, file_ids: list[int]) -> str | None:
    return asyncio.run(
        handlers._photo_source_document_note(
            cast(ContextTypes.DEFAULT_TYPE, context), question, file_ids
        )
    )


def test_note_is_skipped_without_a_question_a_service_or_a_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = _Search({6: [_evidence("c1", 6, "A.pdf", text="KONUS 75 SM")]})
    context = _patch_services(monkeypatch, _service(search))
    assert _note(context, None, [6]) is None
    assert _note(context, "va", [6]) is None
    assert _note(context, "Konus 75 sm var bizdə?", []) is None
    assert search.calls == []

    without_service = _patch_services(monkeypatch, None)
    assert _note(without_service, "Konus 75 sm var bizdə?", [6]) is None
    assert search.calls == []


def test_note_reads_only_the_matched_file_once(monkeypatch: pytest.MonkeyPatch) -> None:
    search = _Search({6: [_evidence("c1", 6, "VKLUX-KATALOQ.pdf")]})
    context = _patch_services(monkeypatch, _service(search))
    note = _note(context, "Konus 75 sm var bizdə?", [6, 6, 0, -2])
    assert note is not None
    assert "KONUS 75 SM 2,5KG PLASTİK" in note
    assert [call.file_id for call in search.calls] == [6]


def test_a_hung_index_never_delays_the_photo_page(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Slow:
        async def photo_source_facts(
            self, question: str, file_ids: Sequence[int]
        ) -> list[DocumentEvidence]:
            await asyncio.sleep(0.2)
            return [_evidence("c1", 6, "A.pdf", text="KONUS 75 SM")]

    monkeypatch.setattr(handlers, "PHOTO_SOURCE_FACT_TIMEOUT_SECONDS", 0.01)
    context = _patch_services(monkeypatch, _Slow())
    assert _note(context, "Konus 75 sm?", [6]) is None


def test_an_index_outage_is_not_reported_as_a_search_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Broken:
        async def photo_source_facts(
            self, question: str, file_ids: Sequence[int]
        ) -> list[DocumentEvidence]:
            raise RuntimeError("qdrant unavailable")

    context = _patch_services(monkeypatch, _Broken())
    assert _note(context, "Konus 75 sm?", [6]) is None
