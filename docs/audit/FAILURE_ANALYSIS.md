# Failure Analysis — why the current assistant cannot answer the canonical questions

Phase 0 evidence. Method: run the **current, unmodified** routing and normalization code
against the canonical questions from the task brief, and record exactly where each one dies.
No model call is involved, so the result is deterministic and reproducible.

Reproduction script (throwaway, nothing in the repo was changed for it):

```python
from office_assistant.internal_evidence.router import deterministic_plan
from office_assistant.internal_evidence.query_normalization import normalize_user_query, strong_identifiers
```

## 1. Recorded output (before any change)

```
Q: Kamera ucun hansi kabeller var?
   normalized: kamera ucun hansi kabeller var
   ids       : ()
   plan      : GENERAL_CHAT / GENERAL_WORLD / structured=False / documents=False / tools=[]

Q: Plinter ucun kabel lazimdir
   normalized: plinter ucun kabel lazimdir
   ids       : ()
   plan      : GENERAL_CHAT / GENERAL_WORLD / structured=False / documents=False / tools=[]

Q: Printer ucun kabel var?
   normalized: printer ucun kabel var
   ids       : ()
   plan      : GENERAL_CHAT / GENERAL_WORLD / structured=False / documents=False / tools=[]

Q: Which cable fits this printer, who is the supplier in the latest contract,
   and how many are in stock?
   ids       : ()
   plan      : GENERAL_CHAT / GENERAL_WORLD / structured=False / documents=False / tools=[]

Q: Bu mehsulun zemaneti nece ildir?
   plan      : None  (escalates to one LLM planner call; no deterministic route)

Q: HP M404 ucun kabel
   ids       : ('M404',)
   plan      : None  (escalates to one LLM planner call)
```

## 2. Failure modes, with the code that causes each

### F1 — The company's own vocabulary is not recognised as company vocabulary

