{{- define "finagent.labels" -}}
app.kubernetes.io/part-of: finagent
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "finagent.securityContext" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: {drop: ["ALL"]}
  seccompProfile: {type: RuntimeDefault}
{{- end -}}

{{- define "finagent.env" -}}
envFrom:
  - configMapRef: {name: finagent-config}
  - secretRef: {name: finagent-secrets}   # synced from Key Vault by the CSI driver
{{- end -}}
