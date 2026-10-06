# ADR 0007: Replace PyMuPDF with pypdf

## Decision

Phase 4.1 removes PyMuPDF from the production dependency graph and uses pypdf 6.19.0 for the current narrow PDF requirements: page enumeration, page-by-page text extraction, page provenance, and detection of pages with no meaningful extracted text.

PyMuPDF's AGPL/commercial dual licensing is unnecessary for the current internal V1 feature set. pypdf is BSD-3-Clause licensed and does not require native PDFium binaries for this adapter.

## Scope and limitations

The normalized document model, worker lifecycle, storage, and job semantics are unchanged. PDF editing, rendering, layout reconstruction, OCR, annotations, and visual extraction remain out of scope. A corrupt or unsupported PDF still produces a deterministic parser failure; an empty or image-only page produces `requires_ocr=true` and does not invoke OCR.

The replacement should be reevaluated if later requirements need rendering, advanced layout fidelity, or visual PDF analysis.
