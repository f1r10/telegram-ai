# Owner Requirements — V2 Extension (2026-11-08)

Status: owner-confirmed requirements baseline. This document records the decisions made in the Telegram project conversation on 2026-11-08. It is a requirements document, not evidence that the features are implemented or production-ready.

Related existing documents:
- `V2-CUSTOMER-FIRST-REBUILD.md`
- `V2-SALES-LEDGER.md`
- `V2-OPERATIONS-READINESS.md`
- `V2-VISUAL-PHOTO-ACCEPTANCE.md`
- `architecture/README.md`

## 1. Non-negotiable architecture rules

1. Preserve existing PostgreSQL data, original uploaded files, image assets, audit history, Qdrant projections and working services. No destructive volume reset or broad rewrite.
2. Implement each business capability as a separate module with explicit application-service boundaries, typed inputs/outputs, its own tests and documented dependencies.
3. Reuse existing modules through adapters where practical. Do not create a second competing Telegram poller, ingestion pipeline, search index or source-of-truth.
4. PostgreSQL is authoritative for structured records and workflow state. Qdrant is a rebuildable retrieval projection. Original documents and images remain in durable object/file storage. The LLM is not a source of truth and must not perform authoritative arithmetic or writes.
5. Long-running parsing, indexing, visual embedding and reminder delivery run in bounded background workers, not inside the Telegram update handler.
6. Every state-changing operation is authenticated, authorized server-side, transactional where applicable, idempotent and audit logged.
7. A feature is not declared complete because a module or unit test exists. Completion requires focused automated tests, integration tests and live Telegram acceptance evidence.

## 2. User and feature access management

### Owner-confirmed behavior

- Admin has access to every system function.
- Admin menu contains a **Users** section.
- Admin selects a user and enables/disables each feature using a simple binary toggle.
- No per-feature read/create/update/delete permission matrix is required.
- A disabled feature is hidden from that user's Telegram menu and is also denied by backend authorization. Hiding a button alone is not authorization.
- Changes take effect on the next request; stale callback buttons must be re-authorized when pressed.
- Permission changes are audit logged.

### Initial feature permission keys

Use stable internal keys, not localized button labels:
- `ai_chat`
- `document_search`
- `document_ingest`
- `product_search`
- `image_search`
- `orders`
- `sales_reports`
- `external_search`
- `inventory` (only if this capability is exposed in the current product)
- `reminders` (only if independently permissioned; otherwise inherited from the owning feature)

The admin user must not be lockable out by an ordinary user-permission toggle. Keep the existing authentication model and admin bootstrap rules unless a reviewed migration explicitly changes them.

## 3. Orders module

### 3.1 Purpose and intake

Add an independent Orders domain module. A user can start an order by sending any supported combination of:
- order document(s);
- image(s) or scans;
- other files;
- typed details and notes.

The intake layer stores original files and provenance, then invokes existing document/image processing services. Extracted values are suggestions until confirmed; OCR or LLM extraction must not silently become authoritative order data.

### 3.2 Sequential guided creation

After the initial material is received, the bot asks the following questions **one at a time**, in this order. It waits for and persists each answer before asking the next question:

1. Order deadline / expiry date and time.
2. How long before the deadline the first warning should begin.
3. Total number of warnings.
4. Person who placed/requested the order.
5. Short description of the order.
6. Additional notes.

After all answers, show a complete review containing the attached materials, extracted/supplied data, deadline and reminder schedule. Only an explicit confirmation creates the authoritative order record.

The workflow must:
- persist its current step and answers in PostgreSQL so it survives a bot or worker restart;
- support cancel, resume and correction without mixing the state with another user or another order;
- reject invalid dates, non-positive warning counts and impossible schedules with a targeted clarification;
- make the final confirmation idempotent;
- retain source file IDs, message IDs, uploader, timestamps and extraction provenance.

Distinguish the Telegram user who created the order (`created_by_user_id`) from the person named as the order requester (`requested_by`); they may be different.

### 3.3 Reminder schedule

Notifications go to the Telegram user who created the order, not automatically to all users or to the named requester.

Let:
- `D` = order deadline;
- `L` = configured lead time before deadline;
- `N` = requested total number of warnings.

For `N > 0`, create `N` evenly spaced warning instants across the lead window, starting at `D - L` and ending one interval before the deadline, with interval `L / N`. Example: a 48-hour lead window and 5 warnings gives an interval of 9 hours 36 minutes; warning times are approximately 48h, 38h24m, 28h48m, 19h12m and 9h36m before the deadline.