`deterministic_plan` in [router.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/internal_evidence/router.py#L222-L248)
gates internal routing on a hard-coded marker list:

```
"azn", "mmc", "bizim", "musteri", "menbe", "fayl", "sened", "mehsul",
"bizde", "biz ", "satiriq", "neceye", "qiymet", "markali"
```

None of `kamera`, `kabel`, `printer`, `stok`, `anbar`, `supplier`, `contract`, `stock`
appears. A question whose only company-specific words are product-domain nouns therefore
returns `GENERAL_CHAT`, and `InternalEvidenceService.answer` hands it to
`GroundedAnswerService.answer_general` — **the world-knowledge path**. The assistant then
answers a company inventory question out of general model memory, which is precisely the
hallucination the project forbids (AGENTS.md invariant 3).

Affected scenarios: **K** (camera → cables), **A** (typo `plinter`), **G** (mixed question).

### F2 — No typo, transliteration or alias tolerance

`normalize_user_query` in
[query_normalization.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/internal_evidence/query_normalization.py#L27-L33)
only NFKC-normalizes, casefolds, strips Azerbaijani diacritics and strips punctuation.
`plinter` stays `plinter`. There is no edit-distance correction, no AZ↔TR↔RU↔EN gloss, and
no Cyrillic/Latin folding. A misspelled product word cannot reach the concept it names.

Affected scenarios: **A**, **I**.

### F3 — No concept expansion; no device → connector model

There is no knowledge base linking a *device* to the *connector families* it can need.
Nothing in `src/` models ports (USB-B, RJ45/PoE, HDMI, DC barrel, BNC) or cable ends.
Compatibility is nowhere computable, so the camera/cable question cannot be answered even
in principle from the retrieved catalogue text — the link has to be *inferred* from typed
attributes, which do not exist.

Affected scenarios: **B**, **C**, **K**.

### F4 — No decomposition of a multi-topic question

`EvidencePlan` ([models.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/internal_evidence/models.py#L50-L60))
holds a single `category`, a single `intent`, and up to four tool calls of which the first
match wins. `deterministic_plan` returns on the first `if` that matches. A question mixing
"cable for printer" + "contract supplier" + "stock" has no representation: it is one plan,
not three sub-questions with their own source types. The mixed question above fell through
to `GENERAL_CHAT`.

Affected scenario: **G**.

### F5 — Conflict handling covers money only

`_detect_conflicts` in
[service.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/internal_evidence/service.py#L938-L1000)
matches `(amount|unit_price|line_total|qiymət)` and a currency code. Two dates, two
quantities, two specifications, or an old-vs-new price with no currency token are not
classified as conflict, temporal difference, or anything at all.

Affected scenario: **E**.

### F6 — "Not found" is one flag, not a per-statement confidence

`NOT_FOUND_INTERNAL_AZ = "Bu məlumat daxili sistemdə tapılmadı."` is a whole-turn verdict.
`GroundedModelOutput` ([schemas.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/ai/schemas.py#L6-L10))
has a single `insufficient_evidence` boolean. The three-state epistemic contract the brief
requires — **Təsdiqlənib / Ehtimal / Naməlum** per statement — has no representation, so a
mixed answer (one confirmed fact + one inference + one unknown) cannot be expressed.

Affected scenarios: **C**, **F**, **K**.

### F7 — No entity registry or typed relations beyond products and customers

Only `products`, `product_aliases`, `customers`, `customer_aliases` exist. There is no
generic entity/alias/candidate registry, no `relation(subject, type, object, source,
locator, confidence, valid_from, valid_until, verification_status)` table. Cross-document
linking therefore cannot be persisted or reasoned over; it would have to be recomputed from
raw text on every turn.

Affected scenarios: **B**, **I**, **K**.

### F8 — No temporal facts for relationships

`product_prices` is immutable history, but every other relationship (supplier of a product,
component of a kit, holder of a photo) has no `valid_from`/`valid_until`. "Who is the
supplier **now**" versus "who supplied it in the 2024 contract" cannot be distinguished.

Affected scenario: **E**; part of **G**.

### F9 — No evaluation harness

`src/office_assistant/scripts/benchmark_retrieval.py`, `benchmark_ocr.py` and
`benchmark_visual.py` exist, but there is no corpus, no A–M scenario runner, no
hallucination/evidence-coverage/citation-accuracy metric, and no baseline-vs-after table.
Without that, no improvement claim can be proven.

## 3. What already works and must be kept

These were verified by reading the code and the existing tests, and must not be rewritten:

- deterministic-first routing with one bounded planner call (`router.py`) — the *shape* is
  right; only its vocabulary and its single-plan limitation are wrong;
- the application-issued evidence-ID citation protocol
  (`_validated_synthesis`) — fabricated IDs are already stripped;
- `STRUCTURED_AUTHORITATIVE` / `INTERNAL_DOCUMENT` / `IMAGE_UNTRUSTED` separation;
- manufacturer-independent read-only business tools with actor ACL;
- document-search-after-empty-structured fallback;
- deterministic concise answers for single-field document facts;
- provider failure never crashing a turn (`_deterministic_summary` fallback).

The Global Intelligence Engine **extends** this layer. It does not replace it.

## 4. Prioritised repair order (drives the phase plan)

| Priority | Defect | Phase that fixes it |
| --- | --- | --- |
| 1 | F9 no harness — cannot prove anything | 0 (harness skeleton), all phases feed it |
| 2 | F1 company vocabulary not recognised | 1 |
| 3 | F2 no typo/alias tolerance | 1 |
| 4 | F6 no per-statement confidence | 1 |
| 5 | F4 no decomposition | 1 (plan model), 5 (execution) |
| 6 | F3 no connector/compatibility model | 3 (model) + 5 (reasoning) |
| 7 | F7 no entity/relation registry | 3 |
| 8 | F8 no temporal validity | 3 |
| 9 | F5 money-only conflicts | 5 |

## 5. Scenario → defect map

| Id | Scenario | Defects hit |
| --- | --- | --- |
| A | `Plinter üçün kabel var?` (typo) | F1, F2 |
| B | model doc + spec doc + cable catalogue → which cable | F3, F7 |
| C | printer photo → which cable | F3, F6, F7 |
| D | code/stock on different sheets → how many left | (works today via business tools) |
| E | 1700 old vs 1800 new | F5, F8 |
| F | warranty length, no source | F6 |
| G | 20 unrelated files, three topics in one question | F1, F4 |
| H | AZ question, EN technical doc | (partially works: multilingual embedding) |
| I | `Plinter` / `printer` / `HP M404` identity | F2, F7 |
| J | prompt injection inside a PDF | (invariant 1 already enforced) |
| K | camera → cable families | F1, F3, F6 |
| L | ACL isolation | (works today) |
| M | provider outage mid-query | (works today) |

## 6. Environment honesty

Not available in this sandbox, therefore **not verified live** and never reported as such:
a live Telegram bot token, an OpenAI/Gemini API key, a populated company corpus, a running
PostgreSQL/Qdrant pair on this host, Docker daemon interaction, GPU. Everything built in
this branch is therefore deterministic and verifiable offline; LLM paths are exercised
through fakes, exactly as the existing suite already does.
