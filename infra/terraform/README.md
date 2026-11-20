# Terraform (Azure reference)

**Status: LOCALLY VALIDATED; NOT PLANNED OR APPLIED.** On 20 November 2026, Terraform 1.11.4
formatting and `terraform validate` passed in an isolated copy after `terraform init -backend=false`
installed the signed AzureRM 4.81.0 provider. The deprecated Key Vault RBAC argument was updated,
and validation passed again without warnings. The provider selection is recorded in
`.terraform.lock.hcl` for repeatability. This checks configuration syntax and provider schemas;
it does not verify credentials, Azure capabilities, connectivity, costs, a plan, or deployment.
No cloud resources were created and no cloud cost was incurred.

What it defines (PROPOSED values, see `variables.tf`): resource group, Log Analytics, Key Vault
(RBAC, purge protection, private), PostgreSQL 16 Flexible Server (Entra auth only, private, zone
redundant HA, geo-redundant backup), Storage (ZRS, no shared keys, versioning + soft delete),
Service Bus Premium queue (duplicate detection, 5 deliveries then DLQ), Azure AI Search, ACR,
user-assigned identities for api/worker/web/migrator and proposed role assignments.

## Key Vault access

`key_vault_secret_access` defaults to an empty map, so this configuration creates no
Key Vault secret-reader assignments unless secret names are explicitly supplied for a
workload. Each configured assignment is scoped to the named secret. For example, the
following names are illustrative only and must match separately provisioned secrets:

```hcl
key_vault_secret_access = {
  api      = ["api-database-url"]
  migrator = ["migration-database-url"]
  web      = ["web-session-secret"]
}
```

This is a proposed reference, not an approved Azure security design. Azure's RBAC
guidance recommends a vault per application per environment for most scenarios and
reserves individual secret scopes for limited cases. Review inherited role assignments,
vault boundaries, secret provisioning, workload identity federation, and CSI integration;
this configuration does not guarantee that a workload has no other access. Secrets are
not created here. Validate the actual deployment and permissions before use.

### Reconcile the chart and access configuration

Before a staging deployment, the platform owner must review the selected vault design and:

1. Provision the approved secrets through an authorized process. Do not put secret values in
   Terraform variables, Helm values, source control, or test logs.
2. For each of `api`, `worker`, and `web`, compare the Key Vault names under
   `secretProviderClass.secrets.<workload>` in the reviewed Helm values with that workload's
   `key_vault_secret_access` entry. Include every name the workload actually needs and remove
   unused names. The example above is illustrative and does not cover the default chart values.
3. Match `migration.databaseUrlSecretName` to the `migrator` access entry and use a separately
   authorized migration credential. Confirm names refer to secrets in the intended vault.
4. Bind each chart workload's service account to its corresponding Azure identity and confirm
   the chart's client IDs and tenant refer to the intended environment. Check inherited RBAC,
   private network paths, the CSI driver, and secret synchronization in the target cluster.
5. Review the Terraform plan and Helm output, then test startup, migration and denied access
   using synthetic data before considering a deployment. Mocked Terraform tests and Helm renders
   do not establish live permissions or connectivity.

To run the local mocked checks after initializing the pinned provider in an isolated copy:

```powershell
terraform test -no-color
```

**Existing environments:** this change replaces previous vault-wide role assignments.
Applying it with the default empty map can remove those assignments and interrupt
services. Review the Terraform plan and agree a staged migration, including secret
creation and access verification, before any apply. No cloud deployment has been tested.

Not included (needs institutional decisions): VNet/subnet/private-endpoint topology, AKS cluster,
Azure OpenAI deployment (DEC-07/AI-D03), Defender for Storage/ClamAV hosting, diagnostic settings,
alert action groups (SEC-D08).

```powershell
terraform init -backend-config=env/staging.backend.hcl
terraform validate
terraform plan -var-file=env/staging.tfvars   # review; apply only with written authorization
```
