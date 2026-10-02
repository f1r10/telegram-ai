# ADR 0003: Private Telegram Authentication and Approval

## Status

Accepted and implemented in Phase 2.

## Decision

Use `python-telegram-bot` with local long polling. Telegram numeric user ID is the authoritative external identity; usernames and names are mutable display metadata. V1 supports private chats only and exposes `/start`, `/whoami`, `/users`, and `/cancel`.

The shared access password is configured through `TELEGRAM_ACCESS_PASSWORD`. It is checked with constant-time comparison, never persisted, and never logged. The first successful authentication becomes an active `ADMIN`; subsequent new users become `PENDING` until an active administrator approves or rejects them. Active users authenticate by stored Telegram ID and status, not by asking for the shared password again.

The bootstrap decision is protected by the PostgreSQL `system_metadata` row `bootstrap.admin.initialized`, locked with `SELECT ... FOR UPDATE` inside the authentication transaction. This keeps competing application processes from creating two bootstrap administrators. User mutations and audit events use the same transaction. The callback payload carries only an action and internal target ID; actor role, target status, and transition validity are rechecked server-side.

Webhook infrastructure, public endpoints, FastAPI, and production Telegram deployment are postponed.

## Consequences

- Local development needs no public URL or TLS setup.
- A best-effort bounded in-process failed-password limiter is sufficient for the initial 3-4 user deployment.
- User history is preserved through status transitions and audit events rather than physical deletion.
