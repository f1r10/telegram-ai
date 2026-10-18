# Photo recognition: operator validation and calibration

This is an **offline staging-only acceptance procedure**, not evidence of
commercial recognition quality. The production bot already limits exact
photo identity to a byte-identical, explicitly confirmed **active** product
reference. Near-duplicate dHash/aHash and optional FastEmbed CLIP
cross-angle candidates are **not identities**, confirmed prices, or stock.

The evaluation runner uses the **same conservative production order**:
verified SHA equality → near-duplicate candidates → opt-in local semantic
candidates → abstention. It never creates or changes products, attaches
photos, or persists the uploaded query image or its semantic vector.
Both benchmark labels and operator-owned image files stay on the WSL host.

## Assemble the ground-truth sample

Only use photos you are authorized to process. Include **real phone photos**
captured independently of the original confirmed reference pictures.
For each model/SKU, use front/side/back/packaging/poor-light views,
several cameras and backgrounds, close confusable SKUs, products whose
images are **not** yet in the reference set, and unrelated objects.
A real hold-out view must not be the exact reference bytes, just a
resized version, or a reference used to prewarm the local model.

Create private `/srv/office/visual-acceptance/photos/` with source images.
Create private `/srv/office/visual-acceptance/truth.jsonl`; each line:

```json
{"query_id":"sample01","scenario":"different_angle","photo":"sku01-side.jpg","exact_code":"KA45","similar_codes":[]}
{"query_id":"sample02","scenario":"confusable","photo":"sku02-close.jpg","exact_code":null,"similar_codes":["KA46"]}
{"query_id":"sample03","scenario":"unrelated","photo":"other-object.jpg","exact_code":null,"similar_codes":[]}
```

`exact_code` is the independently verified product code, or null
when the image cannot be identified as an approved catalog product.
`similar_codes` lists visually similar but **not** exact alternatives.
Do not infer truth labels from a prior model answer. Paths must be relative
within the private photo directory, and symlinks are rejected. The file
manifest and image root must not be committed to GitHub.

## Evaluate without network or sensitive output

```bash
# Dry-run: validate data schema and count up to 80 active, explicitly
# confirmed references from the existing PostgreSQL database.
uv run python -m office_assistant.scripts.evaluate_visual_photos \
  --manifest /srv/office/visual-acceptance/truth.jsonl \
  --photo-root /srv/office/visual-acceptance/photos/

# Only after the FastEmbed CLIP model is already installed locally:
uv run python -m office_assistant.scripts.prepare_visual_references --limit 80
uv run python -m office_assistant.scripts.prepare_visual_references --apply --limit 80
uv run python -m office_assistant.scripts.evaluate_visual_photos \
  --manifest /srv/office/visual-acceptance/truth.jsonl \
  --photo-root /srv/office/visual-acceptance/photos/ --apply --limit 80
```

The dry-run **does not** score photos or load an image model; it is NOT a
recognition success. On `--apply`, a missing offline CLIP model fails the
run rather than reporting clean abstention as high accuracy. Neither the
reference nor the query photos are transmitted to AI vendors by this tool.

The CLI prints **only manifest SHA-256, sample/reference counts,
per-scenario aggregate metrics, truncation, and overall aggregate metrics**.
It never prints query IDs, actual product codes or file paths. Primary
metrics: `recall_at_1/3/5`, MRR, **negative_candidate_rate** (an unknown
SKU still got a visual suggestion), and `false_positive_rate` for claims
of exact identity (semantic matches never assert identity). A zero
false-positive *identity claim* rate alone says nothing about false
suggestions: inspect the negative-candidate metric separately. If
`truncated=true`, not all approved reference images were evaluated.
The default 80-reference cap must be declared in any reported results.
Sampling is deterministic and *product-balanced*: the first approved
view of each SKU is preferred before a second view from any SKU, avoiding
one heavily photographed product occupying the entire 80-photo window.
The DB scan is still capped to the 501 most recent references; other
products farther outside this window may remain undiscovered.
A truncated benchmark therefore cannot establish complete catalog recall.

Record the acceptance manifest SHA and model/cache version in the private
operator run book, and compare before/after parameter changes using the
**same private fixed hold-out manifest**. Calibration thresholds must be
chosen from these real positive/negative experiments, not invented from
synthetic CI fixtures. Validate independent multiple angles and negatives
before enabling `VISUAL_SEMANTIC_ENABLED=true` on the WSL staging bot.
Without real data, keep the setting **false**. A user should see possible
candidates only, and must explicitly verify the final product code.
Do not label the recognition rollout complete until real Telegram
Android/iOS photographs pass the manual acceptance suite.

Security note: same-content photo ownership checks deliberately keep
historical inactive-product claims for confirmation conflict prevention.
The visible exact-match lookup additionally excludes inactive SKUs.
This avoids reviving retired catalog items while preserving audit integrity.


## Mini App: one-photo product lookup

The `Şəkillə axtar` mode is a read-only one-photo-at-a-time convenience
view of the same confirmed catalog reference hierarchy used by the native bot.
An ACTIVE Telegram user may submit one **JPEG or PNG**, maximum **4 MiB**.
The loopback Mini App handler enforces canonical HTTPS origin, a current
Telegram launch-data signature and current PostgreSQL actor authorization
before receiving bounded bytes. The application validates real JPEG/PNG
content, fingerprints it, and compares ONLY active explicitly
human-confirmed product references. It prefers exact SHA identity,
then conservative dHash/aHash near duplicates, then optionally the
preprovisioned offline CLIP model for different-angle candidates.
Model queries are transient. There is **no upload staging**, file creation,
catalog write, price/stock write, or on-device persistent photo history
from this operation. The local `VISUAL_SEMANTIC_ENABLED` switch defaults
to **false**. When unavailable, the user sees a truthful fallback.

`/api/photo/lookup` accepts binary JPEG/PNG by signed POST (not a public
photo GET URL) and returns `exact | candidates | not_found | unconfirmed |
unavailable`, a list of **up to three** authorized catalog codes, a
non-authoritative method label and a clear Azerbaijani notice.
An exact SHA means only that the active reference contains the same bytes:
it still does **not** certify stock or price. A candidate can be tapped to
open the existing `Məhsul tap` text-code query, preserving separate
business verification. Test Android/iOS gallery selection, large images,
near matches, other-side product views, unknown items and slow connections
before activating the Mini App on staging.
