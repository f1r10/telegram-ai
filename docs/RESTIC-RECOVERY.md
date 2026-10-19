# Restic disaster-recovery snapshot — operator procedure

Status: opt-in snapshot utility, not a completed restore drill or a claim of production data protection.
The code does not stop services, run against live data automatically, or delete volumes.

The application uses PostgreSQL as the transactional authority and private file
storage for originals. Qdrant is a derived, rebuildable index. A PostgreSQL dump
without matching private file storage is insufficient for provenance recovery.
This tool submits the dump and storage in one Restic backup invocation.

## Preconditions

1. Verify Restic and pg_dump are installed on a trusted operator host; review
   versions and license. Use a non-public, off-machine Restic repository.
2. Set RESTIC_REPOSITORY, RESTIC_PASSWORD_FILE, PGDATABASE and the appropriate
   PGHOST, PGPORT, PGUSER, and PG passfile in the operator environment, not Git.
   No passwords may be placed on a command line or in CI logs.
3. Find the actual mounted original file-storage directory on the host. The
   operator needs read permissions for all blobs and image assets. Prepare a
   SEPARATE, operator-owned, private staging directory on an encrypted trusted
   filesystem (e.g. mkdir -m 700 /secure/office-backup-stage). This location
   must not be inside the application storage or the Restic repository.
4. Default dry-run (does not execute pg_dump or restic):

       python -m office_assistant.scripts.backup_restic --storage-root /absolute/private/storage --staging-root /secure/office-backup-stage

5. Before creating a snapshot, quiesce ALL writers, including Telegram polling
   app, ingestion worker, Mini App uploads, admin imports, and scheduled jobs.
   Keep PostgreSQL itself running for pg_dump. A dump and filesystem copy are
   NOT atomic if a writer remains active. Do not claim consistency otherwise.
6. Only after approved quiescence and preflight, execute:

       python -m office_assistant.scripts.backup_restic --storage-root /absolute/private/storage --staging-root /secure/office-backup-stage --apply --confirm-services-paused

SNAPSHOT_WRITTEN is not RESTORE_VERIFIED. Temporary dumps are written ONLY to the private staging directory,
with restrictive file permissions, and removed by the utility; failures are reported without private subprocess output.

## Isolated restore drill (required before production approval)

Use a separate disposable Compose project, test database and empty storage
volume. Use restic snapshots to list available snapshots and restore one into
a new private directory. Find the manifest.json and postgres.dump under the
temporary staging root in that snapshot. Restore the custom-format dump to a
fresh TEST database with pg_restore and place the storage tree into a separate
TEST volume. Rebuild the derived Qdrant index from restored authoritative data.
Do not overwrite production paths, volumes, or PostgreSQL.

Check original-file SHA-256, citations, source downloads, sales and inventory
ledgers, reference-image provenance, successful document indexing and query
answers with synthetic identities. Privately record the snapshot ID, date,
pass/fail results, recovery duration and approved RPO/RTO. Backup passwords,
customer files and company data must not be committed to GitHub.

Until this is done the backup is unverified and production release remains gated.
