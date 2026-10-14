# Sales Office V2 — confirmed completed-sales ledger

Additive PostgreSQL table via migration 0018_sales_entries. Not a replacement for existing payment or quotation tables. Real invoice imports from customer PDF/Excel files require a separate reviewed workflow.

## Telegram read and write
An ACTIVE user can ask: Bu il A şirkətinə nə qədər satmışıq? Or open Satış hesabatı and type A Şirkəti | 2026.
An ADMIN can enter: Satış əlavə et: A Şirkəti | 2026-10-01 | INV-001 | 125.50 | AZN.
The assistant previews the invoice, confirms with a separate button, and only then posts it. Pending approvals expire in 10 minutes; repeated taps cannot post twice. PostgreSQL also enforces unique customer/invoice keys; posted entries are audit logged.

Yearly sales SQL includes POSTED invoices from January 1 to December 31, grouped by currency. VOIDED invoices, payments and proposals are excluded. No records means NO CONFIRMED INVOICES FOUND; it does not prove that real sales are zero.

POSTED currently means manually entered and explicitly approved by an administrator, NOT independently authenticated against a customer source document. Separate invoice-import and review capability and a per-item stock ledger remain unfinished.

## Deployment and QA
1. Back up PostgreSQL before changing schema. Never use Docker down -v or prune volumes.
2. Pull merged main, build app and worker, upgrade Alembic to 0018_sales_entries, recreate app and worker.
3. Check Alembic head; use synthetic invoices only while testing.
4. Unit tests: input validation, no implied writes and one-time Telegram confirmation. Real PostgreSQL integration: invoice uniqueness, admin-only writes, per-currency sums, VOIDED exclusion and audit.
5. GitHub CI cannot substitute for manual Telegram tests, production invoice verification or restored-backup rehearsal.
