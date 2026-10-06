# ADR 0006: Deterministic Document Parser Adapters

## Decision

Use a small `DocumentParser` protocol and `ParserRegistry` with targeted adapters:

- pypdf for page-by-page PDF text extraction;
- python-docx for ordered paragraphs, headings, and tables;
- openpyxl in read-only, `data_only=False` mode for spreadsheet values and formulas;
- Python UTF-8 facilities for TXT;
- an explicit deferred image adapter for JPG/JPEG/PNG.

Adapters return one typed normalized document model with blocks and provenance locators. Derived JSON and text are written atomically under the configured storage root and metadata is kept in PostgreSQL parse-run rows. Raw committed blobs are never modified.

## Deferred work

OCR, vision, embeddings, RAG chunking, and business interpretation are intentionally outside this phase. Docling is postponed for a benchmark against representative company documents; it is not a production dependency in Phase 4.

## Open-source decisions

pypdf solves the current narrow PDF parsing requirement and is BSD-3-Clause licensed. python-docx and openpyxl solve Office structure access and are MIT licensed. These libraries replace manual PDF or Office XML parsing; project-specific logic remains normalization, provenance, processing state, and storage.
