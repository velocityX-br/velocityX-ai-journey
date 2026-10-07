# 本地运行 gardener-ai-mcp 服务器 — 依赖与环境说明

> 面向读者：想在本地 macOS/Linux 开发机上让 Claude Code、Claude Desktop 或任何 MCP 客户端使用 `gardener-ai-mcp` RAG 工具的开发者。
> 姊妹文档：`docs/deploy-cloud-zh.md`（云端 / 生产环境部署）。

---

## 1. 运行时拓扑（先理解全景）

本地运行有 **两种执行模式**，二者共享同一份 Qdrant 数据：

| 模式 | 谁启动进程 | 网络暴露 | 面向 |
|---|---|---|---|
| **A. stdio 子进程**（默认） | Claude Code / Claude Desktop 通过 `~/.claude.json` 自行 `spawn` | 无网络端口，走 stdin/stdout | 单机开发者 |
| **B. SSE / Streamable-HTTP** | 你手动 `uv run` 或 kind 里的 Pod | `http://localhost:8080/sse` 或 `/mcp` | 多客户端、容器化 |

**关键事实**：即使跑 A（stdio），MCP 子进程仍然需要能够 TCP 连接到 Qdrant（默认 `http://localhost:6333`）和 Hyperspace 代理（默认 `http://localhost:6655`）。缺一个连接就会得到 `VectorStoreError: All connection attempts failed`。

架构图：

```
                     ┌──────────────────────────┐
Claude Code  ──stdio──▶  gardener_mcp.server    │
    or                │  (python subprocess)     │
Claude Desktop        └────────────┬─────────────┘
                                   │ HTTP
                    ┌──────────────┼──────────────┐
                    ▼                             ▼
       ┌──────────────────────┐    ┌───────────────────────────┐
       │ Qdrant :6333         │    │ Hyperspace Proxy :6655    │
       │ (kind Pod, 端口转发)  │    │ (本机 SAP CLI daemon)     │
       └──────────────────────┘    └───────────────────────────┘
```

---

## 2. 必装依赖（Prerequisites）

### 2.1 系统工具

| 工具 | 最低版本 | 用途 | 安装 |
|---|---|---|---|
| Python | 3.12 | MCP 服务器运行时 | `brew install python@3.12` |
| `uv` | 0.5+ | 依赖管理 + 启动 | `brew install uv` |
| Docker | 27+ | 承载 kind 集群 | https://docs.docker.com/get-docker/ |
| `kind` | 0.22+ | 本地 K8s 集群（承载 Qdrant） | `brew install kind` |
| `kubectl` | 1.29+ | 集群操作 + port-forward | `brew install kubectl` |
| Helm | 3.17+ | 部署 Helm Chart | `brew install helm` |
| `lsof` | 系统自带 | 端口占用检测 | — |

一键校验：

```bash
python3.12 --version && uv --version && docker version >/dev/null \
  && kind version && kubectl version --client && helm version
```

### 2.2 凭据（Credentials）

| 凭据 | 用途 | 获取 |
|---|---|---|
| **SAP Hyperspace Bearer Token** | LLM (Anthropic) + Embeddings (OpenAI 兼容) 调用 | SAP CIA 登录，token 写入 `~/.local/cia_token/.cia_token` |
| **GitHub PAT (github.com)** | 增量爬取 `gardener/*` 仓库 | https://github.com/settings/tokens → `read:repo` |
| **GitHub PAT (github.tools.sap)**（可选） | SAP 内部 issue 爬取 | https://github.tools.sap/settings/tokens → `read:repo` |

### 2.3 本地长期运行的辅助进程

`gardener-ai-mcp` 假定这两个 endpoint **持续可达**：

1. **Hyperspace 代理** `http://localhost:6655`
   - 由 SAP CIA CLI 或类似工具在本机守护
   - 提供 `/anthropic/` 和 `/openai/v1` 两个路径
2. **Qdrant** `http://localhost:6333`
   - 由 kind 集群里的 `svc/gardener-ai-mcp-qdrant` 通过 `kubectl port-forward` 暴露

---

## 3. 首次安装（一次性）

### 3.1 克隆并同步依赖

```bash
cd ~/Workdir/Github/veloxityX-ai-journey/mcp-servers/gardener-ai-mcp
uv sync                              # 生成 .venv、锁定 uv.lock 中的依赖
```

### 3.2 起 kind 集群 + Helm 部署

项目自带脚本（如存在）：

```bash
./scripts/setup_kind.sh              # 创建 kind 集群 'gardener-ai-mcp'
                                     # 部署 helm/ 下的 chart
                                     # 命名空间：gardener-mcp
```

如果不用脚本，等价手动步骤见 `docs/deploy-local.md`。

验证：

```bash
kubectl --context kind-gardener-ai-mcp -n gardener-mcp get pods
# 期望看到:
#   gardener-ai-mcp-xxxx           Running
#   gardener-ai-mcp-qdrant-0       Running
```

### 3.3 环境变量（`.env`）

```bash
cp .env.example .env
```

**最少必填**（本地开发默认值即可，凭据除外）：

```bash
GITHUB_TOKEN=<你的 github.com PAT>
GITHUB_SAP_TOKEN=<你的 github.tools.sap PAT>       # 可选
ANTHROPIC_AUTH_TOKEN=<Hyperspace Bearer>
ANTHROPIC_BASE_URL=http://localhost:6655/anthropic/
HYPERSPACE_OPENAI_BASE_URL=http://localhost:6655/openai/v1
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=                                     # 本地为空
```

