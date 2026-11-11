# Terraform (Azure reference)

**Status: NOT APPLIED, NOT VALIDATED.** The build host had no `terraform` binary and no cloud
authorization was given; nothing was deployed and no cloud cost was incurred.

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
