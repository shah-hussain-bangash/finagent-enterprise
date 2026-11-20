{{- define "finagent.labels" -}}
app.kubernetes.io/part-of: finagent
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}
{{- define "finagent.securityContext" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: {{ . }}
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: {drop: ["ALL"]}
  seccompProfile: {type: RuntimeDefault}
{{- end -}}