> **优先级规则**：所有变量都有 `GARDENER_MCP_*` 前缀版本，前缀版本 **覆盖** 无前缀版本。这样可以让 CI/云端和本地开发共存，不会冲突。

---

## 4. 每次开机启动（这就是我们改 `~/.zshrc` 的原因）

**问题**：kind 集群 Pod 存在，但 Pod 的端口默认 **不会** 暴露到 host。要让本地 stdio MCP 进程能访问，必须持续维护两个 port-forward：

- `svc/gardener-ai-mcp        8080:8080`   （MCP HTTP/SSE，可选）
- `svc/gardener-ai-mcp-qdrant 6333:6333`   （**Qdrant，必需**）

`~/.zshrc` 里已经安装了自动启动函数 `_start_gardener_mcp_pf`，它会：

1. 探测 `kind-gardener-ai-mcp` context 是否存在，缺失时用 `kind export kubeconfig` 恢复
2. 对两个 service，各自：
   - 检查目标是否可达（`kubectl get svc`）
   - 检查本地端口是否已被占用（`lsof`）
   - 如空闲则后台启动 `kubectl port-forward`，`disown` 摆脱 shell 生命周期
3. 日志分别写到 `/tmp/gardener-mcp-pf.log` 和 `/tmp/gardener-qdrant-pf.log`

**手动触发（无需重开终端）**：

```bash
source ~/.zshrc
lsof -iTCP:6333 -sTCP:LISTEN    # 期望看到 kubectl 进程
lsof -iTCP:8080 -sTCP:LISTEN
```

---

## 5. 把 MCP 挂到 Claude Code

`~/.claude.json` 已经注册好（示例结构）：

```json
"gardener-ai-mcp": {
  "command": "uv",
  "args": [
    "--directory",
    "/Users/I577081/Workdir/Github/veloxityX-ai-journey/mcp-servers/gardener-ai-mcp",
    "run", "python", "-m", "gardener_mcp.server"
  ],
  "env": {}
}
```

`env` 留空表示继承 shell 环境变量。也可以显式写入 `QDRANT_URL` 等以隔离配置。

**加载/重载**：MCP 工具只在 Claude Code **启动新会话** 时加载 — 中途不会热加载。若改了 `.env` 或 `~/.claude.json`，退出当前会话再进即可。

会话里键入 `/mcp` 可以确认 gardener-ai-mcp 状态为 `connected`。

---

## 6. 冒烟测试（Smoke Test）

启动一个新 Claude Code 会话后，让它调用：

- `mcp__gardener-ai-mcp__search_docs`  — 应返回 Gardener 文档片段
- `mcp__gardener-ai-mcp__search_issues` — 应返回 GitHub issue 片段

或用 curl 直接打 HTTP endpoint（如果你保留了 SSE 模式）：

```bash
curl -s http://localhost:8080/sse | head -5
```

---

## 7. 常见故障排查

| 症状 | 根因 | 修复 |
|---|---|---|
| `VectorStoreError: All connection attempts failed` | Qdrant 6333 端口转发没起 / 挂了 | `source ~/.zshrc` 重启转发；检查 `/tmp/gardener-qdrant-pf.log` |
| `VectorStoreError` `Collection ... doesn't exist` | 只跑过部分 ingestion，某些 collection（如 `gardener_prs`）未生成 | 跑 `python scripts/ingest_*.py`，或使用现存的 collection 名 |
| HTTP 401 from Hyperspace | `~/.local/cia_token/.cia_token` 过期 | 重新 SAP CIA 登录，令 token 刷新 |
| Claude Code `/mcp` 里工具没出现 | stdio 子进程崩溃（缺 env 或 uv 找不到项目） | `ps aux \| grep gardener_mcp`；手动跑 `uv --directory <path> run python -m gardener_mcp.server`，看报错 |
| 端口 6333 已占用但没数据 | 之前的转发指向旧 Pod / 死亡 | `lsof -iTCP:6333`、`kill -9 <PID>`、再 `source ~/.zshrc` |
| RAG 结果与你预期完全无关 | 向量维度不匹配（1024 vs 1536）或用错 embedding 模型 | 检查 `GARDENER_MCP_EMBEDDING_MODEL` 与集合创建时一致（`text-embedding-3-small` → 1536 维） |

---

## 8. 一览：本地全套依赖清单

**运行时**
- Python 3.12
- Docker Desktop（已启动）
- kind 集群 `gardener-ai-mcp`，命名空间 `gardener-mcp`，包含 pods:
  - `gardener-ai-mcp-*`（MCP server，可选）
  - `gardener-ai-mcp-qdrant-0`（**必需**，向量库）

**本机后台进程**
- Hyperspace 代理 `:6655`（SAP CIA daemon）
- `kubectl port-forward` × 2（由 `~/.zshrc` 维护，指向 `:8080` 和 `:6333`）

**Python 环境**
- `.venv/` 由 `uv sync` 生成，含 FastMCP、qdrant-client、langchain 等

**凭据文件**
- `.env`（不提交）
- `~/.local/cia_token/.cia_token`（Hyperspace bearer）

**MCP 客户端配置**
- `~/.claude.json` 中的 `gardener-ai-mcp` 条目

这条链上任意一项缺失，工具调用就会失败。逐项对照，问题基本能定位到具体一环。