Requirements:
- Use the order's explicit timezone; default to the configured company timezone (currently documented as Asia/Baku) and display it in the confirmation.
- Persist each planned delivery and its state. Use a durable outbox/claim-send ledger and idempotency key per order + scheduled instant.
- Do not send duplicate warnings after worker restarts, retries or concurrent claims.
- If a scheduled instant is already in the past when an order is confirmed or resumed, do not send a burst of stale warnings; compute only future eligible warnings and disclose any skipped warnings in the order view.
- Completing, cancelling, deleting or freezing an order stops future deliveries. A frozen order must not send reminders.
- Resuming a frozen order recalculates only future reminders against the existing deadline; it must not replay past reminders.
- Delivery failures must be visible in operational status and retryable under a bounded policy; never silently mark a failed delivery as sent.

### 3.4 Order list, detail and lifecycle

Add **Sifarişlərim** to the Telegram menu. It shows orders created by the current user, with useful filters for active, frozen, completed/archive and overdue states. Admin may have a separate all-orders view.

Opening an order shows:
- order ID and current status;
- original documents and images;
- deadline and time remaining / overdue duration;
- warning lead time, count, planned and delivered warning history;
- creator and named requester;
- short description and additional notes;
- created, updated, completed and frozen timestamps;
- audit/history events.

Order actions:
- **Bitir** — mark completed and move to archive; stop reminders.
- **Dondur** — freeze and stop reminders.
- **Davam etdir** — resume a frozen order and recalculate future reminders.
- **Sil** — remove it from normal active views and stop reminders.

Completed orders are archived, not physically erased. Keep creation/completion/deadline metadata, attached-source references and audit history available for later reporting and indexing. A user-facing delete should use a recoverable/soft-delete state with an audit event; retention/purge policy must be separately approved before any permanent deletion.

Order status values should be explicit and constrained, e.g. `PENDING_CONFIRMATION`, `ACTIVE`, `FROZEN`, `COMPLETED`, `CANCELLED`, `OVERDUE`, `DELETED`. Overdue may be a derived condition rather than a terminal state; do not let it conflict with frozen/completed status.

## 4. Document intelligence and company knowledge

Improve, do not replace, the existing ingestion, parser, retrieval and provenance services.

Separate responsibilities:
- Document Intake and original-file storage.
- Format-specific parsing (PDF, DOCX, XLSX, TXT and supported future formats).
- OCR and scanned-page handling.
- Embedded-image extraction with source locators.
- Structured table/cell extraction for exact spreadsheet facts.
- Lexical + dense/vector + metadata retrieval.
- Document versioning and provenance.
- Cross-document/entity relationship resolution.
- Source delivery back to Telegram.

Core requirements:
- Search the whole internal company knowledge space, not only PostgreSQL rows: structured business data, PDFs, DOCX, XLSX, TXT, saved notes and standalone/embedded images.
- Do not stop at the first relevant source. Gather bounded evidence across relevant source classes and show conflicts instead of hiding them.
- Exact identifiers and numeric spreadsheet questions must resolve to exact rows/cells where available, not merely semantically similar chunks.
- Keep original file, page/sheet/cell/paragraph locator, source hash, ingestion version and timestamps.
- When asked for a source, deliver the actual original file through the existing secure source-delivery path, subject to authorization.
- Report indexed, failed, deferred and unsupported items honestly.
- Re-indexing must be safe, repeatable and not duplicate original records or destroy prior evidence.

## 5. Cross-document and cross-module relationships

The system should resolve shared entities across orders, customers, products, sales, inventory, documents and images.

Examples:
- an order connects to its customer, requested products, quotation, invoice, correspondence and product photos;
- a product connects to its confirmed catalog record, aliases, technical sheets, price evidence and reference images;
- a customer connects to orders, invoices, proposals and correspondence.

Use deterministic identifiers first (confirmed product code, customer ID, invoice/order number, file hash). Use fuzzy or semantic matching only to propose candidate links with confidence and evidence. Low-confidence or conflicting links require human confirmation. Never silently merge distinct customers/products/orders.

Store relationship type, source evidence, confidence, confirmation actor/time and lifecycle state. Relationship search must be bounded and explain why two records are linked.

## 6. Image Intelligence

Keep image handling as a separately testable module integrated with document parsing and product catalog.

Required capabilities:
- accept Telegram images and supported images extracted from PDF/DOCX/XLSX;
- preserve original bytes and SHA-256 identity, with occurrence/source locators;
- OCR visible codes, model numbers and labels where supported;
- index visual embeddings and searchable text/metadata;
- search the internal image corpus and confirmed product reference images;
- support multiple confirmed reference views per product (front, side, packaging, catalogue, etc.);
- return distinct outcomes: `EXACT_MATCH`, `PROBABLE_MATCH`, `SIMILAR`, `NO_MATCH`;
- similarity alone never establishes product identity, price, stock or other authoritative fact;
- explicit human confirmation is required before binding a new image to a product;
- if the exact item is not found but similar items are, say so explicitly and identify the source;
- include evidence and source provenance in results.

