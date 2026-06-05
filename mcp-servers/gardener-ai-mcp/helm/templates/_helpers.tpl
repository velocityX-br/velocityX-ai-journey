{{/*
Expand the name of the chart, truncated to 63 characters (Kubernetes label limit).
*/}}
{{- define "gardener-ai-mcp.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a fully-qualified name combining release name and chart name.
Truncated to 63 characters.  If the release name already contains the chart
name, the chart name is not appended to avoid double-suffixing.
*/}}
{{- define "gardener-ai-mcp.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create the chart label string: <chart-name>-<chart-version>.
*/}}
{{- define "gardener-ai-mcp.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources managed by this chart.
Includes the selector labels plus the Helm-managed metadata labels.
*/}}
{{- define "gardener-ai-mcp.labels" -}}
helm.sh/chart: {{ include "gardener-ai-mcp.chart" . }}
{{ include "gardener-ai-mcp.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels used for pod selection in Services and Deployments.
Only includes stable labels that must not change after initial deployment.
*/}}
{{- define "gardener-ai-mcp.selectorLabels" -}}
app.kubernetes.io/name: {{ include "gardener-ai-mcp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Determine the ServiceAccount name.
If serviceAccount.create is true and serviceAccount.name is empty,
the full release name is used.  If serviceAccount.create is false,
a non-empty serviceAccount.name is used as-is, otherwise "default".
*/}}
{{- define "gardener-ai-mcp.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "gardener-ai-mcp.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
