# Mini App — verified Excel stock journal approval (staging only)

Opt in ONLY on an isolated, backed-up server with
`MINIAPP_STOCK_XLSX_ENABLED=true`. The feature remains OFF by default,
independently of normal uploads or Telegram access. It is not a general
Excel ingestion endpoint and must never automatically update inventory
from uploaded documents, LLM answers or image guesses.

An approved active administrator uploads **one** 5MiB-or-smaller .xlsx
to `POST /api/stock/xlsx/preview` as signed binary. Authorization,
origin, Content-Length and MIME are checked BEFORE reading the body.
The bounded parser requires one sheet, five or six exact template
columns, no Excel formulas or external links, at most 200 nonblank rows
and uniquely scoped source references. Unit-managed products require
the six-column canonical-unit template.

The entire approved workbook stays ONLY in bounded process memory.
At most three pending workbooks globally and one per administrator are
allowed; an expiry callback discards the original bytes after 120 seconds
even with no further requests. A restart erases all tickets.
Preview returns every draft row (not only a few examples), type counts
and SHA-256 digest. It does not touch PostgreSQL.

The user must compare all rows to the original and then make a separate
`POST /api/stock/xlsx/confirm` with their opaque ticket. That actor
must still be ACTIVE ADMIN. The ticket is consumed exactly once, then
the identical workbook is re-parsed and SHA-checked before the
existing `InventoryLedgerService.record_batch` acquires canonical
product locks and checks UOM, openings, active reservations, duplicates,
permissions and nonnegative balances. All movements and audits post
in ONE transaction; otherwise none do. HTTP timeouts are ambiguous:
verify stock history before attempting any new upload.

`POST /api/stock/xlsx/cancel` removes the uncommitted actor-owned
ticket, no writes. The operator must privately archive the source
workbook and record audit provenance per retention policy before any
production rollout. This module does not save source bytes as a company
document; the SHA in the audit is only an integrity reference.

Remaining: real staging restore and full browser/mobile UAT, optional
private original-file retention workflow, multi-instance deployment
coordination (tickets are single-process), final owner acceptance of
inventory semantics and production change control. No live data has
been imported with the code-only implementation.
