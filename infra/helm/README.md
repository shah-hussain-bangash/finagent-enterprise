# Helm chart `finagent`

**Status: LOCAL RENDER ONLY; NOT READY TO INSTALL.** On 20 November 2026, Helm 3.17.3 linted
and rendered the default chart after two YAML syntax fixes. This did not validate a Kubernetes
cluster, identities, secrets, networking, migration, or deployment. Default values contain
placeholders. No chart was installed. The dedicated migration-credential render was also checked
with synthetic values by `verify_render.py`.

The reference chart describes api, worker and web workloads, a migration job, Key Vault secrets,
non-root containers, default-deny networking and a worker PodDisruptionBudget. Before any staging
attempt, address and test these blockers against the selected environment:

- The default-deny policy selects all pods for ingress and egress. The chart adds only an ingress
  allowance for web-to-api; it does not allow the required egress or ingress to web. NetworkPolicy
  enforcement and any additional policies depend on the cluster. See the [Kubernetes network policy documentation](https://kubernetes.io/docs/concepts/services-networking/network-policies/).
- The pre-install migration job now mounts a dedicated CSI SecretProviderClass and reads only its
  configured migration database URL from a file; it no longer depends on the application pods to
  sync `finagent-secrets`. Supply a separately authorized migration credential and the migrator
  workload identity. This bootstrap has only been rendered locally and must be tested on a fresh
  cluster with the Azure CSI driver. Application pods still use a synced Kubernetes Secret; the
  CSI driver's [sync documentation](https://secrets-store-csi-driver.sigs.k8s.io/topics/sync-as-kubernetes-secret)
  states that syncing requires a pod mounting the volume.
- Service accounts, workload identity bindings, required private endpoints and approved
  configuration must be supplied and verified separately. Rendering does not establish that
  the CSI secret references or identities work.

```powershell
helm lint infra/helm/finagent
helm template finagent infra/helm/finagent -f my-values.yaml   # review before any install
python infra/helm/verify_render.py --helm helm  # requires PyYAML from the development dependencies
```
