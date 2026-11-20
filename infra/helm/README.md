# Helm chart `finagent`

**Status: LOCAL RENDER ONLY; NOT READY TO INSTALL.** On 20 November 2026, Helm 3.17.3 linted
and rendered the default chart after two YAML syntax fixes. This did not validate a Kubernetes
cluster, identities, secrets, networking, migration, or deployment. Default values contain
placeholders. No chart was installed.

The reference chart describes api, worker and web workloads, a migration job, Key Vault secrets,
non-root containers, default-deny networking and a worker PodDisruptionBudget. Before any staging
attempt, address and test these blockers against the selected environment:

- The default-deny policy selects all pods for ingress and egress. The chart adds only an ingress
  allowance for web-to-api; it does not allow the required egress or ingress to web. NetworkPolicy
  enforcement and any additional policies depend on the cluster. See the [Kubernetes network policy documentation](https://kubernetes.io/docs/concepts/services-networking/network-policies/).
- The pre-install migration job reads `finagent-secrets`, but does not mount the CSI volume. The
  workload pods that do mount it are not yet created at pre-install time. A fresh installation
  therefore needs an explicit secret-bootstrap design; the CSI driver's [sync documentation](https://secrets-store-csi-driver.sigs.k8s.io/topics/sync-as-kubernetes-secret)
  states that syncing requires a pod mounting the volume.
- Service accounts, workload identity bindings, required private endpoints and approved
  configuration must be supplied and verified separately. Rendering does not establish that
  the CSI secret references or identities work.

```powershell
helm lint infra/helm/finagent
helm template finagent infra/helm/finagent -f my-values.yaml   # review before any install
```
