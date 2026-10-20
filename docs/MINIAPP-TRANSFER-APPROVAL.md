# Mini App transfer preview → explicit approval (administrator only)

The first irreversible warehouse UI is deliberately **OFF BY DEFAULT**.
Only an operator on a backed-up, isolated staging server may set
`MINIAPP_STOCK_TRANSFER_ENABLED=true`, independently of `MINIAPP_ENABLED`.
Do not turn it on in a production office without company approval and
a verified isolated restore.

The administrator-only card is shown only when `/api/me` reports an
ACTIVE current PostgreSQL administrator AND the dedicated stock-write
feature flag is enabled. The browser cannot set a product's balance
directly. It sends a complete proposed draft to a signed JSON API:

1. `POST /api/stock/transfer/preview` — actor checked against DB, six
   typed fields validated; both warehouses must already have approved
   opening balances. A 120-second opaque server-only ticket is returned.
   PREVIEW makes no database writes. Displayed projected balances may
   become outdated as another staff member edits stock.
2. `POST /api/stock/transfer/confirm` — same signed, currently ACTIVE
   ADMIN identity required; one-time ticket is consumed under lock BEFORE
   the authoritative inventory transfer. The domain service then acquires
   PostgreSQL locks and rechecks product activity, warehouse status, unit,
   reservations, nonnegative stock and duplicate document number in one
   transaction. Nothing is posted if any check fails.
3. `POST /api/stock/transfer/cancel` — discards the caller's unused
   ticket without modifying stock.

Tickets are scoped to current actor IDs, expire after two minutes, are
not persisted across service restarts, and cannot be submitted twice.
A timeout or network failure after confirm is AMBIGUOUS: the UI must
not automatically replay the transfer. The administrator must query
stock history and the document reference before making another attempt.
A new reference is required for any genuinely new operation.

The HTTP layer rejects unsigned requests, cross-origin JSON posts,
unrecognized routes, malformed fields and oversized bodies before any
business code. The Mini App interface inserts all returned text using
`textContent`, not HTML. Server-side audit and movement rows use the
existing inventory service. No write is allowed via an LLM answer or
arbitrary Telegram chat text.

**Outstanding gates**: private staging end-to-end Telegram device UAT,
HTTPS proxy checks, accidental double taps on slow networks, baseline
warehouse/unit reconciliations, administrator preview-confirm flows for
adjustments/reservations/XLSX (separate workflows), and owner acceptance.
This is one isolated transfer slice, not completion of #40 or #43.
