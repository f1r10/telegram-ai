# Target Architecture — Global Intelligence Engine

Status: Phase 0 design, adapted to the measured baseline
([BASELINE_AUDIT.md](../audit/BASELINE_AUDIT.md), [FAILURE_ANALYSIS.md](../audit/FAILURE_ANALYSIS.md)).

This document describes **where the code goes**, not only what it should do. Every section names the
existing module it extends and the new module it adds, so the design is auditable against the repo.

---

## 1. Design constraints taken from the audit

The audit produced nine measured defects (F1–F9 in `FAILURE_ANALYSIS.md`). They drive every decision
below:

| Audit finding | Architectural consequence |
| --- | --- |
| F1 company vocabulary not recognised (`internal_markers` in [router.py](../../src/office_assistant/internal_evidence/router.py)) | Routing must be a **first-class, testable component** with an explicit vocabulary and a documented fallback, not a tuple of substrings inside a fast path. |
| F2 no typo/transliteration/alias tolerance | Query understanding must be a **separate, benchmarked stage** in front of routing. |
| F3 no concept expansion (device → connectors) | Compatibility must be computed from **typed attributes**, never from prose similarity. |
| F4 no decomposition of multi-topic questions | `EvidencePlan.category` is a single value; the target needs **sub-questions each with their own source types**. |
| F5 conflict handling is money-only | Conflicts become a **typed, general** classification (kind, dates, reliability), not a price comparison. |
| F6 "not found" is one turn-level flag | Confidence must be **per statement**, not per answer. |
| F7 no entity registry beyond products/customers | Entity resolution needs **canonical entities, aliases, candidates, and provenance**. |
| F8 no temporal facts | Relations need **`valid_from` / `valid_until`**. |
| F9 no evaluation harness | The harness is built **before** the engine (Phase 0 exit criterion). |

Two further constraints come from `AGENTS.md` and §1.1 of the task brief and are non-negotiable here:

* Retrieved text, documents, web pages and image text are **untrusted data**, never instructions.
* The LLM never performs a side effect and never produces free-form SQL.

---

## 2. Where the engine lives

A **modular monolith package inside the existing application**. No new service, no new broker.

```text
src/office_assistant/
  global_intelligence/            <- NEW package (the engine)
    understanding/                <- query understanding (F2, F3, F4)
    planning/                     <- QueryPlan + planner (F1, F4)
    retrieval/                    <- adaptive retrieval + source adapters
    entities/                     <- entity resolution (F7)
    relations/                    <- relationship + compatibility reasoning (F3, F8)
    evidence/                     <- unified Evidence model + aggregation
    reasoning/                    <- conflict, temporal, multi-hop, verification (F5, F6)
    answering/                    <- grounded answer assembly + confidence labels
    providers/                    <- protocol definitions and adapters
    flags.py                      <- feature flags for every new path
    engine.py                     <- GlobalIntelligenceEngine orchestrator

  internal_evidence/              <- EXISTING, preserved and extended
    router.py                     <- kept; becomes the deterministic fast path of the planner
    models.py                     <- kept; gains confidence-label types
    service.py                    <- kept; InternalEvidenceService remains the document/structured path
    query_normalization.py        <- kept; extended, not replaced
    document_search.py            <- kept
    delivery.py                   <- kept (file delivery)
    benchmark.py                  <- kept (Phase 10 routing benchmark)

  business_tools/                 <- EXISTING, preserved
    registry.py                   <- the 13 allow-listed read-only tools; the engine calls these, nothing else

  knowledge/                      <- EXISTING, preserved
    retrieval.py / qdrant_store.py / embeddings.py / chunking.py / indexing.py

  processing/                     <- EXISTING, preserved
    parsers.py / pdf_ocr.py / image_ocr.py / vision.py / ...

  ai/                             <- EXISTING, preserved
    provider.py (LanguageModel, VisionLanguageModel) / schemas.py / service.py
```

The engine is **additive**. Nothing in `internal_evidence`, `business_tools`, `knowledge`,
`processing`, `integrations` or `miniapp` is deleted or rewritten. The engine calls into them.

---

## 3. Pipeline and module mapping

