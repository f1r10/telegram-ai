# Explicit stock adjustments, returns and damage — backend-only release

The controlled InventoryLedgerService.adjust method accepts only a signed
StockAdjustmentDraft from an active ADMIN in the current PostgreSQL database.

- RETURN: positive source quantity, increases an already-known balance.
- DAMAGE / WRITE_OFF: positive source quantity, decrements an already-known
  balance, never allowing negative stock.
- CORRECTION: signed quantity (+ or -), after a human reviewed physical count.
- All types require a unique per-product reference and a descriptive reason,
  require an existing authoritative opening balance, and append a RECEIPT or
  ISSUE row without changing or deleting earlier ledger movements.
- Each successful adjustment stores a reason and signed delta in an audit event
  referencing the precise appended movement. Replayed references fail closed.
- Product locking serializes concurrent changes and bounds the final balance.
- Reserved adj: and xfer: namespaces cannot be supplied as ordinary manual
  stock receipt/issue references. No invoice or photo implies a stock change.

This is a domain API only. There is no arbitrary-language write route and no
Mini App/admin Telegram preview-confirmation interface yet. No correction is
executed against a production database by merging this code.

Open issue #40 still requires a warehouse registry, explicit units and
conversion/rejection, reservations, safe XLSX imports, previews and audited
human confirmation screens. Both PostgreSQL disposable tests and a backed-up
private staging environment are required before a live operator enables stock
mutations. Until then the API remains an unused internal backend capability.
