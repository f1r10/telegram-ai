# V2 evidence and release gates — 2026-10-19

This is a **code-evidence snapshot**, not a declaration of production readiness.
The owner's unpublished Master Specification is authoritative and is intentionally
not copied into this public repository. Where a real private corpus or staging
environment is needed, automated GitHub CI does not substitute for that gate.

## Delivered and reviewed in code

- PR #54: bounded Mini App HTTP reads, early auth rejection and regression tests.
- PR #55: quiesced PostgreSQL + file storage Restic snapshot CLI, opt-in only.
- PR #56: localhost, privacy-safe Prometheus exporter and tests.
- PR #57: private, offline Docling PDF comparison; production parser unchanged.
- PR #58: admin-only atomic two-location inventory transfer backend, with
  PostgreSQL concurrency and balance-conservation tests; no write UI yet.
- Existing V2: signed Mini App and native Telegram chat, read-only sales and
  stock views, confirmed image references, local PDF OCR and controlled retries.
  Browse the source/tests before claiming any specific feature is approved.

## Not yet accepted — keep issues open

- #40: warehouse registry, units and conversions/refusals, correction/return/
  damage, reservations, preview-confirmed bulk stock import, history, UI and
  WSL restore/parallel acceptance. The backend transfer does not close #40.
- #35: real owner-labeled positive/negative multi-angle photos, calibrated
  recall and false candidates, then opt-in rollout. No semantic similarity
  automatically confirms a product, price or stock quantity.
- #43: Android/iOS Telegram Mini App, dark-mode accessibility, HTTPS/reverse
  proxy, signed launch, file delivery, slow network and multi-user acceptance.
- #32: retry code exists; operator must prove recovery on a backed-up staging
  database, nonzero Qdrant index, original source and no sibling-document harm.
- #11: the original real-user failure reports require live app/worker logs,
  provider status and owner UAT evidence. Synthetic CI cannot close them.

## Final release procedure

1. Run main-branch unit + Ruff + strict Pyright + disposable PG/Qdrant CI.
2. Deploy a matching commit to an isolated, backed-up WSL/Compose environment.
3. Re-run local readiness doctor; SearXNG real JSON, OCR and Qdrant must report
   actual success, not merely live health endpoints or queued jobs.
4. Run the Restic snapshot plus isolated restore and provenance/hash checks.
5. Collect a private labeled, representative image/document corpus and record
   retrieval and product-matching error rates without uploading source files.
6. Perform multi-account Telegram + Mini App Android/iOS UAT and data isolation.
7. Obtain explicit owner acceptance for irreversible inventory and accounting
   changes and only then perform a controlled production cutover.

No live company data was migrated, deleted, repaired or backed up by the
GitHub-only changes above. Operator credentials, tokens and company files
must never be committed to the public repository.
