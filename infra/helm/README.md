# Helm chart `finagent`

**Status: NOT VALIDATED** - no `helm` binary on the build host; the chart was not rendered or
installed. It deploys api, worker and web (never the mock bank), a pre-install/upgrade migration
job running as the migrator identity, Key Vault secrets through the Secrets Store CSI driver,
non-root read-only containers, a default-deny NetworkPolicy and a worker PodDisruptionBudget.

```powershell
helm lint infra/helm/finagent
helm template finagent infra/helm/finagent -f my-values.yaml   # review before any install
```
