# Lens-style office catalog visual retrieval: verified architecture and release gates

Status: **DESIGN + PARTIAL CODE FIX**, NOT calibrated visual identity.  
Authoritative catalog and ledger are private; reverse-image search is never equivalent to verified company stock, price, or product identity.

## 1. Failure seen on Telegram staging

The operator uploaded a watch photograph captioned `Bu casio 1234 modelidir 129 azn yadda saxla`. The old deterministic parser incorrectly treated the amount `129 AZN` as another product code, reported ambiguity, and **did not queue a product image confirmation**. The photo file then ingested successfully; a later photograph produced a PDF catalog no-match message. Neither file save nor document Qdrant indexing establishes a confirmed product photo relation.

PR #112 fixes the AZN ambiguity, supports `modelidir` binding and bounds slow photo fallback execution; the operator must still ensure **catalog product code 1234 exists**, reattach a photograph with a correct product code, and explicitly confirm `/saxla`. An amount typed in a photo caption is **not** a verified price or sale. No new identity is auto-created.

## 2. Independent sources reviewed

- [Google Lens official](https://search.google/ways-to-search/lens/): camera/photo search and product discovery; Google's full Shopping Graph scale cannot be reproduced from a handful of local references.
- [Yandex visual search official](https://yandex.com/support/browser/en/search-and-browse/search): exact-copy and similar-image retrieval; this is not an open, unrestricted product-recognition API.
- [Qdrant ecommerce reverse-image-search example](https://github.com/qdrant/examples): existing CLIP + vector search stack aligns with our Qdrant deployment.
- [Qdrant multivectors + retrieval/reranking guide](https://qdrant.tech/documentation/tutorials-search-engineering/using-multivector-representations/): fast coarse retrieval then more costly reranking is preferable to an expensive check of every reference on every query.
- [Google SigLIP2 reference](https://github.com/google-research/big_vision/blob/main/big_vision/configs/proj/image_text/README_siglip2.md): newer multilingual visual encoder, a candidate to benchmark against CLIP.
- [Meta DINOv2 reference](https://github.com/facebookresearch/dinov2): object-centric visual representation candidate; substantially different runtime and deployment requirements.
- [FastEmbed official supported models](https://qdrant.tech/documentation/embeddings/fastembed/): image CLIP and SigLIP2 available in documented newer releases. **Current project's `uv.lock` pins FastEmbed 0.8.1**; do not claim SigLIP2 runs until a reviewed version upgrade, model licensing review and offline model provisioning.

Do not scrape the Google Lens or Yandex internal browser-upload flow and treat its result as a supported API. Company images must not leave the local installation unless the user explicitly consents to an authorized provider with documented retention and access rules.

## 3. Target pipeline

1. **Intent/consent:** distinguish `yadda saxla` (source ingestion with explicit confirm) from `bizdə varmı?` (read-only visual lookup). Product identity, price, and stock are separate authoritative domains.
2. **Source integrity:** verify active PostgreSQL product, approved photo association, source file digest and access scope. No active confirmed reference => say so; do not tell the user the product is absent.
3. **Image normalization:** EXIF orientation, bounded bytes/pixels, local crop/object bounding box and optical text hints from real label/caseback; do not trust OCR as final exact SKU.
4. **Low-latency retrieval:** SHA-256 for exact approved source bytes, dHash/aHash for recompressed near duplicates; cached embeddings (multi-angle per SKU) in a dedicated Qdrant collection for candidate shortlist. Independently verify photo source and active revocation state after vector retrieval.
5. **Re-ranking:** optional stronger visual encoder (evaluate CLIP baseline vs offline SigLIP2 or DINO-backed image retrieval), geometry/crop and visible brand/model OCR. Only shortlist 10-20 candidates. Score by product group, not by the sheer number of its saved angles, to avoid sample bias. Re-check SKU and source status at reply time.
6. **Calibrated abstention:** `EXACT_APPROVED_BYTES`, `NEAR_DUPLICATE_CANDIDATES`, `SEMANTIC_CANDIDATES`, `UNCONFIRMED`, `UNKNOWN`, `MODEL_UNAVAILABLE`. Do not promote a semantic score to a verified product, price or available stock.
7. **Optional internet mode:** explicit private-photo egress approval and contract-compatible reverse-image provider only; external visually similar products are not company catalog stock.
8. **User-facing answer:** show at most 3 candidate cards with visible confidence *type*, SKU, optional verified source, one-tap verification; never claim price or warehouse balance from a picture or raw PDF.

## 4. Performance design

- Use a bounded, prewarmed local model worker; default missing model must fail fast instead of attempting network download.
- Cache approved reference embeddings by **source content SHA + embedding model/version**; do not cache arbitrary user query images as durable product authority.
- Prefer indexed ANN retrieval in a separate Qdrant photo collection over every-query image decode and embedding of every stored reference. Filter by active/authorized product ownership and verify revocations against PostgreSQL before replying.
- Use a short, explicit latency budget for near duplicates and PDF fallback, and avoid sequential per-candidate PDF render calls. Instrument timestamp phases (Telegram file acquisition, DB lookup, pHash, ANN, rerank, answer) with anonymized counts only.
- Release target to measure (not a claim): warm-model p50 and p95; a user-perceived target may be p95 <= 5 s for catalog candidate search, with a truthful partial/unknown fallback if the budget expires. Cold model provisioning is an operator activity, not a Telegram turn.

## 5. Private holdout acceptance BEFORE enabling semantic search

From previously independently confirmed *active* SKU photos, assemble multiple separate real phone views with different background, angle, illumination, packaging, close confusable models, new/unknown SKUs and negative unrelated objects. At least 3 independent angles per test SKU are preferred; larger balanced sample sizes are needed before commercial claims. Ground truth is reviewed by humans and never inferred from a model prediction. Raw photos and SKU labels stay private, never in CI artifacts or public GitHub.

Use existing `office_assistant.scripts.evaluate_visual_photos` in default dry-run first, then `--apply` only if the local image encoder is already installed. Measure per-scenario recall@1/3/5, MRR, false suggestions for unknowns, and incorrect exact-identity claim rate. Fix weights/thresholds on a development split and evaluate once on an unseen held-out split. Report the candidate limit and reference truncation.

**Current blocker:** initial staging preflight found `active_confirmed_reference_relations: 0`. No amount of image-search tuning substitutes for a trusted catalog relation. Even after PR #112, `1234` must exist as an active product in the authoritative catalog before a photo can be linked.

## 6. Versioned implementation waves

- **Wave 0 — delivered in PR #112:** caption parser fixes, bounded slow-path search, correct unconfirmed-image feedback and regression tests.
- **Wave 1 — next, only after owner test:** at least one approved genuine product picture, verify same/recompressed image + alternate-angle + unrelated negative, collect timing; document failure modes.
- **Wave 2:** separate opt-in Qdrant image-vector projection service with revocation/delete job safety, offline cache warm, metrics; migrate no existing document Qdrant collection.
- **Wave 3:** offline SigLIP2 vs CLIP benchmarks (first upgrade/review FastEmbed lock/model terms, no default downloads), cropped region + OCR rerank only if holdout improves. Benchmark before changing default.
- **Wave 4:** optional consent-based external visual search and Mini App mobile photo UI once HTTPS/signed Telegram launch is accepted.

No phase should be marked accepted until the corresponding real-corpus/latency/privacy evidence exists.

## 8. Default PDF-image evidence fallback

Ordinary Telegram photo search previously skipped embedded PDF imagery unless the
caption explicitly mentioned a PDF. When approved SKU photo lookup returns no match, a read-only,
**near-duplicate-only** scan of existing indexed PDF image occurrences is now attempted with a 2-second comparison timeout. Source
results identify PDF pages, **not product identity**. Explicit PDF requests
retain their existing fuller behavior; semantic PDF inference is not enabled
by this implicit fallback. This is not a full cross-angle unified image index.

Next design gate: product-balanced, precomputed image vectors for both approved
SKU photos and document occurrences; isolate the PDF evidence namespace from
human-approved SKU relations, attach document-page/product-label provenance,
and shortlist using indexed ANN plus local OCR/crop rerank. Index all active
sources rather than silently treating a capped first 500 as complete. Validate
real negative images and latency before any automatic semantic PDF fallback.

## 9. Local offline visual catalog preparation (bounded implementation)

The separately opt-in image encoder can now prepare up to **500 approved active SKU
photo references** and up to **500 active embedded PDF image occurrences** using
versioned, content-hash-addressed local vectors. This does not activate the
model and does not create product claims from PDF images. For an authorized
staging host, use the following commands without exporting pictures:

```bash
uv run python -m office_assistant.scripts.prepare_visual_catalog
uv run python -m office_assistant.scripts.prepare_visual_catalog --apply
```

The first command is a count-only dry run. The second requires the compatible
model to have already been installed offline. Both report aggregate coverage,
including whether their selection was truncated. The query path still uses a
bounded local reference scan, **not** a dedicated Qdrant ANN image collection;
the index redesign and real negative/cross-angle phone calibration remain open.
Fine spatial colour comparison now supplements coarse image hashes on the
near-duplicate path, including border-cropped PDF candidates, to reduce
unrelated-object suggestions. Its thresholds must be assessed against real
positive and negative images, not treated as proof of identity.

## 10. Bounded optional image comparison stages

Telegram now applies a 2-second wait budget to approved-image near-duplicate
comparison and 4 seconds to its optional semantic image comparison. The Mini
App's semantic stage has a 5-second budget instead of 25 seconds. These are
**stage waits**, not end-to-end SLAs: an already-running thread may continue
after cancellation, and Telegram transfer, PostgreSQL, PDF source enrichment,
and other work are additional. No result or stock fact is fabricated at timeout.
Operator-owned mobile p50/p95 measurement is still required before any
latency release gate can be accepted.

## 11. Optional DINOv2 mean-pooling from local-image-search (2026-10-26)

Source: https://github.com/f1r10/local-image-search, `app/vision_pipeline.py`
and `app/embedding.py`. The owner reports encouraging real image search
results in the independent app; its published GitHub CI establishes synthetic
CPU model execution, **not** our company photos' recognition accuracy.

A small opt-in adapter now supports version `dinov2-small-mean-v1` in
`SemanticVisualSearch`: pre-provisioned **facebook/dinov2-small** CPU
weights, `last_hidden_state.mean(dim=1)` and L2 normalization, yielding
384-dimensional embeddings. It is intentionally YOLO-free: stock COCO YOLOv8n
does not contain a wristwatch category, and the original crop behavior is
configuration-dependent. Therefore this is **not** a bit-identical reproduction
of all `local-image-search` provider modes. Independent version keys and
dimension checks prevent mixing new DINO vectors with existing 512-dimensional
CLIP vectors. Source bytes still need active human approval and SHA verification
before candidate scoring. The query image/embedding is not persisted.

**Deployment state: OFF by default.** The base image does not ship optional
Torch / Transformers and the encoder passes `local_files_only=True`. Install
CPU-only PyTorch and Transformers in a reviewed optional staging image, provision
both processor and model weights into the same embedding cache root **offline**,
and set the version only after that and a successful operator-led dry-run:
```text
VISUAL_SEMANTIC_ENABLED=true
VISUAL_SEMANTIC_MODEL_VERSION=dinov2-small-mean-v1
```
These settings are illustrative, **not** instructions to enable uncalibrated
production suggestions. Run `office_assistant.scripts.prepare_visual_catalog`
without `--apply` first; then prewarm with `--apply` on the offline model
host only. Check the same private camera-photo holdout for recall@1/3,
unrelated-object candidate rate and warm p95 before enabling the Telegram
flag. Existing `VISUAL_SEMANTIC_MIN_SCORE=0.60` is CLIP-derived and is
**not** a calibrated DINOv2 threshold. Copy no public-test catalog SQLite
rows or user photos across repositories; preserve PostgreSQL as authority.

## 12. Process-local verified vector hot cache (2026-10-27)

The semantic shortlist now stores **only per-reference normalized vectors** in a
bounded in-memory cache after the source bytes have passed the existing
SHA-256 content check. Every query still reads a fresh, authorized active
reference list from the application database; an explicitly revoked relation
is **never** made active by the cache. A cache hit requires the same current
resolved storage path, device, inode, size, `mtime_ns` and `ctime_ns`.
Changed, missing or escaped paths fail closed and must be hashed again before
scoring. No original reference bytes, query image, or query vector are kept in
this hot cache. The process retains at most `2 * max_references` vector entries.

This is **not** a Qdrant ANN product image index, nor a cryptographic guarantee
against a privileged filesystem adversary who can forge unchanged metadata.
The first lookup after a process restart still verifies all selected sources;
repeated warm lookups avoid repetitive disk reads and SHA work. End-to-end
latency, multi-angle accuracy, and a calibrated DINO threshold remain separate
real-photo acceptance gates; no product identity or stock is asserted from a
semantic score.

## 13. Embedded-document image coverage and upload preparation (2026-10-27)

The ingestion parser already extracts and stores separate `ImageAsset` content and
`ImageAssetOccurrence` provenance for **PDF, DOCX, and XLSX** pictures, without
inventing user uploads. The missing stage was exposing all these occurrences to
visual source lookup and preparing their embeddings. Ordinary Telegram photo
search now checks active, explicitly confirmed SKU photos first, followed by
all currently ACTIVE parent-document image occurrences (PDF, DOCX, XLSX).
An evidence match shows the parent filename and a grounded locator — PDF page,
DOCX paragraph/table, or XLSX worksheet/cell — **without** treating document
art as an approved product reference, price or stock entry. Source buttons
reference an actor-bound, expiring occurrence ticket and the delivery service
rechecks current original document status and type each time.

The operator prewarm command `python -m office_assistant.scripts.prepare_visual_catalog`
now reports the aggregate `documents` image count across PDF/DOCX/XLSX, not
only `pdf`. With the **same offline model installed and enabled explicitly on
the worker**, successfully processed newly uploaded files prewarm at most 100
source image embeddings per file *after* the parse transaction commits.
This image-cache step is derived/best-effort: the upload remains saved when a
local model is unavailable, and the query path can still compute a missing
vector after model provisioning. Existing documents require the one-time
operator `--apply` backfill. The visual CPU Compose overlay includes app,
worker and Mini App runtimes and is disabled by default.

Limits: the photo search checks at most 500 recent active embedded occurrences;
truncation is shown, so this is not yet an unlimited whole-company ANN index.
Unsupported/ambiguous picture payloads remain unindexed with a parser warning.
No PDF page is invented for DOCX/XLSX pictures. No production or Kali rollout,
real cross-angle accuracy benchmark or Telegram latency acceptance is implied.