```text
TELEGRAM USER
  -> integrations/telegram  (existing) + auth/ (existing)  [UNCHANGED]
  -> global_intelligence/engine.py :: GlobalIntelligenceEngine.answer()
       1. understanding/          -> QueryUnderstanding  (normalize, typo/alias, language, intent, entities, sub-questions)
       2. planning/planner.py     -> QueryPlan (Pydantic)  [deterministic fast path first: internal_evidence.router.deterministic_plan]
       3. retrieval/              -> parallel, filtered, budget-limited fan-out
             document      -> internal_evidence.document_search + knowledge.retrieval (existing)
             structured    -> business_tools.registry (existing, allow-listed, ACL-filtered)
             image         -> processing.semantic_visual + vision/OCR providers (existing + Phase 4)
             relations     -> relations/ + PostgreSQL (Phase 3)
             memory        -> memory/ (Phase 6)
             web           -> web_evidence/ (existing, always separated)
       4. entities/               -> EntityResolver (canonical + aliases + candidates)
       5. relations/              -> RelationshipAnalyzer (compatibility from typed attributes)
       6. evidence/               -> EvidenceAggregator -> EvidenceBundle (existing type, extended)
       7. reasoning/conflicts.py  -> ConflictClassifier (typed, dated, reliability-aware)
       8. reasoning/engine.py     -> multi-hop, constraint checking, missing-information detection
       9. reasoning/verification.py -> claim -> source check, ACL re-check, downgrade unsupported claims
      10. answering/              -> GroundedAnswer with Təsdiqlənib / Ehtimal / Naməlum per statement
  -> integrations/telegram  (existing)  [UNCHANGED]
```

### 3.1 Stage contracts

| Stage | Input | Output (Pydantic/dataclass) | Deterministic fallback when the provider fails |
| --- | --- | --- | --- |
| Understanding | raw message + conversation context | `QueryUnderstanding` | rule-based normalization + `normalize_user_query` (existing) |
| Planning | `QueryUnderstanding` | `QueryPlan` | `internal_evidence.router.deterministic_plan` (existing) |
| Retrieval | `QueryPlan.sub_questions` | `list[RetrievedItem]` | skip the failing source, record `source_unavailable` |
| Entity resolution | mention strings | `list[EntityCandidate]` | exact identifier match only |
| Relations | entity ids | `list[RelationCandidate]` | attribute join only, no inference |
| Evidence aggregation | retrieved items | `EvidenceBundle` (existing type) | unchanged |
| Reasoning | `EvidenceBundle` | `ReasonedAnswerDraft` | deterministic summary (existing `_deterministic_summary`) |
| Verification | draft + bundle | `VerifiedAnswer` | downgrade every unverifiable claim to `Naməlum` |
| Answering | verified answer | Telegram text (AZ) | existing not-found / clarification wording |

**Every stage degrades to the previously shipped behaviour.** That is what makes the feature flags
safe.

---

## 4. The epistemic contract

Non-negotiable, from §4.1 of the task brief. Modelled once, in
`global_intelligence/evidence/confidence.py`:

```python
class StatementStatus(StrEnum):
    CONFIRMED = "tesdiqlendi"   # Təsdiqlənib  - identified source + locator
    PROBABLE  = "ehtimal"       # Ehtimal      - reasoned or weakly identified, basis stated
    UNKNOWN   = "namelum"       # Naməlum      - no evidence found
```

Rules enforced in code, not by prompt alone:

1. `CONFIRMED` requires `evidence_id` **and** a non-empty `locator`. The verification stage rejects
   any statement claiming `CONFIRMED` without both (`Evidence without a known source/locator can
   never be shown as confirmed fact`, §13.1).
2. "not found" wording is preserved verbatim from the existing `NOT_FOUND_INTERNAL_AZ` constant; the
   engine never emits "does not exist".
3. A statement whose evidence is `IMAGE_UNTRUSTED` can be at most `PROBABLE`.
4. Web evidence is labelled as external in the answer and is never merged silently with internal
   facts (§4.1, `AGENTS.md` invariant 4).

---

## 5. Feature flags

