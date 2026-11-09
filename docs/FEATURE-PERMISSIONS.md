# Per-user feature permissions (owner contract V2 §2, §6)

Reconciled 2026-11-09. This document describes what is implemented on
`feature/v2-owner-requirements-2026-11-08`, not a target state.

## What it is

An administrator approves a binary grid of capabilities per user. The grid is the only
place where "may this person use X" is decided, it is read on every turn, and a change
takes effect on the next request without a restart.

Ten keys exist (`office_assistant/auth/features.py::FeatureKey`):

| Key | Capability it opens | Baseline for an existing approved user |
| --- | --- | --- |
| `ai_chat` | free-text question answered from internal evidence | enabled |
| `document_search` | «Bizim baza» scope, indexed document retrieval, original-file delivery | enabled |
| `document_ingest` | sending a file, photo or note into the pending session and `/saxla` | enabled |
| `product_search` | structured product/price lookup | enabled |
| `image_search` | photo lookup, reference-image save and revoke | enabled |
| `orders` | the order domain | **disabled** (the domain itself is Milestone 2) |
| `sales_reports` | sales draft and report turns | enabled |
| `external_search` | `/internet`, `/tamaxtar`, Yandex reverse-image web fall-through | enabled |
| `inventory` | stock question turns | enabled |
| `reminders` | reminder create/confirm/list/cancel | enabled |

Baseline means *what happens when no row exists*, and it is also exactly the grid that
`0025_user_feature_permissions` writes for every existing user and that
`UserManagementService.approve()` writes inside the approval transaction for a new one.
A missing row is never a silent denial (owner contract §6: an unexpected lockout reads
as the assistant being broken).

## Two layers, one decision

`can_use_feature(user, grants, feature)` in `auth/features.py` is the single policy: no
active account denies, an `ADMIN` always passes, otherwise the stored row wins and a
missing row falls back to the baseline. Both layers call it, so a menu can never offer
what the backend would refuse, and the backend never depends on what the menu rendered.

1. **Service boundary** (`application/business_common.py`) —
   `ensure_feature(session, actor, feature)` for a service that already holds the actor
   row, `require_feature(session, telegram_user_id, feature)` for one that holds only a
   Telegram id. These are the only places a capability is enforced for real work:
   `sales_ledger`, `inventory`, `ingestion` (upload, instruction, commit),
   `reminders` (confirm, list, cancel), `reference_images` (queue a save), and the
   internal-evidence/photo paths reached through the adapter.
2. **Telegram adapter** (`integrations/telegram/handlers.py`) — `_feature_gate`,
   `_active_user(..., feature)`, `_source_delivery_allowed` and the per-branch
   `allowed()` closure inside `handle_password_text` re-check the same policy before any
   work, including for a button that was printed before the toggle changed. Menu
   generation (`_menu_keyboard`) filters the same way from the user's current grants.

`delete_personal_data` is deliberately ungated: a data-subject right cannot be switched
off by a capability toggle.

## Administrator surface

`/menu` for an active admin gains `👤 İstifadəçilər` next to `🌐 Prioritet saytlar`.
The user list opens a grid (`uf:<user_id>:<key>:<on|off>` buttons) that shows the stored
state and the effective state; an admin's own row renders read-only, because
`set_for_actor` raises `AdministratorFeatureLocked` before any write — no toggle can
lock the owner out of the system. Every accepted change writes one
`USER_FEATURE_ENABLED` / `USER_FEATURE_DISABLED` audit event with the target user, key
and value; pressing the same button twice writes nothing.

Ordinary users do not see the `Users` entry, and `grid_for_actor` / `set_for_actor`
refuse a non-admin actor at the service boundary. A stale or forged `uf:` callback is
therefore a request, never an authorization.

## Storage

`user_feature_permissions(user_id FK CASCADE, feature_key, enabled, updated_at,
updated_by_user_id)` with `PRIMARY KEY (user_id, feature_key)` and a CHECK constraint
listing the ten declared keys (`ck_user_feature_permissions_key`). `models.py` cannot
import the policy module, so `tests/unit/test_feature_permissions.py` pins the
constraint text against `FEATURE_KEYS` — adding a key without a migration fails there.
Deleting a user cascades its grant rows (proved against real PostgreSQL).

## Evidence

* `tests/unit/test_feature_permissions.py` — 29 offline cases (the 10 per-key and 4
  malformed-callback parametrizations included): label/key/Checklist pins,
  baseline vs missing row, admin never locked out and never reads grants, per-key
  active-account requirement, the service boundary denying and allowing, a stale
  `om:photo` press re-authorized, malformed `uf:` data writing nothing, grid coverage,
  the admin-only `Users` row.
* `tests/integration/test_feature_permissions_db.py` — 9 cases on disposable
  PostgreSQL: the explicit baseline grid, the approval transaction, denial at the
  boundary while another capability keeps working, admin lockout refused, a toggle
  effective on the next read, per-user isolation, audit written once and idempotent on
  a repeat, admin-only read/write, and the cascade.
* `tests/unit/test_telegram_menu.py` — a disabled capability hides its own button;
  `Prioritet saytlar` is an administrator row.

## Open

`docs/OPEN-QUESTIONS.md` Q46 records the two assumptions in this slice: the rollout
baseline (everything previously working stays enabled, `orders` starts disabled) and the
`ai_chat` / `document_search` split for free-text questions versus the explicit «Bizim
baza» scope and original-file delivery.
