# Telegram Office Assistant V2 — operational checks

The check is **read-only**; it does not reset PostgreSQL, Redis, Qdrant or Docker volumes and does not call a paid AI model.

Run from the WSL checkout **after deploying the matching GitHub commit**:

```bash
cd ~/telegram-ai
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -m office_assistant.scripts.check_readiness
```

Expected JSON:
- `readiness: READY`: PostgreSQL queries succeed, Qdrant collection endpoint is available, real SearXNG JSON search gives at least one result, and existing indexing rows have no recorded failure/deferred warnings.
- `readiness: DEGRADED`: some historical documents still need OCR, index failures require operator attention, or the live internet search returned no hits. Do not advertise fully working web search based only on `/healthz`.
- `readiness: NOT_READY`: essential PostgreSQL or Qdrant checks failed.

**Historical partial-PDF recovery** (no source deletion):
```bash
# First: read-only scan
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -m office_assistant.scripts.repair_legacy_index --limit 100
# Only after backup/review and during a controlled maintenance window:
# sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -m office_assistant.scripts.repair_legacy_index --apply --limit 100
```

The second command only queues eligible older documents for the existing indexing worker; it does not make OCR scans readable. Re-run the readiness check after the worker has completed indexing. If a job remains failed, inspect app/worker logs with credentials and personal data removed before sharing.

The readiness probe does not constitute production certification: manual bot interaction, cross-user access control, resource and soak tests, restore drills and customer document accuracy tests remain necessary.
