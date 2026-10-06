# Changelog

All notable changes to this project. Phases follow `docs/ROADMAP.md` (reconciled 2026-10-06).

## [Unreleased]

### Live-test fixes (2026-10-06)

- Citations no longer leak raw technical metadata to the user.
  `internal_evidence/service.py:_citation_label` rendered `json.dumps(locator)`,
  so a `/baza` answer ended with
  `[1] Sənəd — Price list Diller.xlsx — {"blocks": [{"sheet": "Sheet1", ...}]}`.
  The user-facing label is now the document name alone, falling back to the
  Azerbaijani class name (`Sənəd` / `Biznes bazası`) when no filename exists.
  Locator precision is retained only on the model-facing evidence prompt line
  (`_prompt_source`), so grounded synthesis still sees the exact sheet/page.
  Numbering and conflict dedup are unchanged; the same document cited for two
  different blocks now appears twice under the same name, which is accepted.
  4 new unit cases in `tests/unit/test_phase10_evidence_unit.py`.
- Extraction review callbacks no longer report a permission problem for every
  failure. `handle_extraction_callback` collapsed all `ApplicationError` and
  `ValueError` into `Bu əməliyyat üçün icazəniz yoxdur.`, so an administrator
  tapping "Yaddaşa yaz" after a successful save was told they had no permission
  when the real cause was a missing or already finished review. Mapping is now
  `AuthorizationDenied -> NO_PERMISSION`, `DomainConflict ->
  EXTRACTION_REVIEW_UNAVAILABLE` (new Azerbaijani string), everything else ->
  `TEMPORARY_ERROR` with a logged error type.
- `extraction/review.py`: `approve()` committed the run as `APPLYING` and then
  called `_apply_safe()` outside that transaction, so any error escaping the
  per-candidate loop left the run in `APPLYING` permanently and every later tap
  returned the empty counts of that half-finished attempt. An aborted attempt now
  moves the run to `PARTIAL` with a `STRUCTURED_REVIEW_APPLY_ABORTED` audit row so
  a retry re-runs the application, and per-candidate and per-proposal guards cover
  every `ApplicationError` (a rejected candidate becomes `CONFLICT` and is logged)
  instead of aborting the whole run.
- 5 new unit cases in `tests/unit/test_extraction_review_ux.py`; the Phase 8
  integration scenario now also proves an aborted apply leaves `PARTIAL`, not
  `APPLYING`, and that the subsequent approvals still apply all candidates.
