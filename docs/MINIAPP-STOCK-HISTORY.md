## Mini App: reviewed stock movement history

After a signed user requests a **known** product stock balance, the Mini App
offers a contextual "Son anbar hərəkətlərini göstər" button. Only an explicit
tap invokes POST /api/stock/history; the normal stock view does not load
additional history. The server rejects unsigned, wrong-origin and malformed
requests before invoking business code. The current ACTIVE actor is checked
against PostgreSQL on every read; the backend calls the same authorized
InventoryLedgerService.history as native domain consumers.

The endpoint returns at most the 12 latest explicitly confirmed movement rows:
warehouse, RECEIPT/ISSUE/OPENING kind, numeric quantity, confirmed
balance after the movement, documentary reference and UTC timestamp. It does
NOT infer stock from catalog pages, invoices or photos, disclose file paths,
or provide a mutation endpoint. Unknown stock remains distinct from zero.
The UI renders strings through textContent, never source-controlled HTML.

Damage and adjustments are represented by their signed ledger direction and
documentary reference; the associated detailed reason remains in restricted
administrative audit events, not employee-facing text. No warehouse/unit
registry or transfer confirmation UI is invented. Live Telegram mobile,
dark mode, slow-network and role-revocation acceptance remains necessary.
