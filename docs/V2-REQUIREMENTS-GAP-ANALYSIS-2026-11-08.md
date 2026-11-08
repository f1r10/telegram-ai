# V2 Requirements Gap Analysis — 2026-11-08

Status: source inspection snapshot of repository `main` at commit `bfd0fd3bcb318e817d1d2d2a3d2384d48dc5337f`. This is a code/document inventory, not a live deployment audit. No runtime Telegram bot, production database or deployed VM was changed or tested during this inspection.

Requirements baseline: `V2-OWNER-REQUIREMENTS-2026-11-08.md`.

## 1. Executive summary

The repository already contains meaningful foundations for documents, internal evidence retrieval, product and sales data, generic reminders, image assets, product-reference photos, external product search and Yandex reverse-image search. Several requested features are therefore extensions, not greenfield work.

The most important verified gaps are:
1. No Orders domain module or order-specific durable sequential workflow was found in the source tree.
2. User administration exists, but per-user per-feature active/inactive permissions were not found in the inspected application model/service surface.
3. Priority website registry and external product search exist, but the registry is currently a bounded ordered domain list with add/remove behavior, not the complete editable capability registry described in the owner requirements.
4. Yandex reverse-image search currently uploads an image, retrieves general Yandex reverse-image results, and ranks returned pages by preferred domain and `.az`. It does **not** send the configured priority domains as a restriction on the reverse-image search request. The UI filters operate on already-loaded results. This does not satisfy the owner's explicit requirement to search those sites as part of the search itself.
5. The current optional Mini App is an HTTPS web client with a separately deployed reverse proxy requirement. It is not the requested short-lived private admin interface and must remain OFF unless separately approved.
6. A general cross-entity relationship layer connecting orders, customers, products, sales, documents and images was not established by the inspected tree; current specific relations (for example product-image confirmation) are narrower.

## 2. Evidence-based component inventory

| Requirement area | Existing repository evidence | Assessment against owner requirement |
|---|---|---|
| User accounts/admin | `application/user_management.py`, `repositories/users.py`, authentication policy and user migration | Account listing/approval/rejection/disable and admin authorization exist. Feature-level binary permissions are not evidenced; add them without replacing role/status auth. |
| Telegram main menu | `integrations/telegram/handlers.py`, `integrations/telegram/messages.py`, `tests/unit/test_telegram_menu.py` | Existing menu already has chat, internal database, product, stock, photo, upload, sales, internet, help and priority-sites entries. New `Sifarişlərim` and per-user menu filtering are not evidenced. Image search currently appears as photo-search flow rather than the exact requested dedicated entry label. |
| Orders | No `application/orders.py`, order repository/domain package or order-specific migration appeared in the inspected source tree | Not implemented as an Orders domain module. Existing generic reminders are not an order registry/workflow/archive. |
| Generic reminders | `application/reminders.py`, `reminders/dispatch.py`, migration `0014_add_reminders.py`, `decisions/0016-deterministic-reminders.md` | Durable generic reminder and delivery foundations exist. Must not assume they already satisfy order-specific schedule, creator-only routing, freeze/resume, archive and order audit semantics. |
| Priority domains | `web_evidence/priority_sites.py`, `docs/PRIORITY-WEB-SITES.md`, `tests/unit/test_priority_web_sites.py`, `tests/integration/test_priority_web_sites_db.py` | Admin-only add/remove, PostgreSQL persistence, audit, maximum 10 domains and priority-first external product/web search are documented/tested. No evidence of per-site product/image capability toggles or reorder/edit UI. |
| External product/web search | `web_evidence/service.py`, `web_evidence/cache.py`, `web_evidence/providers.py`, `tests/unit/test_priority_web_sites.py` | Domain-scoped product/web search exists with fallback and priority ordering. Verify exact live provider behavior and result quality in staging; no production acceptance inferred. |
| External reverse-image search | `web_evidence/image_search.py`, `tests/unit/test_image_web_search.py`, `docs/PRIORITY-WEB-SITES.md` | Yandex reverse-image provider exists, with bounded upload, cache, CAPTCHA/429 stop behavior, URL validation and result parsing. But configured domains are used for ranking after results are fetched, not to constrain the provider's reverse-image query. The result-panel filters are client-side views over the same fetched result. Owner requirement remains unmet. |
| Internal image assets | `processing/image_assets.py`, `image_metadata.py`, `image_ocr.py`, `image_similarity.py`, `application/reference_images.py`, migrations `0015`-`0017` | Image storage/provenance, embedded image extraction, OCR and human-confirmed product reference-image foundations exist. Production cross-angle matching remains benchmark-gated. |
| Document intelligence | ingestion, parser, internal evidence, document search, Qdrant and PDF/DOCX/XLSX/TXT tests; Phase 4-10 roadmap | Significant foundations exist: durable ingestion, parsers, chunks, hybrid retrieval, source citations and secure source delivery. Exact spreadsheet cells, scanned PDFs and historical deferred items still require acceptance according to current docs. |
| Cross-document relations | Product-image relation repositories/services and source locators exist | A general, explainable entity-linking layer across order/customer/product/sales/documents/images is not evidenced. Build a narrow typed relationship service; do not use unconstrained LLM auto-merging. |
| AI conversation/routing | Internal evidence orchestration, web evidence orchestration, Telegram turn context and conversation persistence | Existing bounded routing/context foundations exist. Verify that ordinary-language routing covers all desired workflows and isolates user-specific pending states before declaring the owner experience complete. |
| Admin interface | `miniapp/` package and `V2-OPERATIONS-READINESS.md` | Optional Mini App is documented as default OFF and requires HTTPS hostname/reverse proxy. It is not equivalent to the requested temporary private admin panel. Do not enable it as a shortcut. |

