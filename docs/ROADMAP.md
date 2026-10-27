# ROADMAP — Implementation Phases (reconciled 2026-10-06)

This file previously listed a 7-phase plan (0-6) that was never checked off and
no longer matches the delivered system. The authoritative phase plan is now the
one actually implemented, recorded in `docs/architecture/README.md`, the decision
records `docs/architecture/decisions/0001..0016`, and the test suite
(`tests/integration/test_phaseN_*`). Completed items are checked only where
acceptance evidence exists in-repo (migrations, modules, tests).

The owner's 2026-10-07 Master Specification governs approved product requirements when provided; `docs/SPEC.md` is an older V1 baseline. Completed code and tests, not planning text, establish implementation status.

## Staging OCR evidence remediation (2026-10-25)

- [x] Implement deterministic document-evidence routing for exact product-code packaging/lot questions and source-scoped photo label extraction (PR #96).
- [x] Add synthetic offline regressions covering Qdrant OCR chunks, Telegram Vision captions, identifier mismatches and conflicting labels.
- [ ] Verify CI is green, merge PR #96, rebuild Kali staging app/worker, and repeat live Telegram QA-IMG-682 tests. GitHub commits alone do not update the running VM.
- [ ] Validate general image/product search against representative real-image corpus before declaring production acceptance.

## Phase 0 — Project Setup & Tooling ✅

- [x] Python 3.12 project with uv (`pyproject.toml`, `uv.lock`).
- [x] Docker Compose: app, worker, postgres, qdrant (+ searxng, model-cache-init).
- [x] Linting (ruff) and typing (pyright strict) — pyright replaced mypy in practice; recorded here, AGENTS.md updated to match.
- [x] pytest configuration; unit tests run offline (`-m "not integration"`).
- [x] Structured, redacted logging (`core/logging.py`).
- [x] Makefile: up, down, logs, test, lint, typecheck, fmt, migrate (seed pending, OPEN-QUESTIONS Q14).

## Phase 1 — Telegram Scaffold & Database Foundation ✅

- [x] SQLAlchemy async models, Alembic, migrations 0001-0003 (users, audit).
- [x] Telegram transport (python-telegram-bot, long polling) behind handlers layer.
- [x] `/start`, `/whoami`, `/users`, `/cancel` commands; Azerbaijani UI strings module.
- [x] Allowlist + access-password authentication (`decisions/0003`), role model.

## Phase 2 — Authentication & Raw Ingestion ✅

- [x] Private-chat raw file ingestion with pending save sessions (`decisions/0004`).
- [x] File validation, size limits, local storage abstraction.

## Phase 3 — PostgreSQL Worker ✅

- [x] `jobs` table, lease-based claiming with `FOR UPDATE SKIP LOCKED` (`decisions/0005`).
- [x] Deterministic ingestion worker; per-file and per-job states.

## Phase 4 — Document Parsing ✅

- [x] Parser adapters: TXT, PDF, DOCX, XLSX, deferred image/OCR marking (`decisions/0006`, `0007`).
- [x] Versioned normalized parse artifacts; Telegram notification outbox.

## Phase 5 — Knowledge Index & Retrieval ✅

- [x] Qdrant collection with alias, deterministic chunks, FastEmbed dense + BM25 sparse (`decisions/0008`, `0009`).
- [x] Hybrid RRF retrieval with file/parse-run/media-type filters; access filters applied.
- [x] Migrations 0004-0008.

## Phase 6 — Grounded Answers & Chat ✅

- [x] OpenAI Responses API behind provider abstraction (`decisions/0010`).
- [x] `/baza` grounded answers with validated citations; conversation persistence (migration 0009).

## Phase 7 — Structured Business Data ✅

- [x] Products, customers, prices, payments, events, proposals (migration 0010, `decisions/0011`).

## Phase 8 — Candidate-First Extraction ✅

- [x] Structured extraction with explicit Telegram review (migration 0011, `decisions/0012`).

## Phase 9 — Typed Business Tools ✅

- [x] Read-only, allowlisted PostgreSQL tool registry (migration 0012, `decisions/0013`).

## Phase 10 — Internal Evidence Orchestration ✅

- [x] Bounded natural-language routing across chat, business tools, and document evidence (`decisions/0014`).
- [x] Unified citations, conflict surfacing, secure recent-source file delivery.

## Phase 11 — Controlled External Web Evidence ✅

- [x] SearXNG + Tavily behind `WebSearchProvider` (migrations 0013, `decisions/0015`, ADR-001).
- [x] `web_search_cache`, `/internet`, `/tamaxtar`, SSRF-hardened fetcher, evidence separation.

## Phase 12 — Reminders, /help, /verilerimi_sil ✅ (SPEC §4, §5)

- [x] `reminders` + `reminder_deliveries` tables (migration 0014, `users.timezone` added).
- [x] Azerbaijani natural-language time parsing with injected Clock and user IANA timezone.
- [x] Confirm/Edit/Cancel UX before saving (invariant 9): inline keyboard, token-scoped proposals.
- [x] Worker dispatcher loop with `FOR UPDATE SKIP LOCKED`; delivery ledger state machine
      `claimed -> sending -> sent` / `retry_pending -> failed` driven by the app-side outbox consumer
      (`decisions/0016`).
- [x] `/hatirlatmalar` with inline cancel, `/help`, `/verilerimi_sil` with confirmation keyboard.
- [x] SPEC §5 Azerbaijani golden cases as tests (17 parser cases, 15 Phase 12 UX/ledger cases).

Recurring reminders remain out of scope (`recurrence_rule` column reserved, unused).

## Phase 13 — Live-test fixes and image ingestion (2026-10-06)

- [x] Issue 1: user-facing citation labels no longer render raw locator JSON.
- [x] Issue 2: extraction review failures are no longer reported as permission problems; an aborted
      apply leaves `PARTIAL` instead of a stuck `APPLYING`.
- [x] Issue 3: a command typed in a file/photo caption routes to the command instead of ingestion.
- [x] Issue 4: web search reports outages, logs why an answer said "nothing found", stops forcing a
      one-day window for price questions, and runs two DuckDuckGo paths inside a 12-second budget.
- [x] Issue 5: uploaded JPEG/PNG images are transcribed through vision (`decisions/0017`), indexed
      like any other document, and left visibly `DEFERRED` when the vendor fails.
      *Amended 2026-10-07:* automatic transcription at upload was withdrawn by the ADR 0017
      amendment; images are stored `DEFERRED` and read on demand (Block 3A below).

## Master Spec remediation blocks (2026-10-07, owner-approved order)

- [x] Block 1: retrieval truth — the read path resolves the live projection, a missing or failing
      Qdrant alias/collection is reported as unavailable instead of "nothing found", and local
      embedding models load at startup rather than inside the first user turn.
- [x] Block 2: clarification honesty and `FILE_DELIVERY` reachability — `ver`-family delivery
      verbs are recognised before the evidence planner runs, and a clarification turn states the
      element its reason code actually detected instead of always demanding a customer.
- [x] Block 3A: no vendor egress at upload — the ingestion worker holds no vision client, a JPEG/PNG
      upload ends in a `DEFERRED` `image-probe` run with an empty text artifact, and image reading
      moves to the turn that asks about the image (ADR 0017 amendment).
- [x] Block 3: multimodal router — `TurnContext`/`MediaRef` carry the image attached to an explicit
      command, or the one the user replies to, into the turn as a first-class search parameter, read
      on demand for that question only and cited as `IMAGE_UNTRUSTED` (Master Spec §22, §3.1). The
      caption router no longer discards the file: `/baza` and `/tamaxtar` keep the attachment, and
      `/saxla` stores it first and then runs.
- [x] Block 4: web fallback and relevance gate — a page of hits that the gate rejects now reaches
      Tavily exactly as an empty page does (the answer layer hands its usability test to the search
      layer, cache reads included), a hit dated outside the requested window no longer counts as
      current, a failed web search leaves `/tamaxtar` with its internal answer plus an honest
      Azerbaijani line, and `fallback_used` survives a cache hit (Master Spec §21, `decisions/0015`).

## Phase 12A — Image asset foundation (Master Spec §23, owner-approved 2026-10-08)

- [x] Block 5A: content and provenance — an uploaded JPEG/PNG becomes one `image_assets` row keyed
      by its own SHA-256, with its bytes copied into a separate `image_assets/<digest>` namespace so
      `blobs/` still holds exactly the original upload, plus one `image_asset_occurrences` row per
      sighting carrying the locator that says where it was seen. Re-registering the same place
      records nothing new; one logo in fifty places stays fifty locators over one copy of bytes.
      Format and pixel size come from the file header, and a header that states no size leaves the
      columns NULL instead of guessing (no imaging library was added). Still no vendor call at
      upload: the run stays `DEFERRED` (ADR 0017 amendment).
- [x] Block 5B: pictures found inside a document become content, never an upload — the PDF, DOCX and
      XLSX parsers now report the pictures they carry, each with the locator its format can honestly
      state: a PDF page plus the XObject the page's own content stream draws (a resource the page
      never paints is not a sighting, and one level of form nesting says so), a DOCX paragraph index
      or table row and column — including the paragraph or cell inside a header or footer part a
      section defines, whose locator names that part and role instead of pretending to be body text —
      an XLSX sheet name plus its anchor cell. The bytes land in the same
      `image_assets/<digest>` namespace as an upload, so one logo shown in a paragraph and in a table
      cell is one copy of bytes and two occurrences, while `file_objects` gains no row for it and
      `blobs/` keeps only the document the user sent (§23.3, §23.5). A flat PDF raster is rebuilt
      into a PNG from the grid its own dictionary declares, 8-bit DeviceGray/DeviceRGB only, with a
      `/SMask` carried as the alpha channel it describes; a payload this build cannot describe — an
      inline PDF image, a JPEG 2000 stream, a colour space with no rule here, a picture over the size
      cap — is counted and warned about instead of silently dropped or guessed at, and a document
      whose text is readable still finishes `SUCCEEDED` rather than being deferred.
