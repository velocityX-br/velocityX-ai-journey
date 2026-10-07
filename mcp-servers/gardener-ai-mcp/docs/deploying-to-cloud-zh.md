# gardener-ai-mcp 云端部署说明

> 面向读者：负责把 `gardener-ai-mcp` 从开发者机器搬到共享 Kubernetes 环境（Gardener Shoot、AKS/GKE/EKS 或 SAP 内部集群）的运维/平台工程师。
> 姊妹文档：`docs/running-locally-zh.md`（本地开发环境）。
> 权威源：`docs/deploy-cloud.md`（英文原版，含 Helm values 细节）— 本文件为其决策与操作层的高层指南。

---

## 1. 部署模型与决策点

云端部署与本地最大的不同：**MCP server 需要以 HTTP/SSE 方式对外暴露**，因为多个 AI Agent（可能跨机器）都会连接同一实例。stdio 只适合单机。

选择前先做三个决策：

### 决策 1 — Qdrant 部署形态

| 选项 | 场景 | 说明 |
|---|---|---|
| **A. 内嵌 Qdrant（同 Chart，StatefulSet）** | PoC、单团队、数据量 < 10 GB | 简单；Helm subchart 一键起 |
| **B. 外部托管 Qdrant Cloud / 自建集群** | 多团队共享、大数据量、需要备份 | Helm 设 `qdrant.enabled=false`，配置 `QDRANT_URL` 指向外部 |

生产强烈推荐 **B**：向量库有独立的生命周期（备份、扩容、升级），不该与 MCP server 绑死。

### 决策 2 — MCP 传输协议

- `MCP_TRANSPORT=sse` — Server-Sent Events（旧协议，兼容度高）
- `MCP_TRANSPORT=streamable-http` — 新版 MCP HTTP 协议（推荐，见 `docs/transport-migration-sse-to-streamable-http.md`）

老客户端仍走 SSE 时，两者并存是合法的（不同路径）。

### 决策 3 — LLM/Embedding 后端

生产环境不能依赖开发者机器上的 Hyperspace 本地代理。三种可选：

| 选项 | 参数 | 说明 |
|---|---|---|
| **企业 Hyperspace 代理** | `ANTHROPIC_BASE_URL=https://hyperspace.internal.sap` | SAP 内部推荐 |
| **官方 Anthropic API** | `ANTHROPIC_BASE_URL=https://api.anthropic.com` | 需要单独付费账户 |
| **自建 Anthropic 兼容网关** | 任意 URL | 需保证响应格式与 SDK 兼容 |

Embedding 同理，指向 OpenAI 兼容 endpoint 即可。

---

## 2. 前置条件（Prerequisites）

### 2.1 基础设施

- Kubernetes ≥ 1.29（生产建议）
- 持久卷 `StorageClass`（Qdrant 需要 PVC，即便外挂也建议同集群备份卷）
- Ingress Controller（NGINX / Istio / Gardener 提供的 istio ingress）
- cert-manager 或等价 TLS 证书方案

### 2.2 网络

- 出向：能到 GitHub API（`api.github.com` 或 `github.tools.sap`）用于 ingestion
- 出向：能到 LLM / Embedding endpoint
- 入向：给 AI Agent 客户端的 TLS 入口
- **零信任场景**：MCP server ↔ Qdrant 建议内网，Ingress 只暴露 MCP 端

### 2.3 凭据 (以 Kubernetes Secret 交付，禁止 ConfigMap)

需要下列 Secret（示例名，可按团队约定改）：

| Secret | 键 | 用途 |
|---|---|---|
| `gardener-mcp-github` | `GITHUB_TOKEN` | github.com 爬取 |
| `gardener-mcp-github-sap` | `GITHUB_SAP_TOKEN` | github.tools.sap 爬取（可选） |
| `gardener-mcp-llm` | `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` | LLM 调用 |
| `gardener-mcp-embed` | `HYPERSPACE_OPENAI_BASE_URL`（如有 key 也在此） | Embedding 调用 |
| `gardener-mcp-qdrant` | `QDRANT_URL`, `QDRANT_API_KEY` | 外部 Qdrant（决策 1 选 B 时必需） |
| `gardener-mcp-alerts` | `GARDENER_MCP_ALERT_WEBHOOK_URL` | ingestion 失败告警（Slack/Teams webhook） |