Each new path is switchable so the shipped behaviour can be restored instantly (§18 "Use feature
flags").

```python
# global_intelligence/flags.py
class EngineFlags(BaseModel):
    engine_enabled: bool = False                 # master switch; False = today's behaviour
    query_understanding_enabled: bool = False    # F2/F3/F4 fix
    concept_expansion_enabled: bool = False      # F3
    decomposition_enabled: bool = False          # F4
    entity_resolution_enabled: bool = False      # F7
    relations_enabled: bool = False              # F3/F8
    conflict_classifier_enabled: bool = False     # F5
    per_statement_confidence_enabled: bool = False  # F6
    verification_enabled: bool = False
```

Default `False` for every flag. A phase turns its own flag on only after the harness shows the
scenario moved from fail to pass, and records the numbers in
`docs/evaluation/BENCHMARK_RESULTS.md`.

Flags are read from `Settings` ([core/settings.py](../../src/office_assistant/core/settings.py))
using the same env-var mechanism already used by every other option, so no new configuration
concept is introduced.

---

## 6. Data model changes (Phase 3+, additive only)

New PostgreSQL tables, added by **new** Alembic revisions. No existing revision is edited and no
column is dropped (§1.1 prohibition).

| Table | Purpose | Key columns |
| --- | --- | --- |
| `entities` | canonical entity registry | `id`, `entity_type`, `canonical_name`, `normalized_name`, `created_at` |
| `entity_aliases` | observed surface forms | `entity_id`, `alias`, `normalized_alias`, `source_id`, `locator`, `confidence` |
| `entity_candidates` | unmerged mentions awaiting review | `mention`, `proposed_entity_id`, `score`, `reason`, `status` |
| `entity_relations` | typed relations | `subject_id`, `relation_type`, `object_id`, `source_id`, `locator`, `extraction_method`, `confidence`, `valid_from`, `valid_until`, `verification_status` |
| `entity_relation_evidence` | provenance for each relation | `relation_id`, `evidence_id` |
| `query_traces` | query execution traces (observability + harness) | `trace_id`, `user_id`, `plan_json`, `source_usage_json`, `latency_ms`, `created_at` |

`verification_status` is constrained to `confirmed | inferred | rejected` (§9). **No relation row is
insertable without a `source_id`** — enforced by a `NOT NULL` constraint plus a repository-level
guard, so the invariant is structural rather than conventional.

Qdrant remains a rebuildable projection. The existing rebuild path is reused; Phase 3 adds no new
collection without a documented migration and rollback.

---

## 7. Provider abstraction

`§7.1` protocols in `global_intelligence/providers/`. The existing
[ai/provider.py](../../src/office_assistant/ai/provider.py) `LanguageModel` / `VisionLanguageModel`
are **used as-is**; the new protocols are additions, not replacements:

```python
class LLMProvider(Protocol): ...                 # satisfied by existing LanguageModel
class EmbeddingProvider(Protocol): ...           # satisfied by existing FastEmbedTextAdapter
class DocumentIntelligenceProvider(Protocol): ...  # NEW
class VisionProvider(Protocol): ...              # NEW
class OCRProvider(Protocol): ...                 # NEW
class RetrievalProvider(Protocol): ...           # NEW
class KnowledgeGraphProvider(Protocol): ...      # NEW (PostgreSQL-native implementation)
class MemoryProvider(Protocol): ...              # NEW
class WebSearchProvider(Protocol): ...           # satisfied by existing web_evidence providers
```

Common policy, implemented once in `providers/base.py` and shared: timeout, retry with backoff,
circuit breaker, health check, concurrency limit, graceful degradation.

**A provider failure must never crash the bot** (§7.1, `AGENTS.md` invariant 8). The engine wraps
every source call; a failure records `source_unavailable=<name>` on the bundle and the answer says
honestly which source was unavailable.

---

## 8. What is deliberately NOT built

Per §7 "no vanity components":

* **RAGFlow** — evaluated only, in an isolated `eval` Compose profile. It would duplicate the
  existing PostgreSQL + Qdrant system of record. Decision recorded in
  [OPEN_SOURCE_INTEGRATION_MATRIX.md](OPEN_SOURCE_INTEGRATION_MATRIX.md).
* **LightRAG / Graphiti** — benchmark-gated. The PostgreSQL-native graph (§9 of the brief) is built
  first; these are only adopted if the multi-hop/temporal benchmarks prove it insufficient.
* **No Redis, no Celery** — the `jobs` table with `FOR UPDATE SKIP LOCKED` already covers background
  work (`AGENTS.md`).
* **No vector database replacement**; Qdrant stays.

---

## 9. Traceability: audit defect → phase → scenario

| Defect | Phase | Evaluation scenario | Flag |
| --- | --- | --- | --- |
| F1 routing vocabulary | 1 | A, K | `query_understanding_enabled` |
| F2 typo/alias | 1 | A, I | `query_understanding_enabled` |
| F3 concept expansion / connectors | 1 + 3 | K, B, C | `concept_expansion_enabled`, `relations_enabled` |
| F4 decomposition | 1 | G | `decomposition_enabled` |
| F5 conflicts | 5 | E | `conflict_classifier_enabled` |
| F6 per-statement confidence | 1 + 5 | F, K | `per_statement_confidence_enabled` |
| F7 entity registry | 3 | B, I | `entity_resolution_enabled` |
| F8 temporal facts | 3 + 5 | E | `relations_enabled` |
| F9 evaluation harness | 0 | all | n/a (harness itself) |

Scenarios C, D, H, J, L, M are covered by Phases 4, 5, 2, 7, 7, 7 respectively.
