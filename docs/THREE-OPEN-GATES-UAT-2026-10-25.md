# Three remaining acceptance gates — Mini App, warehouse and real photos

**Date:** 2026-10-25. **Scope:** Kali staging `~/telegram-ai-staging`, using `docker-compose.staging.yml` and `.env`.  
**These gates remain OPEN until real, owner-reviewed results exist.** Code CI, configuration flags, SQL row counts and synthetic photos cannot be substituted for commercial acceptance.

## A. First, one read-only preflight (no new deployment)

```bash
cd ~/telegram-ai-staging
docker compose -f docker-compose.staging.yml --env-file .env exec -T app \
  uv run --no-dev python -m office_assistant.scripts.check_three_acceptance_gates
```

The command prints only aggregate counts and nonsecret feature flags. Its status is always `PREFLIGHT_ONLY`, never `PASS`. It does not mutate inventory, activate the web service, access original photos, perform model inference or call Telegram. A missing active warehouse, product unit or labelled photo is a genuine setup gap, not an assertion that the associated code failed.

## B. Mini App — actual mobile acceptance, **requires approved HTTPS origin**

Currently `MINIAPP_ENABLED=False` in Kali staging. **Do not enable against the real office or publish a WAN port.** A real-device test requires an operator-controlled HTTPS hostname with valid TLS, fixed origin, hardened reverse proxy/firewall, matching Telegram bot Web App URL and signed `initData`. Expose only the independently configured loopback Mini App service via that HTTPS proxy. The miniapp Compose profile publishes its port to **127.0.0.1** on the host.

1. Before a device: launch the **opt-in** Mini App only after the approved HTTPS endpoint is prepared, then run the existing `office_assistant.scripts.smoke_miniapp` on the reachable loopback port. Expected `SMOKE_PASS`, including static headers, unsigned `/api/me` denial and unsigned stock mutation denial. This is **not** device acceptance.
2. On Telegram **Android and iOS**, each from a currently ACTIVE approved account: open through the bot menu, inspect readable light/dark UI and safe areas, send one ordinary Azerbaijani question, use `Bizim baza`, follow a real citation and download its original file. Confirm that source labels and download bytes match the intended document. Re-run with slow network or brief connection loss; no fake success.
3. From an ordinary user, verify stock-transfer/adjustment/XLSX write features are not exposed/authorized. Check revoked users cannot access authenticated endpoints; stale/forged/expired launch data are denied. Confirm native Telegram chat still works when the Mini App is unavailable.
4. Keep `MINIAPP_STOCK_TRANSFER_ENABLED`, `MINIAPP_STOCK_ACTIONS_ENABLED` and `MINIAPP_STOCK_XLSX_ENABLED` **false** in the live staging DB unless a separate operator-approved **restored disposable clone** is created for mutation tests.
5. Record device type, successful/failed actions, read-only source retrieval, negative cases and sanitized errors. Only owner-verified real mobile evidence may close [#43](https://github.com/f1r10/telegram-ai/issues/43).

## C. Inventory — administrator-only mutations in an **isolated restored clone**

Unit/integration regression coverage already exists for registry/unit conversion, controlled transfer, reservations, CORRECTION/RETURN/DAMAGE/WRITE_OFF, XLSX batch validation and atomicity; these tests run against disposable CI PostgreSQL. This is **not** proof that the company's live warehouse balances are correct.

Use a separate restored test DB with synthetic product codes, two approved active warehouses, explicit opening stock and approved canonical units; not production, and not the existing Kali `tgstage_postgres` DB. In the disposable app enable only the exact feature under test:

| Test | Action and expected result |
| --- | --- |
| Registry / unit | Approve PCS for test SKU; BOX->PCS conversion only with a product-specific factor; incompatible unit rejected |
| Opening / receipt / issue | Establish test baseline; receipt adds quantity; issue cannot overdraw or consume reserved balance |
| Transfer | ADMIN preview -> cancel leaves ledger unchanged; new preview -> confirm moves quantity atomically between two warehouses |
| Reservations | Reserve available stock; CANCEL releases reservation without altering on-hand; CONSUME retires reservation and posts exactly one issue |
| Adjustments | RETURN increases stock; DAMAGE/WRITE_OFF decreases only within nonnegative bounds; CORRECTION requires signed delta, reason and audit |
| XLSX import | Exact 5-/6-column single sheet, bounded rows, no formulas; preview rows and SHA; cancel no writes; confirm one transaction; duplicate batch and changed SHA rejected |
| Authorization / races | USER and revoked ADMIN denied; expired, replayed and cross-actor tickets denied; two concurrent ADMIN requests preserve balance and audit integrity |

If a confirm times out, **do not blindly repeat**; verify the movement and document reference first. Without explicit warehouse/unit reconciliation, ledger approval and multi-account operator UAT, keep [#40](https://github.com/f1r10/telegram-ai/issues/40) open.

## D. Photo calibration — real independently labelled phone photographs

Use actual active, **human-confirmed** catalog references and a private fixed holdout dataset. A camera photo from a different angle is not a duplicate of the reference. Samples must include front/side/back, weak light, confusingly similar SKUs, unknown products and unrelated objects. Do not choose ground truth based on the AI response.

Private JSONL manifest schema (one line per real image):

```jsonl
{"query_id":"p01","scenario":"different_angle","photo":"p01-side.jpg","exact_code":"KA45","similar_codes":[]}
{"query_id":"p02","scenario":"unrelated","photo":"floor-sign.jpg","exact_code":null,"similar_codes":[]}
```

All photo paths stay under the private photo root. Never commit private photographs or code labels to GitHub. The existing `office_assistant.scripts.evaluate_visual_photos` reads a `--manifest` and `--photo-root`, dry-run first. Real scoring with `--apply` requires offline FastEmbed CLIP provisioned locally and authorized, verified reference images. The evaluator records `recall_at_1/3/5`, MRR, `negative_candidate_rate`, `false_positive_rate`, scenario counts and whether capped/truncated. Keep the default 80-reference limit explicit and report insufficient sample size rather than claiming calibration. **No model weights are downloaded implicitly**, and unknown candidates must remain non-authoritative.

Do not turn on the production visual semantic flag solely from synthetic unit tests. The final human review must inspect real misses, confusable positives and withheld SKUs before approving any threshold changes and closing [#35](https://github.com/f1r10/telegram-ai/issues/35).

## Acceptance boundary

**AUTOMATED PASS:** existing GitHub CI suites and any subsequent targeted regressions.  
**PRECONDITIONS:** HTTPS hostname/device, explicit operator stock policy/isolated database, labelled real photo corpus.  
**REQUIRED FOR CLOSURE:** device-captured successful/negative Mini App runs, clean isolated warehouse workflows and measured holdout photo metrics.  

Keep `docker compose down -v`, resetting live databases, enabling the live irreversible stock flags and uploading secret/customer photos to public GitHub out of this procedure.