- [x] Block 5C: `product_image_relations` plus the deterministic "Bu KA45-dir, yadda saxla"
      confirmation (§23.7) — the caption is read by a pattern, so a code that parses costs no model
      tokens; an unknown product is never created, an ambiguous caption is never guessed, and every
      refusal writes nothing at all, bytes included. The caption queues its claim against the open
      ingestion session (Q31, resolved by the owner 2026-10-10) and `/saxla` is what writes it: the
      relation lands as `USER_CONFIRMED` with `confirmation_type = USER_EXPLICIT` on `is_reference`,
      next to its own `source_references` row naming the exact message and file, so §22.3's "the
      human said it" survives as evidence rather than as a log line, while `/cancel` or expiry leaves
      no authoritative trace behind. §22.7 is a database fact, not a convention: a partial unique
      index plus an advisory lock keep one active explicit claim per picture and one live claim per
      message, so a second product naming the same
      bytes is refused and told which product already holds it, while §22.4's many pictures for one
      product stay allowed. Superseded rows are kept, never deleted (§23.6). A confirmed photo keeps
      both copies the owner asked for — `blobs/` holds the original the user sent, `image_assets/`
      the internal resource the relation points at (Q27) — and because the confirmation arrives in
      the same turn as the upload, its content is made addressable without inventing an occurrence
      no parser sighted yet (§23.5).