## 3. High-priority discrepancies

### A. Reverse-image search scope

Current flow (source inspection):
1. Upload image to Yandex reverse-image endpoint.
2. Receive a Yandex CBIR identifier.
3. Fetch the `sites` and `similar` result sections without a configured-domain constraint.
4. Parse returned image/page pairs.
5. Mark matches as preferred if the source domain equals or is a subdomain of a configured domain.
6. Stable-sort preferred results first and `.az` results before other non-priority results.
7. Offer local result-view filters over this already-loaded result set.

This is ranking/filtering, not site-scoped reverse-image search. A future implementation must verify provider-supported scoping or issue separate domain-constrained image/page searches. If Yandex's current protocol does not support the requested restriction, report that limitation and use a supported domain-scoped fallback. Do not claim that the provider searched only those sites.

### B. Priority-site registry

Current implementation stores a bounded ordered list in `SystemMetadata`, accepts only public root domains, enforces active-admin changes and writes audit events. Current documented limit is 10 domains. The owner requirements add the ability to edit/reorder and independently enable product-search and image-search per site. Preserve the current validation and audit boundary while extending the model/UI.

### C. User feature permissions

Current user management is role/status administration (active admin, active user, pending/rejected/disabled). It does not equal the newly requested binary per-feature authorization. Implement an allowlist of stable feature keys, default-deny for newly introduced features for ordinary users, admin bypass, and server-side checks on callbacks and services. Decide migration defaults carefully so existing users do not unexpectedly lose currently available functionality during rollout; this requires an explicit rollout policy and tests.

### D. Orders

Build as a new bounded domain service and repository, reusing:
- existing Telegram upload/ingestion and file storage;
- existing audit repository;
- existing worker/lease/outbox patterns;
- existing timezone and reminder delivery primitives where their semantics fit.

Do not overload generic personal reminders with order state. Order lifecycle, documents, creator/requester distinction, reminder schedule and archive need first-class relational records.

## 4. Recommended next engineering actions

1. Inspect current SQLAlchemy models, Alembic head and Telegram callback routing to design additive migrations.
2. Implement per-user feature toggles as a separate authorization policy/service, then apply the policy consistently to menu generation, message handlers, callbacks and domain-service entry points.
3. Implement the Orders data model and application service before Telegram UI. Unit-test workflow and scheduling with a fake clock.
4. Extend the site registry to explicit per-site capabilities and admin ordering.
5. Prototype and benchmark domain-scoped reverse-image search with real requests in a controlled environment. Test provider limitations and rate limits; never bypass CAPTCHAs or anti-bot controls.
6. Add cross-document relationships incrementally, starting with deterministic entity IDs and human-confirmed ambiguous candidates.
7. Keep the optional Mini App disabled. Design the temporary admin access path as a separate security review item.

## 5. Verification limits

This snapshot used GitHub source and repository documentation. It did not:
- run unit or integration tests;
- query a live PostgreSQL database or Qdrant;
- call Yandex or any external search provider;
- connect to the deployed Telegram bot;
- inspect the staging VM;
- verify the current production bot token, deployment commit, backups or worker health.

Therefore statements above describe repository code/docs at the recorded commit, not live operational status.
