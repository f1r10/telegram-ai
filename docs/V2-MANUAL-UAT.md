# V2 manual acceptance — isolated Telegram test

Status: the capabilities below must be verified after CI and merging. Do not claim production readiness from unit tests.

## WSL deployment (do not delete existing Docker volumes)
Run in ~/telegram-ai:
  git pull --ff-only origin main
  sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml up -d --build app worker searxng
  sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml ps
Do not use docker compose down -v.

## Manual checks
1. /start then /menu: Sual ver, Məhsul axtar, Sənəd əlavə et, Satış hesabatı in a touch-friendly grid, followed by İnternet and Kömək.
2. Sual ver -> 'Salam, nə edə bilərsən?'; no stale Internet selection.
3. Məhsul axtar -> 'KA45 bizdə varmı?'; only verified catalog/document data, not invented current stock.
4. Send a synthetic PDF/DOCX/XLSX, tap Yadda saxla; wait for worker/index. Ask a direct fact; do not accept zero-index results.
5. Type 'Bazaya yaz: QA-KA45 məhsulu 50 AZN-dir.'; ensure a temporary text document is staged. Only after tapping Yadda saxla should ingestion begin. Structured catalog price still requires review.
6. Confirm a reference picture for an existing catalog product. Send the exact same Telegram photo with caption 'Bu məhsul bizdə varmı?'; expect exact-byte match or honest no-match, never invented visual similarity.
7. Ask 'Bu il A şirkətinə nə qədər satmışıq?' Completed sales ledger remains NOT IMPLEMENTED in this PR. No sales numbers may be fabricated.
8. Verify SearXNG returns real JSON search results; /healthz alone is insufficient. Never send private business data to external search.
9. Test ACTIVE/PENDING users, stale callbacks, single Telegram polling process, no writes without approval.
10. Restart app and worker to test persistence; backup/restore requires a disposable test environment.

## Known gaps (do not mark passed)
Full visual image similarity and OCR; sales invoice ledger/aggregation; Mini App dashboard; production UAT; real web search quality and backup restore.
Capture prompts, actual Telegram screenshots, worker logs and indexes without revealing secrets.
