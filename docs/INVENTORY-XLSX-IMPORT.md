# XLSX stock import: verified preview then atomic approval

This package implements a **code-level** preview/commit boundary for up to 200
explicit stock journal rows. It is NOT yet connected to a public Mini App,
free-form Telegram message, AI-generated inventory instruction, or background
file ingestion. Only a trusted administrator action using the signed current
actor may call `commit_stock_xlsx`. No company workbook is ever uploaded to
GitHub, and no parser output is treated as an automatic purchase/sale.

## Accepted XLSX template

Exactly one worksheet. For historic unknown-unit products, the original
five-column format is accepted:

    product_code | warehouse | kind | quantity | reference

Products with a separately approved base unit REQUIRE the explicit
six-column format, and the unit must equal that canonical base unit:

    product_code | warehouse | kind | quantity | unit | reference

No automatic box-to-piece, kilogram-to-piece or other conversion happens
during XLSX posting. A separately reviewed exact conversion is required.

Supported Azerbaijani `kind`: AÇILIŞ, GİRİŞ, ÇIXIŞ. Each row explicitly
states a known product code, warehouse, quantity (maximum three decimal
places) and unique document reference. Rows are applied in worksheet order,
allowing an opening followed by receipts/issues for the same product/location.
No formulas, external links/macros, embedded executables, hidden extra
columns, empty rows or conflicting duplicate references are accepted.
The XLSX is checked for excessive size (5 MiB), uncompressed expansion
(35 MiB), sheet/row counts and unexpected ZIP content. It is never executed.

## Two-phase integrity and approval

1. Show the result of `preview_stock_xlsx(bytes)`, including sample count,
   operation-kind counts and workbook SHA-256. The operator must inspect the
   original workbook and approve that **exact SHA-256**.
2. After explicit approval, pass the original bytes, the confirming SHA and
   current authenticated actor to `commit_stock_xlsx(ledger, actor, bytes,
   confirmed_sha256=...)`. A changed file is rejected before touching DB.
3. The ledger rechecks ACTIVE administrator status, product identities, stock
   baselines, existing references, ACTIVE reservations, nonnegative balances
   and per-row precision. Product row locks are acquired deterministically.
   **All movements and audit entries post in one database transaction** or
   none do. Each audit includes the SHA digest for private source traceability.
   A duplicate batch submission fails closed; no source text enters log output.

The workbook bytes are not persisted by this module. Before a real production
import, the operator must archive the approved original privately under the
company's source-retention policy, link that source through a reviewed audit
workflow, run a backed-up staging rehearsal, and build a Telegram admin
preview/confirm/cancel screen with a short-lived per-admin approval ticket.
Until that UI and source retention are implemented, this is **backend-only**
and no live XLSX import is enabled. No new product, warehouse or unit of
measure may be inferred from the Excel content.
