# Phase 12B — Offline visual benchmark evaluation

**Status:** evaluation infrastructure only. No OCR or visual embedding model is selected or deployed.

This is the evaluation stage of Master Spec sections 22.12 and 30. It scores ranked
product-code predictions from competing OCR / visual retrieval experiments against
owner-annotated ground truth. It does not open images, load models, call external
providers, or mutate PostgreSQL/Qdrant.

## Private data policy

Keep photos, catalogs, annotations and predictions **outside Git**. A suitable local
directory is var/benchmark/private/ (ignored by Git). Do not commit the private
datasets or model outputs. The CLI prints only aggregate metrics and SHA-256 hashes
of input manifests, never product codes or individual query IDs.

## Ground-truth JSONL

One JSON object per line with exactly these keys:

    {"query_id":"example-1","scenario":"different_angle","exact_code":"KA45","similar_codes":["KA46"]}
    {"query_id":"example-2","scenario":"unrelated_item","exact_code":null,"similar_codes":[]}

The exact code must be verified by a human. Null means no confirmed exact identity.
Similar codes refer to different, visually similar products.

Recommended scenarios: different_angle, different_background, phone_photo,
weak_light, crop, packaging, similar_models, partial_model_code,
pdf_screenshot, catalog_vs_photo, unrelated_item. Include negatives.

## Prediction JSONL

    {"query_id":"example-1","status":"PROBABLE_MATCH","ranked_codes":["KA46","KA45"]}
    {"query_id":"example-2","status":"NO_MATCH","ranked_codes":[]}

Allowed statuses: EXACT_MATCH, PROBABLE_MATCH, SIMILAR, NO_MATCH.
A non-NO_MATCH status requires at least one ranked candidate. Do not claim
EXACT_MATCH based only on visual-vector similarity. Identifiers and product
codes use the existing deterministic normalization, never fuzzy identity.
Extra fields, duplicate canonical codes and missing query IDs are rejected.

## Execute offline

Run from repository root with uv:

    uv run --no-sync python -m office_assistant.scripts.benchmark_visual --truth var/benchmark/private/truth.jsonl --predictions var/benchmark/private/model-a.jsonl --output var/benchmark/private/model-a-metrics.json

Run once per OCR/visual model, or per fusion configuration, against the
same annotated truth set. Version all private evaluations.

## Metrics and denominators

- recall_at_1, recall_at_3, recall_at_5: fraction of exact-labeled cases
  with the correct code in the first 1/3/5 candidates.
- mrr: mean reciprocal rank of annotated exact codes.
- exact_identity_precision: correct top-one EXACT_MATCH declarations divided
  by all EXACT_MATCH declarations.
- false_positive_rate: no-exact cases incorrectly declared EXACT_MATCH or
  PROBABLE_MATCH divided by all no-exact cases.
- similar_recall_at_5: fraction of similar-only cases with any annotated
  similar code in the first five. This is not an exact identity metric.

Undefined denominator metrics are reported as null.

## Remaining acceptance gates

1. Assemble a private, versioned, representative dataset with human-approved
   labels and documented usage rights.
2. Benchmark OCR model-code extraction and permissively licensed visual
   embeddings on that dataset, including memory, latency and accuracy.
3. Compare OCR-only, visual-only and combined ranking, measuring similar-product
   false positives and performance by scenario.
4. Document versioned results, failure cases, and a justified model decision.
5. Only then start Phase 12C integration.

No real ranking score or model recommendation is claimed yet: no private
annotated corpus or actual candidate model outputs are available here.
