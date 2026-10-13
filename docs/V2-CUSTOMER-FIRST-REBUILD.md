# Office Assistant V2 — customer-first rebuild contract

Status: approved direction requested by owner; implementation pending. This document replaces the *interaction design* assumptions of the older slash-command-first Telegram interface, not the requirement to protect existing customer data. Related: #11.

## Success definition
A 3–4-person sales office should work in Azerbaijani through ordinary Telegram conversation: ask about a product, upload PDFs/DOCX/XLSX, identify whether a product photo already exists, create/update confirmed records, and ask year-to-date customer sales. Minimal training; all important actions available through visible buttons, no need to memorize commands.

**Critical distinction:** software features already operational (identity/admin, Postgres, ingestion storage, Qdrant) should not be discarded or their data destroyed solely for a redesign. New V2 services/modules can be written cleanly and introduced behind adapters; create a migration + rollback strategy first. Do not overwrite the active bot during development. Use a separate test token and distinct Compose project/volumes.

## Source research candidates
- https://github.com/Erlaio/rag-telegram-bot — MIT; aiogram + LangChain + FAISS knowledge bot. Adapt RAG answer flow or patterns; not business ledger architecture.
- https://github.com/khurshidnm/telegram-bot-agentic-rag — MIT; PDF/DOCX/XLSX/TXT/MD ingestion, context and admin review. Do not copy unreviewed 'silent training' behavior for confidential company records.
- https://github.com/iamarealhuman-eka/telegram-rag-assistant — document context and follow-up question expansion; verify license before copying.
- https://github.com/infiniflow/ragflow — large document engine; too much operational migration risk for 7-day MVP. Evaluate as post-MVP optional service, not mandatory runtime.

Pin external revisions, verify all licenses and dependencies before incorporating code. Prefer adapting small audited components, not installing several parallel RAG engines.

## Interaction and UX rules
- First screen: branded, clean Azerbaijani welcome, one-sentence explanation and 2x2 buttons: 'Sual ver', 'Sənəd əlavə et', 'Məhsul axtar', 'Satış hesabatı'; secondary 'Yaddaşım', 'Kömək'. No command spam or heavy emoji.
- The chat remains primary: normal typed messages, document/photo and replies automatically carry context. No mode lock requiring /baza or /internet; routing from inferred intent with optional visible mode override. External search only for questions explicitly requiring it; never send proprietary content to search.
- Telegram chat messages: clean bold titles, concise labels, separated sections and inline action buttons. Telegram does NOT offer arbitrary styled HTML/CSS cards inside normal messages; use supported formatted messages and inline keyboards. Optional Telegram Mini App for polished product cards, tables and sales dashboards (mobile-first, Arial or system sans, restrained blue/neutral palette). HTTPS+Telegram initData verification required.
- For long tables, show a short summary and 'Cədvəli aç' Mini App or downloadable XLSX. Do not dump entire spreadsheet into chat when only one product is requested.
- For a photo: 'Şəkli yoxlayıram' -> matched product with code/name/photo/source and status OR 'Etibarlı uyğunluq tapılmadı'. Confirmation buttons before saving as new reference.
- For files: acknowledge received -> show filename/type -> show extraction/indexing status -> 'Hazırdır, indi bu sənəddən soruşa bilərsiniz'. Failures visible, retry safe.
- For proposed writes: concise proposed changes (record type, identifiers, amounts) with 'Təsdiqlə'/'Düzəliş et'/'Ləğv et'. No inferred financial transactions without verified schema/owner approval.
- Responses are in clear Azerbaijani, short first, 'Ətraflı' expandable follow-up, human-readable source references without exposing backend jargon.

## Feature slices — tested end-to-end
1. **General conversation and intent**: 'Salam', 'KA45 varmı?', 'bu sənədi yadda saxla', 'A şirkətinə bu il nə qədər satmışıq?'. OpenAI health and supported model IDs checked at startup; safe human-readable errors instead of generic temporary failure.
2. **Document conversation**: upload PDF/DOCX/XLSX/TXT; durable ingest -> parse -> chunk -> lexical + vector search -> answer with provenance. Numeric/spreadsheet queries must target actual rows, exact IDs and cells, not vaguely relevant large chunks. Show indexed/failed/deferred counts honestly.
3. **Structured product catalog**: confirmed product code, normalized name, description, unit, price+currency+validity, stock (only when imported from authoritative sources), image references. Exact code lookup before approximate/semantic results. Writes audit-logged and preview-confirmed.
4. **Sales facts**: add confirmed invoices/order lines with customer ID, date, product, quantity, unit price, amount, currency, state. Year-to-date aggregation with clear reporting date and currency, voided/cancelled excluded; never treat quote or payment as sale unless the owner explicitly defines the source-of-truth. If no transactions, say no sales facts are available.
5. **Images**: SHA-256 duplicate detection; reference-photo mapping to confirmed catalog; OCR extraction for IDs using a reviewed pipeline and appropriate model; image similarity ranking with confidence thresholds and fallback to unknown. Don't claim visual retrieval exists before benchmarks.
6. **Internet**: SearXNG real JSON search health (not /healthz only), fallback if configured, grounded citations, separate from internal company records.
7. **Roles/privacy**: admin and active user, per-client separation if multi-client ever adopted, Telegram bot token single poller, database/volume backups and restore rehearsal.

## Architecture baseline
Telegram adapter (python-telegram-bot *or* aiogram, select one; do not run both consumers) -> explicit intent router -> 4 domain services (chat-RAG, catalog, sales, document ingest) -> PostgreSQL transactional source-of-truth, Qdrant retrieval projection, object/blob storage, ingestion worker, optional SearXNG. Define typed APIs, deterministic SQL for arithmetic, confidence-scored retrieval, provider abstraction and background job statuses. Local Docker Compose with host ports optional and no collisions with FluentForge.

## Seven-day delivery order / acceptance
D1: V2 isolated branch, known-good API, successful chat+router, Telegram UI design preview, tests.
D2: one PDF+DOCX+XLSX upload and verified exact fact Q&A.
D3: product catalog lookup, document-derived draft + explicit confirmation.
D4: sales records/import and correct annual customer aggregate with unit tests.
D5: photo lookup using tiny labelled demo corpus, unknown case and duplicates.
D6: web search, errors, access, four simultaneous workers/users, restart and recovery.
D7: demonstrations from end-user Telegram accounts, regression, documented limits, deployment cutover only after acceptance.

**Non-negotiable demos**
- Upload QA-price-list.xlsx -> ask 'QA-KA45 neçəyədir?' -> 50 AZN, not full spreadsheet.
- Send new info 'QA-KA45 stok 12' -> preview -> approve -> query -> 12.
- Upload two synthetic invoices for 'A Şirkəti' (this calendar year) -> ask annual turnover -> exact sum and period.
- Send product photo -> existing match with evidence or transparent no match.
- Ask 'bu sənədin zəmanəti nədir?' referencing uploaded PDF -> correct quote/context.
- Ask real internet question -> external links when reachable; actionable error when unavailable.

Every feature PR needs focused automated tests, observable nonsecret status logs, and real Telegram UAT. No dangerous docker prune/down -v. Existing production data preserved and never imported without owner authorization.