- [x] Block 5D: `/leqv_et <kod>` — the safe explicit revoke the owner approved (2026-10-10, part (b)
      of Q31). Administrator-only, and the answer says what it did: the command normalizes the code,
      holds it server-side, and asks for a confirmation button before anything changes, because a
      callback payload is client-supplied and an unverified string gets to decide whose confirmation
      disappears. Revoking is one column (`superseded_at`) on every active `USER_EXPLICIT` row of the
      named product, under the same advisory lock plus row locks a confirmation takes, so a revoke
      and a save cannot interleave and two administrators pressing yes at once retire the claim once.
      Nothing is deleted and nothing is reassigned: the bytes in both namespaces, the asset, the
      provenance row and the earlier `PRODUCT_IMAGE_CONFIRMED` audit all stay, a
      `PRODUCT_IMAGE_REVOKED` audit is added per retired row, and asking again answers
      "nothing active" rather than an error. The retired picture stops answering the active reads, so
      the next caption for those bytes is a fresh decision instead of a conflict (§22.7 guards a live
      claim only). No migration: §23.6 already defined the lifecycle column this needs.
- [x] Block 5C follow-up 2: Q32 resolved (owner, 2026-10-10) — the naming caption has to *assert*, not
      merely mention. A caption authorizes a picture only when `yadda saxla` closes it, exactly one
      distinct product code appears after canonical normalization, that code carries an Azerbaijani
      naming copula (`KA45-dir`, `KA45 dır`, `KA45dır`), and nothing cancels the statement: `deyil`
      after the name denies it and a question mark anywhere means the user was asking. So
      `Bu məhsul KA45, yadda saxla`, `KA45 anbarda var, yadda saxla` and
      `Bu KA45-dir deyil, yadda saxla` write nothing, where before they confirmed — the second one
      being the exact shape §22.7's "the human said it" cannot survive. One product named twice is
      deduplicated rather than called ambiguous, since identity and not spelling decides ambiguity;
      two distinct codes still refuse to be chosen. The digit requirement, the canonical code path,
      the photo-only boundary, the Telegram routing and `CAPTION_COMMAND_FILE_SKIPPED` are untouched.
      No migration.
