# Optional approved inventory reservations — backend and recovery boundary

Assumption: reservation, cancellation and consumption are desirable for the
company but exact sales workflow, expiry and warehouse policies are not yet
defined by the private owner Master Spec. Therefore there is **no automatic
Telegram, model or Mini App mutation path**. Only a reviewed, explicit
administrator can call this domain service. Do not activate in production
before confirming operational workflow and running staging migration 0021.

The migration introduces `inventory_reservations` with unique
(product, reservation reference), positive decimal quantity, approved actor
and ACTIVE/CANCELLED/CONSUMED status. It does not migrate or rewrite any
existing `inventory_balances` or `inventory_movements` rows. Product-row
locks serialize all reservations and stock changes; the transaction refuses
reservations that exceed *on-hand minus existing ACTIVE reservations*.

Existing stock ISSUE, warehouse transfer, DAMAGE, WRITE_OFF and signed
negative CORRECTION must preserve ACTIVE reserved quantities. Restocking
and RETURNS can increase stock; they do not automatically create a
reservation. A source product or warehouse without a verified opening
balance cannot be reserved, and an inactive SKU cannot be mutated.

`reservation_action(..., action="CANCEL")` makes reserved quantity
available again without altering physical stock. `action="CONSUME"`
appends exactly one ISSUE stock movement linked by a distinct `res:`
reference and marks that reservation CONSUMED in the same transaction.
A replay of the same completed action returns the existing result;
cross-action replay and a duplicate reservation identifier are rejected.
Earlier ledger rows remain unchanged. Every successful transition is
persisted in actor-linked audit events without customer data in logs.

No reservation policy expiration, automatic allocation, employee self-
service or reservation-based invoice fulfillment is assumed. The user's
existing stock query still denotes **on-hand verified quantity**, not
"available to sell"; future UI must present reserved and available
separately before an operator exposes reservations to employees.

Tests use disposable PostgreSQL with concurrent reservation attempts,
idempotent cancellation/consumption, insufficient balance, reserved-stock
protection against ISSUE/TRANSFER/WRITE_OFF, role denial and immutable
movement audit. Before deploying migration: backup/restore on isolated
staging, then confirm policy and admin preview/commit/cancel workflow.
Do not silently change live stock from file, image, invoice or text.
