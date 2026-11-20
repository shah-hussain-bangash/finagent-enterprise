# Deployment guide (reference - nothing has been deployed)

**Status:** images build locally and the compose stack is the verified local path. Azure deployment
(Terraform, Helm, signing, environment promotion) is documented and templated but NOT validated or
applied - no cloud authorization was given and no cloud cost was incurred.

## Environments
| Environment | Identity | Model | Retrieval | Blob | Scanner | Notes |
|---|---|---|---|---|---|---|
| local / test | local issuer (SYNTHETIC identities) | MockModelProvider | local BM25 | local encrypted files | local test signatures (NOT antivirus) | refused elsewhere by Settings |
| dev / staging / production | Entra ID (OIDC) | Azure OpenAI (approved deployment) | Azure AI Search | Azure Blob | ClamAV clamd | Settings fail closed without these |

Mandatory outside local/test (enforced at startup): data/signing keys, https bank endpoint, connector
credentials, clamd, Azure blob, Azure search with key, Azure OpenAI, `require_promoted_configuration=true`,
case-status write NOT enabled by environment variable in production.

## Release pipeline (reference)
1. CI (`.github/workflows/ci.yml`): lint, strict typing, unit, contract drift (OpenAPI + events),
   SAST + dependency audit + secret scan, release blockers, configured full-suite invocation,
   mutation check,
   web typecheck/build/audit, image builds.
   Hosted CI has run; use the [current evidence record](../05-testing-evidence/13-CURRENT-STATUS-AND-RELEASE-EVIDENCE.md)
   for the exact revision and exclusions. A passing run does not prove staging acceptance or deployment.
2. Image signing (cosign/Notation) + SBOM (syft) + provenance attestation - needs registry and signing
   identity (not configured).
3. `terraform plan` reviewed and approved; `apply` only with written authorization.
4. Evaluation run on the staging configuration -> promote configuration (Tenant Admin, MFA) - the model
   gateway refuses unpromoted configurations.
5. `helm upgrade --install` (pre-upgrade migration job runs as the migrator identity).
6. Smoke: `/health/ready`, sign-in, a synthetic case to WAITING_REVIEW; alerts green.

## Rollback
See `runbook-backup-restore-rollback.md`: `helm rollback`, configuration rollback endpoint, flag-based
containment of the single write. Migrations are forward-only in shared environments.