- [x] Block 5C follow-up 3: the Q32 recognizer widened (owner, 2026-10-10) — requiring the copula and
      the two-word phrase `yadda saxla` was narrower than the approved requirement, so four of the
      five instructions an owner would actually type were refused. The rule is now an *instruction
      that binds*: a save/link verb (`saxla`, `yadda saxla`, `bağla`) still has to close the caption,
      and the single distinct code still has to be tied to the picture — either by the naming copula
      as before, or by `şəki?l` / `məhsul` / `referans` / `üçün` as the next word, matched as a prefix
      because Azerbaijani folds the case ending into the word (`şəklini`, `şəklidir`). So
      `KA45 şəklini yadda saxla`, `Bu şəkli KA45 məhsuluna bağla`,
      `KA45 üçün referans şəkil kimi saxla` and `Bu KA45 məhsulunun şəklidir, yadda saxla` confirm
      alongside `Bu KA45-dir, yadda saxla`. Binding is what keeps the refusal honest: `Bu KA45-dir`
      and `KA45 şəklini göndərdim` say nothing to store, and `Bu məhsul KA45, yadda saxla` still
      writes nothing because the code is mentioned rather than bound. Negated verbs (`saxlama`,
      `bağlama`), `deyil` anywhere, a question mark anywhere, several distinct codes, and a caption
      without a digit-bearing code all stay refusals, and identity stays strict — `KA45-in` reads as
      `KA45IN`, a different product, never guessed back to `KA45`. No migration, no new abstraction;
      the `/saxla` commit boundary (Q31), the photo-only rule and the Telegram routing are unchanged.
- [x] Phase 12A message hygiene: two user-facing strings still built inside `handlers.py` — the
      empty-registry answer of `handle_users` and the account line of `handle_whoami` — moved to
      `messages.USERS_NONE_REGISTERED` and `messages.WHOAMI_DETAILS`, so neither reply builds wording
      inline anymore and both are asserted against the catalog the project rule requires. The admin
      user-list line built by `_user_label` still carries its own labels; that is pre-existing and is
      reported as remaining work rather than changed here. Text unchanged, so no user-visible change.


- [x] Cross-platform storage-key hardening identified by GitHub Actions Linux
      tests: reject Windows drive/backslash keys and dot segments, retain
      symlink confinement, and test portable path safety.
- [x] Post-delivery Telegram text cleanup (2026-10-10): Q33 closed by moving the
      `_user_label` line template and both fallback labels into `messages.py` without
      changing existing text. Unit tests now cover missing and present names/handles.
- [x] Corrected the `/help` description: `/tamaxtar <sual>` is combined internal +
      external search; original source files are requested by conversational follow-up
      (for example, `2-ci mənbəni göndər`), not by `/tamaxtar <nömrə>`.
- [ ] Live Telegram acceptance with two real accounts, real product photos and the
      `/saxla` → `/leqv_et` lifecycle is still a manual production gate.
- [x] Phase 12B evaluation infrastructure: added a deterministic offline JSONL
      benchmark harness for Recall@1/3/5, MRR, exact-identity precision,
      false-positive rate and similar-product Recall@5; tests use synthetic
      identifiers only. See docs/PHASE12B-BENCHMARK.md.
- [x] Phase 12B OCR evaluation added: private JSONL visible-code truth and OCR-candidate
      prediction inputs; deterministic code precision/recall/F1, false alarm rates,
      exact-set accuracy and per-scenario reporting, with no product identity inference.
      Real OCR engine execution and local visual model benchmark are still pending.
- [x] Added opt-in local Tesseract candidate runner for a private image manifest;
      image path confinement, timeout, code allowlist and synthetic tests included.
      No new production dependency; real model comparison remains open.
- [ ] Phase 12B: OCR and visual embedding benchmark on a representative,
      non-public company dataset; do not declare an embedding model selected
      based only on synthetic samples.


## Explicitly out of scope (recorded):## Sales-office productization (owner direction 2026-10-13)

- [x] Web provider malformed-response/transport error correction with fallback tests.
- [x] Short `/menu` for active private-chat users and one-message guided internal,
      internet, and combined search; see `docs/SALES-OFFICE-UX.md`.
