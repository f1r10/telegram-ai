# Staged Mini App approvals for reservations and stock adjustments

The new administrator screen is independent of transfer approval and is
disabled unless `MINIAPP_STOCK_ACTIONS_ENABLED=true`. It is NOT intended
for production deployment without isolated staging restore and owner signoff.

An ACTIVE current database administrator can propose:
`RESERVE`, `RETURN`, `DAMAGE`, `WRITE_OFF`, or signed `CORRECTION`.
The same opt-in screen now offers `RESERVATION_CANCEL` and
`RESERVATION_CONSUME` for **existing ACTIVE reservations**, using only
the exact product code and original reservation document reference.
All variants use the dedicated `/api/stock/action/preview`,
`/api/stock/action/confirm`, and `/api/stock/action/cancel` routes.

The preview validates the explicit product, warehouse, quantity (at most
three decimals), unit, document number and 10–300-character reason on
adjustments. It reads the opening balance without editing the database
and returns a two-minute opaque one-use ticket bound to the actor ID.
It does not infer reserved/available stock and does not promise success.
One actor may hold at most three open tickets; global cap is 32.

Confirmation consumes the ticket before invoking the existing privileged,
audited PostgreSQL domain service. That service alone checks the current
role, canonical unit and registered warehouse (where applicable),
opening balance, reserved stock, duplicate documents, product activity and
transaction-safe balance. A failed/ambiguous confirmation is not silently
retried. An operator verifies inventory history before initiating another
request. Cancel discards an uncommitted ticket with no stock writes.

No free-form LLM response, photo match, uploaded workbook, or ordinary
employee access is allowed to mutate inventory through these APIs.
Existing reservations can be consumed or cancelled via the domain
service, but operator-facing approval for those finalizing actions is
still separately gated. Excel bulk source audit and byte-exact preview
approval are also still not connected to the Mini App.

The browser UI only displays values from the signed response with
`textContent`; server rechecks auth on EVERY step. Both server and
client have bounded payloads, invalid-input errors and no automatic
posting from file ingestion.

Remaining acceptance: disposable PostgreSQL complete workflow with
real Telegram mobile UI, private test photos, staged restore, owner policy
for reservation expiration, approval UI for reservation cancellation/
consumption, and XSLX journal review.