- Commands sent in a file or photo caption are now honoured. PTB's
  `CommandHandler.check_update` requires `Message.text`, which is always empty for
  media messages, so `/baza Zəmanət neçə aydır?` typed as a caption never reached
  a command handler and was swallowed by ingestion ("Fayl müvəqqəti sessiyaya
  əlavə edildi."). `handle_document` and `handle_photo` now inspect the leading
  `BOT_COMMAND` caption entity via `_caption_command` and route known commands to
  the same callbacks `app.py` registers, with `COMMAND_CALLBACKS` as the single
  source for both. Per the "explicit commands override ingestion" rule the
  attached file is not stored and the user is told so in Azerbaijani
  (`CAPTION_COMMAND_FILE_SKIPPED`). Unknown `/...` captions, prose captions and
  captionless files keep ingesting. Command arguments now come from
  text-or-caption through `_command_text`.
  7 new unit cases in `tests/unit/test_caption_command_routing.py`.

### Phase 12 — Reminders, `/help`, `/verilerimi_sil` (2026-10-06)

Step A (data layer and parsing):

- Migration `0014_add_reminders`: `reminders`, `reminder_deliveries`, and
  `users.timezone` (NOT NULL, server default `Asia/Baku`). Upgrade, downgrade and
  re-upgrade verified against the isolated `qoder_postgres` instance.
- `core/clock.py`: `Clock` protocol plus `SystemClock`, so reminder arithmetic is testable.
- `reminders/time_parsing.py`: span-eating deterministic parser for Azerbaijani time
  expressions (relative offsets, `sabah`/`birisigün`/`bu gün`, `gələn həftə`/`ay`,
  weekdays, `ayın N-də`, `səhər`/`gündüz`/`axşam`/`gecə`, `HH:MM`, `saat N-də`,
  bare `N-də`, word numbers). Returns UTC instants through the injected `Clock` and
  the user's IANA timezone; ambiguous input returns `needs_clarification` with a
  suggested default instead of a silent save. No LLM is involved.

Step B (UX, dispatcher, commands):

- `application/reminders.py`: token-scoped `ReminderProposal` store (bounded, TTL 30
  minutes, owner-checked), `propose`/`repropose`/`confirm`/`discard`, `/hatirlatmalar`
  listing, ownership-checked cancel, and `/verilerimi_sil` deletion of conversations,
  reminders and open ingestion sessions (user row and audit history preserved).
- `reminders/dispatch.py`: worker-side `claim_due` selects due `ACTIVE` reminders with
  `FOR UPDATE SKIP LOCKED` and atomically writes the `CLAIMED` ledger row, the
  `REMINDER_DUE` outbox event and the `PAUSED` reminder; `reclaim_stale` recovers
  deliveries whose lease expired and reconciles them against outbox state.
- `integrations/telegram/outbox.py`: the app-side consumer now renders `REMINDER_DUE`
  in Azerbaijani and drives `claimed -> sending -> sent`, or `retry_pending` and
  `failed` after three attempts, recording `telegram_message_id`, `sent_at` and
  `last_error`. The worker keeps no bot token.
- `integrations/telegram/handlers.py`: deterministic reminder rule runs before the LLM
  router, so a message such as "sabah saat 9-da Əliyə zəng etməyi xatırlat" produces a
  Confirm / Edit / Cancel keyboard and never an AI answer; `rp:`/`rh:`/`rd:` callbacks,
  `/hatirlatmalar`, `/help` and `/verilerimi_sil` handlers, and the one-message edit wait.
- `integrations/telegram/reminder_format.py`, `messages.py`, `state.py`: Azerbaijani
  weekday/date rendering, the Phase 12 string catalog, and bounded in-process flow state.
- `docs/architecture/decisions/0016-deterministic-reminders.md`, ROADMAP, OPEN-QUESTIONS
  (Q15 proposal durability, Q16 permanently failed deliveries, Q17 timezone capture).

Verification (Phase 12):

- `pytest -m "not integration"`: 159 passed offline (17 parser golden cases,
  15 UX/ledger cases in `tests/unit/test_phase12_reminder_ux.py`).
- `POSTGRES_DB=office_assistant_test pytest tests/integration/test_phase12_reminder_delivery.py`:
  1 passed against the isolated PostgreSQL on 127.0.0.1:5435 — two concurrent
  `claim_due` calls claimed the two due reminders exactly once, a third claim was a
  no-op, the outbox consumer delivered both and drove the ledger to `SENT`, and an
  abandoned `SENDING` delivery was recovered to `RETRY_PENDING` after its lease.
- `ruff check` / `ruff format --check`: clean. `pyright` (strict): 0 errors.
- New disposable `office_assistant_test` database created inside `qoder_postgres` and
  migrated to `0014_add_reminders`; the development database is untouched.

### Phase 12 preparation, Phase 0/11 cleanup (2026-10-06)

- Docker isolation for this working copy: Compose project renamed to
  `qoder_ai_assistand`; containers `qoder_app`, `qoder_worker`, `qoder_postgres`,
  `qoder_qdrant`, `qoder_searxng`, `qoder_model_cache_init`; network `qoder_net`;
  volumes `qoder_*`; host ports hardcoded to 5435 (PostgreSQL), 6335 (Qdrant),
  18085 (SearXNG), all bound to 127.0.0.1. Runs side-by-side with the original
  instance with zero collisions. Fresh database migrated to `0013_web_search_evidence`.
- Added `Makefile` (`up, down, logs, test, test-integration, lint, typecheck, fmt, migrate, seed`).
- Rewrote `docs/ROADMAP.md` to the true delivered plan (Phases 0-11 complete, Phase 12 next).
- Reconciled `AGENTS.md` stack notes with reality (python-telegram-bot + pyright, messages module path).
- Added OPEN-QUESTIONS Q14 (seed script undefined).
- Committed the previously uncommitted, fully green Phase 9-11 work
  (structured business domain, candidate-first extraction, typed business tools,
  internal evidence orchestration, controlled external web evidence: `/baza`,
  `/internet`, `/tamaxtar`, web search cache, migration 0013).

### Verification

- `pytest -m "not integration"`: 120 passed offline.
- `ruff check` / `ruff format --check`: clean. `pyright` (strict): 0 errors.
- `docker compose config`: names and ports as above; `docker compose up -d postgres qdrant searxng`: all healthy alongside the original stack.