**绝对禁止**：把 token 写死进 Helm values 或 Docker image。所有敏感值通过 `envFrom.secretRef` 注入。

---

## 3. 部署步骤

### 3.1 构建并推送镜像

镜像来自 `docker/` 下的多阶段 Dockerfile（本地和云端 **同一份**）：

```bash
docker build \
  -t <your-registry>/gardener-ai-mcp:<git-sha> \
  -f docker/Dockerfile .

docker push <your-registry>/gardener-ai-mcp:<git-sha>
```

推荐 tag 策略：`<git-sha>` + 语义化 `vX.Y.Z`，避免 `latest`。

### 3.2 准备 `values.override.yaml`

以生产（外部 Qdrant + streamable-http）为例：

```yaml
image:
  repository: your-registry.example.com/gardener-ai-mcp
  tag: v1.2.3
  pullPolicy: IfNotPresent

replicaCount: 2

qdrant:
  enabled: false                # 使用外部 Qdrant

env:
  MCP_TRANSPORT: streamable-http
  GARDENER_MCP_TRANSPORT: streamable-http
  GARDENER_MCP_EMBEDDING_MODEL: text-embedding-3-small
  GARDENER_MCP_EMBEDDING_DIMENSIONS: "1536"

envFrom:
  - secretRef: {name: gardener-mcp-github}
  - secretRef: {name: gardener-mcp-github-sap}
  - secretRef: {name: gardener-mcp-llm}
  - secretRef: {name: gardener-mcp-embed}
  - secretRef: {name: gardener-mcp-qdrant}

resources:
  requests: {cpu: 200m, memory: 512Mi}
  limits:   {cpu: 1,     memory: 2Gi}

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: mcp-gardener.example.com
      paths: [{path: /, pathType: Prefix}]
  tls:
    - hosts: [mcp-gardener.example.com]
      secretName: mcp-gardener-tls

podDisruptionBudget:
  enabled: true
  minAvailable: 1

networkPolicy:
  enabled: true                 # 只允许来自 ingress 的入向
```

### 3.3 安装 / 升级

```bash
helm upgrade --install gardener-ai-mcp ./helm \
  --namespace gardener-mcp \
  --create-namespace \
  --values values.override.yaml \
  --wait --timeout 5m
```

### 3.4 首次数据装载（ingestion）

集群空跑 = 空的 Qdrant = 所有 RAG 工具返回空。**必须执行首次 ingestion**：

- **推荐**：CI 中定时执行（GitHub Actions cron `0 2 * * *`），从 CI 环境访问外部 Qdrant 直接写入
- **备选**：在集群里跑一次性 Job：

```bash
kubectl -n gardener-mcp create job --from=cronjob/gardener-ai-mcp-ingest ingest-manual-$(date +%s)
```

ingestion 覆盖以下 collection：
- `gardener_docs` — 文档 + 提案
- `gardener_issues` — GitHub issue（含 SAP 内部）
- `gardener_prs` — Pull request（脚本正在补齐）
- `gardener_code` — 源码（可选）

### 3.5 健康检查

```bash
kubectl -n gardener-mcp rollout status deployment/gardener-ai-mcp
kubectl -n gardener-mcp get pods,svc,ingress
curl -k https://mcp-gardener.example.com/health          # readiness probe
```

在客户端（Claude Code / 自研 Agent）配置：

```json
"gardener-ai-mcp": {
  "url": "https://mcp-gardener.example.com/mcp",
  "transport": "streamable-http"
}
```

---

## 4. 运维要点

### 4.1 可观测性

- **Prometheus**：Helm chart 暴露 `/metrics`（若开启）。关注：
  - `mcp_tool_invocations_total{tool=...}`
  - `qdrant_client_request_duration_seconds`
  - `embedding_api_error_total`
- **日志**：结构化 JSON，字段包含 `tool`、`collection`、`latency_ms`
- **Trace**：如启用 OpenTelemetry，接入企业 tracing 后端

