"""Regression protection against silent zero-result document imports.

A mixed PDF with readable pages and pages requiring OCR must still produce
searchable text. Empty files can never be reported as fully indexed.
"""

from office_assistant.infrastructure.database.models import (
    DocumentProcessingStatus,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRecordStatus,
)
from office_assistant.knowledge.chunking import chunk_normalized_document
from office_assistant.knowledge.indexing import index_job_outcome
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.processing.models import ContentBlock, ParsedDocument
from office_assistant.processing.worker_service import searchable_parse_status


def test_partial_ocr_pdf_is_searchable_with_warning_retained() -> None:
    parsed = ParsedDocument(
        source_file_id=44,
        parser_name="pypdf",
        parser_version="5.0",
        media_type="application/pdf",
        metadata={},
        blocks=[ContentBlock(1, "page_text", "KA45 qiyməti 50 AZN-dir.", {"page": 1})],
        warnings=["Page 2 contains no extractable text; OCR may be required"],
        requires_ocr=True,
    )
    assert searchable_parse_status(parsed) == DocumentProcessingStatus.SUCCEEDED
    assert parsed.requires_ocr
    assert parsed.warnings
    chunks = chunk_normalized_document(
        parsed.as_dict(),
        source_file_id=44,
        parse_run_id=5,
        config=ChunkingConfig("test-v1", 300, 20),
    )
    assert len(chunks) == 1
    assert "KA45" in chunks[0].text
    assert chunks[0].locator["blocks"][0]["page"] == 1


def test_blank_pdf_and_image_only_never_fake_searchability() -> None:
    for require_ocr, require_vision in [(True, False), (False, True), (False, False)]:
        parsed = ParsedDocument(
            source_file_id=45,
            parser_name="empty",
            parser_version="1",
            media_type="application/pdf",
            metadata={},
            blocks=[],
            requires_ocr=require_ocr,
            requires_vision=require_vision,
        )
        assert searchable_parse_status(parsed) == DocumentProcessingStatus.DEFERRED
    whitespace = ParsedDocument(
        source_file_id=46,
        parser_name="txt",
        parser_version="1",
        media_type="text/plain",
        metadata={},
        blocks=[ContentBlock(1, "plain_text", "   ", {})],
    )
    assert searchable_parse_status(whitespace) == DocumentProcessingStatus.DEFERRED


def test_index_job_never_succeeds_with_no_chunks_or_all_failures() -> None:
    assert index_job_outcome([]) == KnowledgeIndexJobStatus.FAILED
    assert index_job_outcome([KnowledgeIndexRecordStatus.FAILED]) == KnowledgeIndexJobStatus.FAILED
    assert (
        index_job_outcome([KnowledgeIndexRecordStatus.SUCCEEDED])
        == KnowledgeIndexJobStatus.SUCCEEDED
    )
    assert (
        index_job_outcome(
            [KnowledgeIndexRecordStatus.SUCCEEDED, KnowledgeIndexRecordStatus.FAILED]
        )
        == KnowledgeIndexJobStatus.PARTIAL
    )
