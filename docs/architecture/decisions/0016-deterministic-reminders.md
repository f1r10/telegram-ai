# ADR 0016: Deterministic reminders with a split claim/send ledger

## Status

Accepted for Phase 12. Supersedes the "LLM extracts `{text, datetime}`" step of
SPEC §5 for V1: the same fields are produced deterministically, which removes a
vendor call, keeps numbers and instants out of model output (invariants 3 and 7),
and is covered by golden-case tests. SPEC §6's cheap deterministic rule is the
router in front of the LLM instead.

## Decision

Reminder time interpretation is deterministic and LLM-free. `parse_reminder_time`
consumes Azerbaijani time spans in a fixed order (relative offset, day word,
week/month rollover, weekday, day of month, day period, clock time) against an
injected `Clock` and the user's IANA timezone, and returns UTC instants for
storage. Ambiguous expressions never yield a silent save: they return
`needs_clarification` plus a suggested instant that must be shown to the user.

Nothing is persisted before the user confirms. A parsed message becomes a
token-scoped proposal in a bounded in-process store; Telegram renders it with a
Confirm / Edit / Cancel inline keyboard. Edit arms a one-message text wait so
the next message replaces only the time while the reminder content is preserved.
Proposals expire by TTL and are owner-scoped, so a callback from another account
cannot confirm someone else's reminder (OPEN-QUESTIONS Q15).

Delivery splits responsibilities across processes because only the app holds the
bot token. The worker's `ReminderDispatchService.claim_due` selects due
`ACTIVE` rows with `FOR UPDATE SKIP LOCKED` and, in one transaction, creates the
`reminder_deliveries` ledger row in `CLAIMED`, pauses the reminder, and enqueues
a `REMINDER_DUE` outbox event. The unique
`(reminder_id, scheduled_for)` key makes a second claim of the same occurrence a
no-op. The app-side outbox consumer performs the send and drives
`CLAIMED -> SENDING -> SENT`, or `RETRY_PENDING` and finally `FAILED` after three
attempts; deliveries abandoned by a crash are recovered by `reclaim_stale` once
the lease expires and reconciled against the outbox event status.

Policy is at-least-once: a crash between the Telegram send and the commit may
rarely duplicate one reminder message, which is preferred over silently losing a
confirmed reminder. V1 reminders are one-shot; `recurrence_rule` and
`next_run_at` are stored but unused.

`/verilerimi_sil` deletes the caller's conversations, reminders, and open
ingestion sessions behind a confirmation keyboard. The user row and audit
history are preserved, because authorization and abuse protection depend on
them.

## Consequences

The worker stays token-free and testable without Telegram. Reminder prose,
buttons, weekday names and confirmation wording are Azerbaijani and live in the
central message catalog. A permanently failed delivery leaves its reminder
`PAUSED` for operator review rather than re-firing forever (OPEN-QUESTIONS Q16).
