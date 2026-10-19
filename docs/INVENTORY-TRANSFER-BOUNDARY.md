# Inventory transfer API — controlled backend increment

This increment introduces an explicit admin-only StockTransferDraft and
InventoryLedgerService.transfer method. It is NOT yet bound to a Telegram
or Mini App action, because the employee must first review a clear,
audited preview/confirmation. It does not create new products, locations,
units, or authoritative opening balances.

Both source and destination must have independently confirmed opening
balances. The same active product row lock serializes concurrent writes,
and both balances and movement rows are saved in one PostgreSQL
transaction. Insufficient source balance, a missing destination,
an inactive product, a reused transfer reference and an unauthorized
employee are refused without partial changes. Transfer movement entries
use the existing ISSUE and RECEIPT kinds, a shared xfer-prefixed reference
and one INVENTORY_TRANSFER_POSTED audit event. The existing CHECK constraints
and append-only event model remain intact. No migration or schema rewrite.

This is one component of issue #40, NOT completion of warehouse management.
Explicit warehouse registries, dimensional units/conversion, adjustments,
reservations, returns, damages, XLSX bulk import and human-visible
preview/confirmation must be designed and accepted separately.
A stock total is still UNKNOWN when no confirmed opening exists.
Do not allow an assistant to infer or apply transfers from ordinary chat.

Before production activation: conduct a backup/restore drill and
concurrent Telegram administrator acceptance using a disposable database.
No live warehouse mutation was executed as part of this code change.
