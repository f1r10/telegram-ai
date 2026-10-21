# Office Assistant V2 — intelligent intake, human clarification and exact-byte dedup

Status (2026-10-21): **six owner requirements accepted as the intended system contract**, not a statement that all six end-to-end flows are implemented. This document guides subsequent code and staging acceptance. Do not place private supplier documents, images, prices or credentials in this repository.

## 1. Clarify only consequential ambiguity

The assistant must ingest supported Telegram documents, photos, captions, free text and price sheets while maintaining the distinction between **observed evidence**, **AI suggestions**, **human-approved interpretations**, and **authoritative business mutations**. An AI model may propose meanings; it must never silently turn uncertainty into an official sale price, payment, invoice, stock movement, supplier identity or confirmed product identity.

Clarification is warranted where an ambiguous field changes material meaning: product/SKU, price kind (cost vs sale vs quotation), currency, VAT inclusion, quantity, unit, warehouse, supplier or effective date. Minor presentational doubts should not repeatedly interrupt users. A question must show the original field, document context and concise plausible choices plus `Other` and `Not known`. Unknown must remain unknown. A low-confidence guess is not a confirmed fact; AI confidence alone never authorizes a write.

Suggested Telegram sequence: `File received` → `Reading and locating evidence` → `Needs clarification` (only when material) → `Show exact field, source locator and choices` → `Approve / Correct / Leave unknown` → `Save approved interpretation with audit provenance`. Model hallucinations cannot be used as source locators.

## 2. Reuse supplier-scoped, approved template interpretations

After a user explicitly confirms a file mapping, a later file with a **verified same supplier** and a sufficiently matched structure can be offered the same mapping. A filename, logo, model suggestion or a visual similarity score is not sufficient supplier verification.

Future durable mapping record requirements: supplier identity, verified document family, normalized headings/column coordinates, currency/VAT semantics, approver and confirmation time, source file/content digest, version, status and invalidation history. Show the proposed reuse for approval when field positions, headings or source semantics change. Never apply a supplier A rule silently to supplier B. A user's illustrative selection is **not** authorization to create a live durable template.

No mapping table or auto-reuse UI is claimed operational yet.

## 3. Retain prices and their evidence rather than overwriting

A product may have different vendor offers, valid-at dates, VAT terms and currencies simultaneously. Store each source-associated price as its own provenance-bearing observation/candidate. Unknown supplier, VAT, currency or price kind must remain unresolved and must not be silently assigned a default (even if a nearby column suggests one). An explicit acceptance pathway is required to elevate a candidate to an authoritative business record. Quotation/proposal prices do not by themselves establish completed sales.

Retain raw document identity, owner/session context, file page/sheet/cell or Telegram message locator, observed label, observed timestamp if present, original amount/currency, interpretation version and actor. New values must not destructively replace prior evidence. Canonical price resolution must be explicit about which scope is asked and return conflicts transparently.

## 4. Anchor images to genuine document or message context

Preserve evidence that a picture came from a particular PDF page, DOCX table cell, XLSX anchor or Telegram photo+caption. A picture located on the same page as a product is **not** automatically proof of its table row, especially when layouts are complex. Where the relation is ambiguous, show candidate rows and request confirmation. Visually similar cameras can be suggested as `SIMILAR`/`PROBABLE_MATCH`, never `EXACT_MATCH` without explicit trusted identity evidence. Manufacturer model/SKU verification is required before creating a confirmed reference.

An OCR/vision description may aid search but cannot become stock or price truth. Never send company photos outside authorized processor boundaries merely to detect duplicates.

## 5. Corrections preserve audit and the original observation

A user can challenge a previously approved field. Require current authorization, a review preview and explicit approval before updating the effective interpretation. Persist who changed what, why, when, the old value, new value and original source. Supersede the prior interpretation rather than deleting or rewriting the original bytes, source evidence or prior audit event. No automatic mass changes to historical invoices, inventory movements or other financial ledgers.

## 6. Deduplicate **only exact bytes**, never near matches

Identity key: SHA-256 of the **original uploaded byte stream** for raw uploads; SHA-256 of the **actual extracted image payload** for image assets. Uploaded filenames, Telegram labels, image appearance, OCR text, resized similarity, dHash/pHash, semantic embeddings and AI judgements are not substitutes for byte equality.

- A second upload with identical bytes reuses the existing original `FileObject` and physical `blobs/` object, without creating a second copy. The Telegram submission, caption and review provenance remain as separate records: this is an audit event, not a duplicate file.
- A standalone or embedded picture with the same image payload reuses one `ImageAsset` and content-addressed `image_assets/` storage. Each distinct real occurrence keeps its own document locator.
- One changed byte means a new digest and therefore **a distinct file/image content object**, even when its filename, extracted text or visual appearance matches. Re-encoding or metadata-only modification also counts as new bytes; no image perceptual threshold is allowed to suppress it.
- Existing stored original bytes must still pass integrity validation before they can be reused by a new upload. If the content has gone missing or been altered, refuse the duplicate claim; do not silently return the corrupt object.
- Exact duplicate evidence must not authorize a different SKU, sale price, stock amount or note: the business interpretation remains separate from content dedup.
- Repeated receipt/parse/index jobs may still occur for distinct submission sessions. Optimization of downstream reprocessing is separate and **not yet delivered**; implement it only if all authorized source provenance and revised annotations remain accessible.

## Acceptance tests and current scope

- Covered in code: original-file SHA-256 reuse with immutable file identity, content-addressed image assets, distinct source occurrences, human-confirmed product-image relations, basic candidate review/audit, supplier XLSX unlabeled-column warnings. Structured PRICE extraction now distinguishes SELLING_PRICE, COST_PRICE, QUOTE_PRICE and UNKNOWN; any role other than explicit SELLING_PRICE is retained for review and cannot flow through run-level approval into the authoritative sales-price table. This guard is deliberately conservative until a field-level clarification-and-approval interface is shipped.
- Hardened with this change: re-verify the physical blob before a duplicate upload reuses it; regression tests check byte-identical vs one-byte-different original uploads and photo assets; Mini App tells the user when exact content was reused.
- Still pending: cross-file supplier template persistence, AI clarification prompts/callbacks and reviewed mapping application, reliable PDF/DOCX image-to-table-row association on complex layouts, full end-user correction workflow, downstream duplicate parse/index reuse, and Android/iOS Telegram end-to-end UAT.

The operator must validate all ambiguous data scenarios on synthetic fixtures in CI, then on authorized private documents in isolated staging, keeping raw files out of GitHub and maintaining default-OFF stock-write flags.
