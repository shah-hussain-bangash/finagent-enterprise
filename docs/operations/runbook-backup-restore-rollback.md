# Runbook: backup, restore, rollback

RPO/RTO are OPEN (SEC-D09); PROPOSED 4 h / 15 min. Nothing here has been exercised in Azure.

## Backup
- **Azure:** PostgreSQL Flexible Server automated backups (35 days PROPOSED, geo-redundant) and
  Storage versioning + soft delete (Terraform). Key Vault soft delete + purge protection.
- **Local:** `python scripts/backup_restore.py backup` writes `var/backups/finagent-<utc>.dump`
  (pg_dump custom format via the compose `postgres` container). Blob objects are encrypted with the
  data key; back up `var/blobs` together with the dump and keep the key separately.

## Restore (local, disposable target only)
`python scripts/backup_restore.py restore var/backups/<file>.dump --target finagent_restore_check`
restores into a NEW database (never over the live one), then runs `alembic upgrade head` and the
audit hash-chain check. Before granting access to a restored copy, re-apply deletions and legal holds
recorded after the backup time (DIR-035, SPR-025) - retention deletion is disabled in R1, so today
that means verifying `legal_holds` and `retention_rules` match the live system.

## Application rollback
1. Images are immutable and tagged; roll back with `helm rollback finagent <revision>` (or redeploy the
   previous tag). The API/worker refuse to resume runs whose checkpoint graph version differs
   (HUMAN_REVIEW, no silent replay).
2. Database migrations are forward-only in shared environments; each revision has a tested
   `downgrade` (the test suite runs base -> head every session) for local/disposable use only.
3. AI configuration rollback: `POST /api/v1/admin/config-versions/{id}/rollback` re-promotes an earlier
   evaluated configuration (lineage kept).
4. Containment (SPR-050): disable the write flag (`PUT /api/v1/admin/feature-flags/case_status_write`)
   and stop workers; evidence, decisions and the effect ledger are preserved.
