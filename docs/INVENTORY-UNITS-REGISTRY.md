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

The ledger now applies a **conditional unit fence**. When a product has
an approved base unit, every new opening, receipt, issue, warehouse
transfer, adjustment, reservation and batch posting must carry that exact
unit and involve ACTIVE registered warehouses. Legacy products with NO
base-unit approval continue using their old unknown-unit flow; supplying
an alleged unit to an unregistered product is rejected. Consumption of
an existing approved reservation remains tied to its original quantity.

The new six-column XLSX template has explicit `unit` before `reference`.
The old five-column template remains accepted for legacy unknown-unit
products, but cannot alter unit-managed products. Conversion of arbitrary
noncanonical XLSX quantities is NOT implicit; operators must use a
separate reviewed conversion and approve exact canonical quantities.

Telegram chat's old stock-write draft lacks a unit and therefore safely
fails for unit-managed products until an explicit administrator preview/
confirm UI exists. Read-only stock answers display the verified unit or
say it is unconfirmed, never guess. Full warehouse coverage and real
historical unit reconciliation still require backed-up staging acceptance.
Do not infer unit or whole-company stock from catalog descriptions,
photos, invoices or unreviewed Excel.

Run new disposable PostgreSQL tests for unauthorized registration,
conflicting aliases, unit immutability, no conversion guesses,
sub-thousandth precision refusal and historical balance denial.
Separate admin acceptance on real warehouse names is still required.
