# ADR 0017: Vision Extraction for Uploaded Images

## Status

Accepted (2026-10-06). Supersedes the "images are out of scope" line in SPEC §2 and the
"OCR, image …" entry in ROADMAP's out-of-scope list, for uploaded raster images only.

## Context

Users photograph price lists, warranty notes and letters and send them to the bot as photos.
Since Phase 4 the ingestion worker marked every `.jpg`/`.jpeg`/`.png` with
`ImageDeferredParser`, which produced zero content blocks and a parse run in `DEFERRED`.
`knowledge/indexing.py` only indexes runs in `SUCCEEDED`, so the file was stored, reported as
processed, and then permanently invisible to `/baza` — a silent failure the user could not see.

The owner approved building this phase on 2026-10-06 with four explicit decisions: the default
`vision_model` is `gpt-4o-mini`; sending image bytes to the OpenAI-compatible vendor is
acceptable; scanned PDFs stay out of scope; only uploaded image files are handled.

## Decision

1. **Transport stays behind a narrow interface.** A `VisionLanguageModel` protocol in
   `ai/provider.py` exposes one operation: instructions plus image bytes plus a pydantic output
   schema, returning the existing `ModelResponse`. `OpenAILanguageModel` implements it through the
   Responses API `input_image` block with a base64 data URL. No vendor SDK type crosses into
   `processing/`.
2. **Only uploaded raster images.** JPEG and PNG, recognized by declared content type **and** by
   magic bytes before anything leaves the process. A file whose bytes disagree with its extension
   fails the parse run instead of being sent to the vendor.
3. **Scanned PDFs are out of scope.** The `pypdf` parser keeps emitting its
   "no extractable text; OCR may be required" warning and `requires_ocr` marking. Page rasterization
   is deliberately not added here.
4. **Vision output is document text, nothing else.** The transcription becomes `ContentBlock`
   values (`block_type="paragraph"`, `locator={"page": 1}`) written to the same
   `derived/<file_id>/<run_id>/normalized.json` and `text.txt` as any parsed document, so the run
   reaches `SUCCEEDED` and the normal chunk/embedding/index path makes it retrievable.
5. **Vision output never becomes a business fact automatically.** `extraction/sources.py` already
   skips runs with `requires_vision`, and that skip is kept: image text feeds search and grounded
   answers, and a price read off a photo still needs the candidate-first human confirmation to
   enter the database.
6. **Untrusted data.** The image's own text never acts as an instruction. The model is asked to
   transcribe verbatim and to describe, with a schema that has no place for actions, and the prompt
   states that visible text is content only. Retrieved/ingested text is data for the rest of the
   pipeline as well: numbers still come from PostgreSQL when `/baza` answers a business question.
7. **Failure is honest and bounded.** Images above the configured byte bound are not sent; a vendor
   error or an invalid response leaves the run in `DEFERRED`, logs a warning with the file id and
   error type (never the content), and the completion notification tells the user in Azerbaijani how
   many files were left unprocessed. The bot never invents transcription.

## Consequences

- Photographed documents become searchable knowledge instead of silently dead files.
- One more vendor call per image, with a size bound and a dedicated model id, so image cost is
  separable from chat cost in the logs.
- `DEFERRED` stays a real terminal state (no new enum values, no migration), and the ingestion job
  still "succeeds" when a provider is down; the difference is that the reason is now logged and
  visible to the user.
- Image quality, handwriting and multi-page photos remain a known limitation; the transcription is
  stored with the vision parser name so it can be re-run later.

## Alternatives considered

- **Local OCR (Tesseract/RapidOCR)**: no vendor egress and no per-image cost, but Azerbaijani text
  quality on phone photos is weak and it adds a large model/dependency to the image for a phase that
  already has a bounded vendor interface. Rejected for now; revisit if volume makes vendor cost the
  dominant term.
- **Docling / heavier document stack**: still postponed per ADR 0006; it does not remove the need
  for a vision call on a photo.
- **Storing image embeddings for multimodal retrieval**: out of scope; text transcription plus the
  existing hybrid index already answers the reported failure.
