# Approved warehouse registry and product units (migration 0022)

This is an **internal, operator-gated registry**, not an automatic migration
of old stock balances and not a production warehouse release. It adds
three independent PostgreSQL tables: warehouse identities, immutable
per-product canonical stock units, and administrator-approved per-product
conversion factors. Nothing in migration 0022 rewrites balances, postings,
reservations or catalog product identities.

Supported exact unit identifiers are `PCS` (piece), `KG`, `L` and `BOX`.
No English/Azerbaijani synonym, machine translation or AI inference may
choose a unit. A declared product base unit is write-once. Registration
**refuses existing opening balances, movements or reservations**, because
legacy numbers have no recorded unit provenance. An existing free-text
warehouse with historical balances cannot silently enter the new
warehouse registry: an owner-supervised reconciliation is needed.

Conversion `from_unit` -> the product's immutable canonical `base_unit`
requires a per-product signed factor. One BOX = 12 PCS for one product
does not authorize 12 PCS per BOX for any other product. Missing ratios,
invalid units, negative/unbounded/non-finite quantities, conversion that
needs more than 3 decimal places and already-declared ratios fail closed.
Registration and ratio declarations are ADMIN-only and generate AuditEvent
rows. Read-only conversions and warehouse listing check ACTIVE users.

Crucially, **this registry is not wired into existing free-text stock
postings**. Those legacy postings have unit-UNKNOWN semantics, and the new
API alone must not imply a finished unit migration. Before enabling
new-unit stock writes: extend the ledger and XLSX template to carry
explicit base units, reject unregistered/inactive warehouse locations
for unit-managed products, expose verified units in Telegram/Mini App
without claiming full company coverage, build admin preview/confirm,
and test concurrent imports and migration on a backed-up staging copy.
Do not infer unit or whole-company stock from product descriptions,
invoices, photos or unreviewed Excel.

Run new disposable PostgreSQL tests for unauthorized registration,
conflicting aliases, unit immutability, no conversion guesses,
sub-thousandth precision refusal and historical balance denial.
Separate admin acceptance on real warehouse names is still required.