### 4.2 备份

- Qdrant 快照：`POST /collections/{name}/snapshots`，落地到对象存储（S3/GCS）
- 建议每日 + 每次 ingestion 后 + 重大升级前

### 4.3 升级流程

1. 灰度：新版本先跑在 canary namespace，重放少量真实请求
2. 检查向量维度：若 embedding 模型换代（例如 1536 → 3072），**必须重建 collection**（旧向量不能与新向量共存）
3. Helm `--atomic --wait`，失败自动回滚
4. 数据库 schema 变更（COLLECTIONS 列表变化）需先执行数据迁移脚本

### 4.4 安全

- Secret 只经 CSI Secret Store / External Secrets 注入，禁止裸 Secret 提交 Git
- Ingress 至少 TLS 1.2；生产建议启用 mTLS 供 Agent 认证
- Network Policy：默认拒绝，仅放通：
  - Ingress → MCP Pod（8080）
  - MCP Pod → Qdrant（6333）
  - MCP Pod → LLM/Embedding endpoint（443）
  - MCP Pod → GitHub API（443）

---

## 5. 故障排查（云端）

| 症状 | 定位命令 | 常见原因 |
|---|---|---|
| Pod `CrashLoopBackOff`，日志 `VectorStoreError` | `kubectl logs -f deploy/gardener-ai-mcp` | `QDRANT_URL` 错、Qdrant Service 未就绪 |
| `401 Unauthorized` from LLM | 检查 `ANTHROPIC_AUTH_TOKEN` Secret 是否被正确挂载 | Secret 未更新或 Pod 未 rollout |
| Ingestion Job 长时间不结束 | `kubectl -n gardener-mcp get jobs` | GitHub API 限流；查 rate-limit header |
| RAG 命中率骤降 | 对比新旧 Pod 的 `EMBEDDING_MODEL` env | 意外切换 embedding 模型导致向量空间不一致 |
| `Collection doesn't exist` | `kubectl exec` 进 MCP Pod，`curl $QDRANT_URL/collections` | ingestion 从未跑过，或 collection 名不匹配 |

---

## 6. 与本地开发的差异一览

| 维度 | 本地 (kind) | 云端 (生产 K8s) |
|---|---|---|
| 传输协议 | stdio（Claude Code 直接 spawn） | streamable-http / SSE（Ingress 暴露） |
| Qdrant | Helm subchart，同集群 StatefulSet | **外部托管** 或独立命名空间 |
| LLM 代理 | 本机 `:6655` Hyperspace daemon | 企业 Hyperspace / Anthropic 官方 endpoint |
| 端口暴露 | `kubectl port-forward`（由 `~/.zshrc` 自动化） | Ingress + TLS |
| Ingestion | 手动 `python scripts/ingest_*.py` | CI cron + 集群 CronJob |
| 凭据来源 | `.env` 文件 | Kubernetes Secret + External Secrets |
| 可观测性 | Pod 日志 + 本地 curl | Prometheus + OTel + 结构化日志 |
| 升级方式 | `helm upgrade`（无风险） | Canary + `--atomic` + 备份 |

---

## 7. 快速核对清单（上线前）

- [ ] 镜像已推到私有 registry，且带 SBOM/签名（Cosign 可选）
- [ ] 所有 Secret 通过 External Secrets / CSI 注入，Git 里零明文
- [ ] `values.override.yaml` 走 GitOps 管理（Argo CD / Flux）
- [ ] Ingress TLS 证书有效，且 cert-manager 自动续签就位
- [ ] NetworkPolicy 生效，默认拒绝
- [ ] Ingestion CronJob 已就位，首次 run 成功，Qdrant 里 collection 齐全
- [ ] Prometheus scrape target 已配置
- [ ] Qdrant 备份定时任务已就位，至少完成一次成功恢复演练
- [ ] AI Agent 端配置指向新 URL，`/mcp` 状态 `connected`
- [ ] 生产运行手册（Runbook）中列出本文档链接

上线成功的定义：新 Claude 会话中调用 `search_docs` 返回真实 Gardener 内容，端到端延迟 < 3s（p95）。