- [ ] Confirm actual natural-language intent quality using misspelled sales queries,
      with OpenAI bounded planner evaluation against the owner's private scenarios.
- [ ] Fix remaining real SearXNG/Tavily search-quality failures based on reproducible
      screenshots, safe logs, and query examples (no credentials).
- [x] Add disposable PostgreSQL/Qdrant integration CI job to GitHub Actions.
- [x] The first ephemeral PostgreSQL/Qdrant integration run passed 52 tests;
      1 live SearXNG test was explicitly skipped (requires local outbound search).
- [ ] Run a separate real SearXNG provider smoke test and production-like Docker
      smoke without using private production data.
- [ ] Run real PostgreSQL/Qdrant integration and Docker smoke on current branch.
- [x] Add four-concurrent-user synthetic menu scope-isolation regression tests;
      clear stale internet mode when another action is selected.
- [ ] Run 3–4 real-account Telegram concurrency, restart and resource/capacity test.
- [ ] Complete real Telegram photo reference save/search/revoke acceptance,
      cross-user source isolation, backup+restore drill and final product UAT.
- [ ] Finish representative local OCR/visual model benchmark before Phase 12C.

 recurring reminders, scanned-PDF OCR, image embeddings,
exchange-rate conversion, reports, reranking, mutation tools, autonomous agents.

## Visual photo retrieval hardening — 2026-10-26

- [x] Ordinary Telegram photo search includes a bounded non-authoritative PDF image
      near-duplicate fallback when approved product-photo search does not match.
- [x] Add a fine spatial-colour check on near duplicates and bordered PDF image
      candidates to reduce coarse-hash false suggestions.
- [x] Raise opt-in local semantic model reference coverage to 500 and provide
      a dry-run/--apply offline prewarming command for up to 500 approved photo
      references plus up to 500 embedded PDF image occurrences.
- [ ] Add a Qdrant ANN image collection with current-reference revocation and
      active-document provenance filtering; today's warm vector files are NOT ANN.
- [ ] Calibrate different-angle recall and unrelated-object false suggestions on
      the owner's independent private real-phone holdout before enabling semantic
      candidate display.
- [ ] Recheck active approved photo count on Kali staging, then run a real
      Telegram photo reference /saxla lifecycle and mobile latency p50/p95.
- [ ] Complete operator-approved staging deployment and owner acceptance; the
      GitHub unit/integration suite alone does not close photo UAT.

- [x] Bound Telegram optional approved-image duplicate/semantic stages to 2s/4s,
      and Mini App optional semantic stage to 5s; CI regression test added.
- [ ] Confirm real end-to-end mobile photo p95 against the five-second target;
      stage budgets alone are not an end-to-end guarantee.

- [x] Add safe, code-only Kali staging photo rollout script and explicit real-photo manual UAT checklist; keep visual semantic matching OFF by default when unset.
- [ ] Execute Kali staging rollout with verified backup and capture five operator-owned photo outcomes, active reference count, actual model status and response latency.

- [x] Add an opt-in local DINOv2 Small mean-pooling 384-d encoder, informed by
      `f1r10/local-image-search`, without replacing the PostgreSQL product registry
      or existing CLIP vector cache. Regression CI must pass before merging.
- [ ] Provision reviewed optional CPU-only Torch/Transformers and DINOv2 weights
      on the staging host without implicit downloads during Telegram turns.
- [ ] Benchmark DINOv2 mean-pooling with private held-out real phone images;
      tune the candidate threshold and check false suggestions before enabling.
- [ ] Compare optional YOLO crop off/auto on non-watch inventory after
      dependency/license review; no Ultralytics dependency in the base bot.

- [x] Provide an opt-in, CPU-only DINOv2 staging app/ops image and Compose override,
      explicit public-weight download + offline synthetic dimension probe, and
      separate offline approved/PDF source prewarm procedure without modifying the
      base Telegram image or main data stores.
- [ ] Validate real Torch/DINO weights and Compose overlay on CI, then confirm
      actual Kali CPU model performance and owner-labelled photo holdout before
      any semantic feature flag is enabled.

- [x] Add bounded in-process hot reuse of SHA-verified semantic vectors for
      unchanged authorized catalog/PDF source images; invalidate on file edits,
      removal, source path changes and use only the current DB reference list.
- [ ] Measure cold vs warm p50/p95 against private 100–500-image corpora on Kali;
      an in-process cache does not replace persistent ANN or calibrated negatives.