Keep any uncalibrated semantic/cross-angle matcher in candidate-only mode until it passes a representative real-image holdout benchmark. Do not claim production-grade visual recognition without measured results.

## 7. External product and image search

Extend the existing external web search rather than creating an unrelated second web stack.

### 7.1 Priority site registry

- Admin can add, edit, enable/disable and reorder priority sites without code changes.
- Registry entries include canonical domain, display name, priority/rank, product-search enabled, image-search enabled, active state and supported adapter/capabilities.
- Search precedence: owner/admin-prioritized sites first, then relevant `.az` domains, then other configured sources. Do not interpret this as permission to send private company data to public sites.
- Existing site-search functionality should be inspected and reused where possible; its exact current capabilities must be verified before claiming completion.

### 7.2 Product search

For a user request that explicitly requires external search, search configured priority sites using site-specific search/API adapters where available, otherwise domain-scoped search queries. Internal company search and external evidence remain separate and are labelled separately.

Result fields when available:
- product image;
- product name and model/code;
- source site and direct product-page URL;
- price, currency and retrieval time;
- technical specifications;
- availability/stock only when the source explicitly states it.

Do not fabricate missing values. Prices and stock are volatile; show source and checked time. Never let an external result overwrite a confirmed internal price or stock fact.

### 7.3 Reverse image search

The owner's requirement is **search scope**, not post-search filtering: image search must be directed at the priority sites/domains as part of the search operation, not run as a generic global image search and merely filter results to `.az` afterward.

- Use reverse-image providers/adapters that actually support image-based lookup and domain/site scoping.
- The supplied Yandex example is a reference for the intended behavior, not proof that every provider exposes an equivalent supported API.
- Where a provider supports image search but not domain scoping, disclose that limitation and use a domain-scoped secondary search path where possible.
- Do not claim that a provider searched a site unless the request/response evidence supports it.
- Search results should show image, source domain, page URL, product label and other metadata only when available.
- External image search is invoked only when the user explicitly requests internet/external image search, consistent with the existing project privacy boundary. Internal image search remains available for internal queries.

## 8. Telegram UX and AI conversation

The bot remains primarily a conversational Azerbaijani office assistant. The user should not need to memorize slash commands.

Main menu should include:
- Sual ver
- Sənəd əlavə et
- Məhsul axtar
- Image Search
- Sifarişlərim
- Satış hesabatı
- Yaddaşım
- Kömək

Render only features enabled for the current user. Admin receives additional **Users**, site registry and administration entries.

The AI conversation layer is an intent/context router and answer synthesizer, not a monolithic implementation of every domain:
- route requests to typed domain services;
- preserve conversational context for follow-up questions;
- keep each user's private conversation state separate;
- share confirmed company knowledge across authorized users;
- prevent a new topic or user from inheriting another user's pending workflow;
- ask targeted clarifying questions when an intent or entity is ambiguous;
- keep external search opt-in/explicit and never disclose proprietary data;
- require a review/confirmation before authoritative writes;
- return concise Azerbaijani answers with evidence and source links/files;
- do deterministic calculations in domain services/SQL, not free-form LLM arithmetic.

## 9. Temporary admin interface — security boundary

Owner does not want a permanent public web application or an always-open web-admin website.

If a browser-based admin interface is used:
- it is launched on demand by an authenticated admin;
- each launch gets a cryptographically random, one-time route/session identifier and short expiry;
- the route name/extension may be non-obvious, but secrecy of the URL is never treated as authentication;
- the session is bound to the authenticated admin and revalidated server-side on every request;
- bind to loopback/private access by default; no direct public WAN port or permanent public hostname;
- no long-lived browser token in URL, localStorage or logs;
- explicit logout/close invalidates the session immediately; expiry and process shutdown also invalidate it;
- CSRF protections, restrictive CSP, no-store responses, bounded requests, audit logging and rate limits are required;
- admin actions remain available through Telegram as a fallback.

The deployment/access method for an admin connecting from a separate phone or computer without exposing a public endpoint remains an implementation decision. Do not expose a reverse proxy/tunnel or public HTTPS endpoint without explicit owner approval. The existing optional Mini App is not automatically approval for a public admin panel.

## 10. Proposed Telegram menu grouping

Main user menu:
- Sual ver
- Sənəd əlavə et
- Məhsul axtar
- Image Search
- Sifarişlərim
- Satış hesabatı
- Yaddaşım
- Kömək

