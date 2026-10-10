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

## OCR extraction benchmark (Phase 12B)

OCR text extraction is a **separate evaluation task** from final product identity.
Different OCR engines or preprocessing variants can produce private JSONL candidate lists.
The evaluator applies only approved deterministic code normalization; it does not
read pictures, OCR them automatically, autocorrect codes, or promote any inferred
code to authoritative product identity.

Each **ground-truth** record must be human-annotated, with the exact fields:

    {"query_id":"example-1","scenario":"partial_model_code","visible_codes":["KA45"]}
    {"query_id":"example-2","scenario":"unrelated_item","visible_codes":[]}

Each **prediction** record must have exactly:

    {"query_id":"example-1","extracted_codes":["KA-45"]}
    {"query_id":"example-2","extracted_codes":[]}

The permitted scenario labels are:
`different_angle`, `different_background`, `phone_photo`, `weak_light`,
`crop`, `packaging`, `similar_models`, `partial_model_code`,
`pdf_screenshot`, `catalog_vs_photo`, and `unrelated_item`.
These fixed scenario identifiers avoid accidental disclosure of company names.
If a new scenario is needed, define a reviewed non-sensitive label in code and docs.

From the repository root:

    uv run --no-sync python -m office_assistant.scripts.benchmark_ocr --truth var/benchmark/private/ocr-truth.jsonl --predictions var/benchmark/private/ocr-model-a.jsonl --output var/benchmark/private/ocr-model-a-metrics.json

The output includes aggregate micro-averaged code precision, recall and F1,
query exact-set accuracy, image code hit rate, and false-alarm image rate,
with the same metrics broken down by the fixed scenario labels. Empty-denominator
metrics are `null`. `KA4S` is not silently corrected to `KA45`.
OCR predictions must enumerate candidate product codes observed in text; they
must not claim the entire photographed object is a verified product.
The evaluator prints input digests and aggregated statistics only, not product codes
or query identifiers. It never writes back to the authoritative business database.

Run OCR extraction with one or more locally installed candidate engines on an
owner-approved, private corpus, then submit their candidates in the above format.
**No OCR engine, trained model, benchmark score, or accuracy threshold has yet been
selected or demonstrated by these fixture-only tests.**

## Optional local Tesseract candidate runner

A non-production, opt-in **local** OCR experiment runner is provided. It requires a
separately installed Tesseract executable with the selected language packs; the
application container and production dependency graph are unchanged. The Tesseract
project is distributed under Apache-2.0; check your particular installation's
package and language-data licenses before enterprise use. This is one benchmark
candidate, not a selected final OCR solution.

Keep a private image directory and a private product-code allowlist outside Git.
The allowlist contains one existing, approved product code per line. Do not include
customer identifiers, secrets, or unrelated documents. The experiment must run in
a trusted, isolated, network-restricted local environment: Tesseract parses
potentially untrusted images, and this wrapper is **not** an OS sandbox.

The image manifest uses strict JSONL records and relative PNG/JPEG paths:

    {"query_id":"example-1","image":"products/front/01.jpg"}
    {"query_id":"example-2","image":"products/other/02.png"}

The runner rejects absolute paths, drive-qualified paths, traversal, symlinks
outside the selected root, missing/empty images, and images over 25 MiB. It runs
one local OCR request at a time with a bounded timeout and never relays raw OCR
text into logs. It checks recognized text **only** against canonicalized codes
from the private allowlist; no similar-code substitution or auto-product creation
is allowed. The output is a private JSONL predictions file using
`query_id` and `extracted_codes` fields.

    uv run --no-sync python -m office_assistant.scripts.run_local_ocr --images var/benchmark/private/images.jsonl --image-root var/benchmark/private/images --codes var/benchmark/private/product-codes.txt --output var/benchmark/private/tesseract-predictions.jsonl --languages eng --timeout-seconds 30

If Azerbaijani trained data is installed and relevant, try `--languages eng+aze`
as a separate benchmark configuration. Never silently fall back to another
language. No OCR engine is downloaded automatically. The output contains private
product codes and query IDs: **never commit it to a public repository**.

Evaluate output with the OCR scoring command above, using a separate verified
`ocr-truth.jsonl`. Record the exact Tesseract version, installed language packs,
CPU, runtime, preprocessing configuration and any failure cases in the private
benchmark record. Compare competing OCR candidates on the same data before
selecting a production OCR engine.

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
