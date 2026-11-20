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
user-assigned identities for api/worker/migrator and least-privilege role assignments.

Not included (needs institutional decisions): VNet/subnet/private-endpoint topology, AKS cluster,
Azure OpenAI deployment (DEC-07/AI-D03), Defender for Storage/ClamAV hosting, diagnostic settings,
alert action groups (SEC-D08).

```powershell
terraform init -backend-config=env/staging.backend.hcl
terraform validate
terraform plan -var-file=env/staging.tfvars   # review; apply only with written authorization
```