Admin-only:
- Users
- Prioritet saytlar
- Sistem vəziyyəti
- Müvəqqəti admin panelini aç (only if the approved secure access design is implemented)

The visible menu is generated from the actor's current feature toggles and role. Backend authorization is authoritative.

## 11. Implementation sequence and acceptance gates

### Gate 0 — Baseline and safety
- Record current main commit, schema head, active modules, test results and deployment status.
- Inspect actual code, migrations and handlers for each requirement.
- Back up and verify restore procedure before schema changes.
- Create isolated feature branches/test bot token/Compose project and separate test volumes.
- No live-bot cutover during development.

### Gate 1 — Authorization and user menu
- Per-user feature toggle persistence and admin Users flow.
- Backend authorization for every handler, callback and service entry point.
- Tests: disabled menu hidden; forged/stale callback denied; admin always retains access; permission change effective immediately.

### Gate 2 — Orders core
- Additive migrations for order, order-source, workflow-state, reminder schedule/delivery and audit data.
- Sequential durable conversation workflow and final preview/confirmation.
- Tests for each step, restart/resume, cancellation, two users in parallel, duplicate confirmation, invalid dates and source preservation.

### Gate 3 — Order reminders and lifecycle
- Durable worker/outbox, idempotent delivery, pause/resume/complete/delete semantics.
- Fake-clock tests for 48h / 5 warnings (9h36m spacing), missed schedule, retries, restarts, freeze, resume and deadline passed.
- Live Telegram test to creator account only.

### Gate 4 — Orders UI and reporting
- Sifarişlərim list/detail/actions, archive and order history.
- Reports use deterministic SQL and preserve historical completed/deleted records according to retention policy.

### Gate 5 — Document and relationship intelligence
- Verify parser coverage and existing indexing correctness before enhancements.
- Add relationship registry/resolver with candidate confirmation and provenance.
- Acceptance corpus includes exact spreadsheet values, duplicate files, document versions, embedded images and cross-document links.

### Gate 6 — Image search
- Internal multi-source image retrieval, OCR/code matching, confirmed references and confidence-labelled results.
- Real holdout benchmark, negative examples and no-match acceptance before enabling semantic matching broadly.

### Gate 7 — External priority-site search
- Admin-managed site registry and domain-scoped product search.
- Reverse-image search adapters with tested site scoping/capability declarations.
- Acceptance verifies actual request scope, direct source links, checked-at metadata, provider failures and no private-data egress.

### Gate 8 — AI conversation and integration
- Typed intent routing and multi-source evidence synthesis.
- Test cross-module context, ambiguous entities, conflicting sources, permissions, provider outage and parallel user conversations.

### Gate 9 — Temporary admin UI and operational readiness
- Resolve private access path without a permanent public site.
- Security review, expiry/logout tests, backup/restore rehearsal, concurrency, restart and live UAT.

No gate is accepted by documentation alone. Each needs automated evidence plus a recorded manual acceptance result.

## 12. Known gaps and decisions not yet supplied

These must not be silently guessed:
1. Full original Master Specification: current repository notes refer to an owner Master Spec, but the complete source is not present in the repository. The uploaded project note adds company-knowledge and image-search principles but is not a complete master spec.
2. Priority site list: admin-managed registry is confirmed; actual initial domains and ordering still need to be supplied/approved.
3. Admin panel remote access: owner rejects a permanent public website; the secure method for opening it from a separate device without public exposure needs a technical decision.
4. Permanent deletion/retention: user-facing delete should be reversible/audited; retention duration and any eventual hard-purge authority need approval.
5. Frozen order deadline semantics: this baseline assumes the deadline itself remains unchanged while frozen and only reminders pause; confirm if freezing should also extend the deadline.
6. Order creator account lifecycle: define who receives reminders if the creator is deactivated or their Telegram account becomes unreachable.
7. Supported order attachment formats and maximum aggregate size should align with existing ingestion limits and be documented after code inspection.
8. Site adapters and reverse-image provider licensing, API availability, terms and rate limits must be verified before implementation.

## 13. Completion definition

The system is complete only when:
- all owner-approved requirements map to a module, migration/service/handler and acceptance test;
- no module bypasses authentication, authorization, provenance or audit boundaries;
- orders, reminders and archives survive restart and do not duplicate actions;
- internal document/product/image retrieval is grounded and source-traceable;
- external search demonstrably targets configured sites and does not leak company data;
- the Telegram menu reflects user feature toggles and all disabled actions are denied server-side;
- existing production data and original sources remain intact;
- backup/restore, concurrency, failure and live Telegram acceptance checks pass;
- known limitations are documented rather than described as completed capabilities.
