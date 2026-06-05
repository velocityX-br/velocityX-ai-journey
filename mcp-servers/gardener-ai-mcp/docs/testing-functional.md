# 功能测试与本地部署验证指南

## 文档范围说明

本文档是 `docs/deploy-local.md` 的配套指南。`deploy-local.md` 侧重于基础设施搭建（kind 集群、Docker 镜像构建、Helm 部署），本文档侧重于**应用功能验证**：确认 MCP 服务器已正确启动，全部 7 个工具可被 MCP 客户端调用并返回符合预期的结果。

两份文档应配合使用：先完成 `deploy-local.md` 中的基础设施搭建，再按本文档执行功能验证。

---

## 1. 概述

### 1.1 本文档目标

验证 `gardener-ai-mcp` MCP 服务器在本地环境中正确运行，包括：

- 服务器以 stdio 或 SSE 模式无错启动
- FastMCP 框架正确向 MCP 客户端暴露全部 7 个工具
- 每个工具接受合法输入并返回符合 `ToolSearchResult` 或 `str` 模式的响应
- Qdrant 向量数据库与服务器之间的连接正常
- Hyperspace LLM 代理（`root_cause_analysis` 工具专用）连接正常
- GitHub API 访问正常（`search_issues`、`search_prs`、`search_code` 工具依赖）

### 1.2 两种测试路径

本文档提供两条独立的验证路径，开发者可根据当前阶段选择：

**路径 A：本地直接运行（推荐用于日常开发）**

直接在宿主机上以 `uv run` 执行服务器，无需 Docker 或 Kubernetes。测试迭代速度最快，适合工具开发、调试和单元测试阶段。前提条件：本机安装了 Python 3.12+、uv，以及一个本地 Qdrant Docker 容器。

**路径 B：kind 集群验证（推荐用于发版前验证）**

将完整镜像部署到本地 kind 集群，通过 `kubectl port-forward` 暴露端口后执行功能验证。此路径与生产环境一致（相同 Docker 镜像、相同 Helm chart、相同 Kubernetes 原语），适合集成测试和发版前回归。

### 1.3 工具架构速览

服务器入口点为 `gardener_mcp/server.py`，通过 FastMCP 的 lifespan 机制在启动时构建一个 `AppContext` 单例，其中包含：

- `semantic_retriever`：面向 `gardener_docs` collection 的默认检索器
- `embedder`：调用 Hyperspace OpenAI 兼容端点生成向量
- `vector_store`：Qdrant 客户端
- `hybrid_retriever`：跨四个 collection 的 RRF 融合检索器（供 `root_cause_analysis` 使用）
- `anthropic_client`：调用 Hyperspace Anthropic 兼容端点的 LLM 客户端

所有工具通过 `gardener_mcp/tools.py` 中的 `register_tools(mcp_app)` 函数注册，工具输入/输出模型定义在 `gardener_mcp/models.py` 中。

---

## 2. 前置条件检查

在执行任何验证步骤之前，逐条确认以下前置条件已满足。

### 2.1 必要工具

```bash
# Python 版本必须为 3.12 或更高
python --version
# 期望输出：Python 3.12.x

# uv 包管理器
uv --version
# 期望输出：uv 0.5.x 或更高

# 项目依赖已安装到 .venv
uv sync
# 成功时静默退出，若有依赖冲突会打印错误
```

### 2.2 环境配置文件

```bash
# 确认 .env 文件存在（从 .env.example 复制）
ls -la .env
# 查看关键字段是否填写（不要将 .env 提交到版本库）
grep -E "^(ANTHROPIC_AUTH_TOKEN|GITHUB_TOKEN|QDRANT_URL|HYPERSPACE_OPENAI_BASE_URL)" .env
```

**路径 A 必填环境变量：**

```
ANTHROPIC_AUTH_TOKEN=<hyperspace bearer token>
ANTHROPIC_BASE_URL=http://localhost:6655/anthropic/
HYPERSPACE_OPENAI_BASE_URL=http://localhost:6655/openai/v1
GITHUB_TOKEN=<github PAT with read:repo scope>
QDRANT_URL=http://localhost:6333
```

`GITHUB_TOKEN` 是唯一在 `config/settings.py` 中声明为必填（无默认值）的变量；缺少此变量时 `pydantic-settings` 会在启动时抛出 `ValidationError`，服务器无法启动。`ANTHROPIC_AUTH_TOKEN` 默认为空字符串，服务器可启动，但调用 `root_cause_analysis` 工具时会收到 LLM 认证错误。

**环境变量双前缀说明：**

`config/settings.py` 通过 `AliasChoices` 机制同时识别 `GARDENER_MCP_*` 前缀和无前缀两种形式，优先级高的优先：`GARDENER_MCP_ANTHROPIC_AUTH_TOKEN` > `ANTHROPIC_AUTH_TOKEN`。这允许在同一 shell 会话中同时运行 Claude Code 和 MCP 服务器而不产生变量冲突。

### 2.3 路径 A 专属：Qdrant 本地实例

```bash
# 检查 Qdrant 是否已在运行
curl -s http://localhost:6333/healthz
# 期望输出：{"title":"qdrant - vector search engine","version":"...","commit":"..."}

# 若未运行，则启动（见第 3.1 节）
```

### 2.4 路径 B 专属：Docker 与 kind

```bash
# Docker 守护进程正在运行
docker info | grep "Server Version"

# kind 已安装
kind version
# 期望输出：kind v0.22.x go1.x.x ...

# kubectl 已安装
kubectl version --client --short
```

---

## 3. 路径 A：本地直接运行

### 3.1 启动 Qdrant（本地 Docker 容器）

```bash
docker run -d \
  --name qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v qdrant_storage:/qdrant/storage \
  qdrant/qdrant:latest
```

参数说明：

- `-p 6333:6333`：REST API 端口，`SemanticRetriever` 和 `HybridRetriever` 通过此端口查询
- `-p 6334:6334`：gRPC 端口（供未来 gRPC 客户端使用，当前实现使用 REST）
- `-v qdrant_storage:/qdrant/storage`：持久化向量数据，容器重启后数据不丢失

验证 Qdrant 健康状态：

```bash
curl -s http://localhost:6333/healthz
# 成功响应示例：
# {"title":"qdrant - vector search engine","version":"1.x.x","commit":"..."}
```

访问 Qdrant 管理控制台（可选，用于可视化验证 collection 和向量数量）：

```
http://localhost:6333/dashboard
```

若容器已存在但处于停止状态：

```bash
docker start qdrant
```

### 3.2 运行数据摄取（首次使用必须执行）

在 MCP 工具能够返回真实结果之前，必须先将数据写入 Qdrant 各 collection。

```bash
uv run python scripts/ingest_docs.py
```

摄取脚本执行内容：

1. 从 GitHub `gardener/documentation` 仓库拉取 Markdown 文档，写入 `gardener_docs` collection
2. 从 GitHub `gardener/gardener` 仓库拉取 issues，写入 `gardener_issues` collection
3. 从 GitHub `gardener/gardener` 仓库拉取 pull requests，写入 `gardener_prs` collection
4. 从 GitHub `gardener/gardener` 仓库拉取 Go 源代码，写入 `gardener_code` collection

摄取耗时取决于网络速度和 GitHub API 限速，通常需要 5~20 分钟。

摄取完成后，验证 collection 已创建：

```bash
curl -s http://localhost:6333/collections | python3 -m json.tool
```

期望在响应中看到以下四个 collection：

```json
{
  "result": {
    "collections": [
      {"name": "gardener_docs"},
      {"name": "gardener_issues"},
      {"name": "gardener_prs"},
      {"name": "gardener_code"}
    ]
  }
}
```

若跳过摄取步骤，`search_docs`、`search_issues`、`search_prs`、`search_code`、`search_proposals`、`rag_retrieve` 工具均会返回空列表 `[]`，`root_cause_analysis` 工具会因检索到零文档而导致 LLM 生成质量极低的分析结果。

### 3.3 以 stdio 模式启动服务器

```bash
uv run python -m gardener_mcp.server
```

**重要说明：** stdio 模式下，服务器启动后不会打印任何可见提示，而是阻塞等待来自 stdin 的 JSON-RPC 消息。这是**正常现象**，不是卡死。stdio 传输是 MCP 协议的标准行为：MCP 客户端（如 Claude Desktop、MCP Inspector）通过进程的 stdin/stdout 与服务器通信。

直接在终端中执行此命令时，服务器会显示为"无响应"——这正是预期行为。应使用 MCP Inspector 或 Claude Desktop 来与 stdio 模式的服务器通信（见第 4 节）。

启动时应在 stderr 看到以下日志（INFO 级别）：

```
INFO     Starting Gardener AI MCP server — qdrant_url=http://localhost:6333 anthropic_model=anthropic--claude-sonnet-latest
INFO     AppContext built successfully.
```

若看到 `pydantic_core.ValidationError`，检查 `.env` 文件中 `GITHUB_TOKEN` 是否已填写。

### 3.4 以 SSE 模式启动服务器

SSE（Server-Sent Events）模式将服务器作为 HTTP 服务运行，适合使用 MCP Inspector 进行测试，也适合与 Claude Desktop 通过 HTTP 连接。

方式一：使用内联环境变量覆盖（不修改 `.env` 文件）

```bash
MCP_TRANSPORT=sse uv run python -m gardener_mcp.server
```

方式二：在 `.env` 文件中设置（持久化配置）

在 `.env` 文件中取消注释或添加：

```
MCP_TRANSPORT=sse
```

然后启动：

```bash
uv run python -m gardener_mcp.server
```

注意：`config/settings.py` 同时识别 `MCP_TRANSPORT` 和 `GARDENER_MCP_TRANSPORT`，两者等效，后者优先级更高。

SSE 模式启动成功后，服务器会监听 `http://0.0.0.0:8080`。验证：

```bash
curl -s http://localhost:8080/
# 应返回 FastMCP 服务器信息（JSON 或 HTML，具体格式取决于 FastMCP 版本）
```

SSE 端点地址（MCP 客户端连接地址）：

```
http://localhost:8080/sse
```

---

## 4. 使用 MCP Inspector 验证工具

MCP Inspector 是 Anthropic 提供的官方调试工具，可在浏览器中直观查看工具列表并手动调用每个工具。这是验证工具注册和基本功能最快的方式。

### 4.1 安装 MCP Inspector

方式一：使用 npx（无需全局安装，推荐）

```bash
npx @modelcontextprotocol/inspector
```

方式二：全局安装后运行

```bash
npm install -g @modelcontextprotocol/inspector
mcp-inspector
```

Inspector 启动后会在终端打印本地 URL，默认为 `http://localhost:5173`。

### 4.2 连接到服务器（SSE 模式）

**前提：** 服务器已以 SSE 模式运行（见第 3.4 节）。

1. 打开浏览器，访问 `http://localhost:5173`
2. 在连接配置区域选择 Transport: **SSE**
3. 填写 URL：`http://localhost:8080/sse`
4. 点击 **Connect**

连接成功后，Inspector 左侧面板会显示服务器名称 `gardener-ai-mcp` 和工具列表。

若连接失败，确认：

- 服务器终端中没有报错
- 服务器确实以 SSE 模式启动（`MCP_TRANSPORT=sse`），而非 stdio 模式
- 端口 8080 未被其他进程占用：`lsof -i :8080`

### 4.3 连接到服务器（stdio 模式）

**前提：** 服务器未在运行（Inspector 将启动服务器子进程）。

1. 在 Inspector 连接配置中选择 Transport: **Stdio**
2. Command: `uv`
3. Args: `run python -m gardener_mcp.server`
4. Working directory: 填写项目根目录的绝对路径，例如 `/Users/<你的用户名>/Workdir/Claude_Gardener/gardener-ai-mcp`
5. 点击 **Connect**

Inspector 会在后台启动 `uv run python -m gardener_mcp.server` 子进程，通过 stdin/stdout 与其通信。

### 4.4 验证工具列表

无论通过哪种 transport 连接成功后，在 Inspector 的 **Tools** 面板（或等效的工具选项卡）中应看到以下 7 个工具，顺序可能不同：

| 工具名称 | 对应 collection | 返回类型 |
|---|---|---|
| `search_docs` | `gardener_docs` | `list[ToolSearchResult]` |
| `search_issues` | `gardener_issues` | `list[ToolSearchResult]` |
| `search_prs` | `gardener_prs` | `list[ToolSearchResult]` |
| `search_proposals` | `gardener_docs`（过滤 `content_type=proposal`）| `list[ToolSearchResult]` |
| `search_code` | `gardener_code` | `list[ToolSearchResult]` |
| `rag_retrieve` | 调用方指定 | `list[ToolSearchResult]` |
| `root_cause_analysis` | 全部四个（RRF 融合）| `str` |

若工具数量少于 7，检查 `gardener_mcp/tools.py` 中的 `register_tools` 函数是否完整，以及 `gardener_mcp/server.py` 中是否调用了 `register_tools(mcp)`。

---

## 5. 7 个工具功能验证

以下各小节给出每个工具的标准测试输入、期望输出结构和验证要点。所有测试均通过 MCP Inspector 的工具调用界面执行，或通过任何兼容 MCP 协议的客户端执行。

### 5.1 search_docs

**功能：** 对 `gardener_docs` Qdrant collection 执行语义（稠密向量）相似度搜索，返回 Gardener 文档片段。

**对应模型：** `SearchDocsInput`（`query: str`，`limit: int = 10`，`filters: dict | None = None`）

**测试输入：**

```json
{
  "query": "how to create a shoot cluster",
  "limit": 3
}
```

**期望输出结构（`list[ToolSearchResult]`）：**

```json
[
  {
    "id": "abc123...",
    "content": "To create a Shoot cluster, apply the following manifest...",
    "score": 0.87,
    "metadata": {
      "source": "https://github.com/gardener/documentation/...",
      "content_type": "doc"
    },
    "collection": "gardener_docs",
    "source": "https://github.com/gardener/documentation/..."
  }
]
```

**验证要点：**

- 返回列表非空（前提：已运行 `ingest_docs.py`）
- 每条结果的 `collection` 字段值为 `"gardener_docs"`
- `score` 值大于 0（通常在 0.3~0.95 之间）
- `content` 字段非空字符串

**空结果排查：**

若返回 `[]`，最可能的原因是 Qdrant 的 `gardener_docs` collection 为空。执行以下命令确认：

```bash
curl -s "http://localhost:6333/collections/gardener_docs" | python3 -m json.tool
```

若 collection 不存在或 `vectors_count` 为 0，执行 `uv run python scripts/ingest_docs.py`。

**附加测试（带过滤器）：**

```json
{
  "query": "worker pool configuration",
  "limit": 5,
  "filters": {"content_type": "doc"}
}
```

### 5.2 search_issues

**功能：** 对 `gardener_issues` Qdrant collection 执行语义搜索，返回来自 `gardener/gardener` GitHub 仓库的 issue 片段。`state` 参数被转换为 Qdrant payload 过滤条件。

**对应模型：** `SearchIssuesInput`（`query: str`，`limit: int = 10`，`state: str | None = None`，`labels: list[str] | None = None`）

**测试输入：**

```json
{
  "query": "shoot reconciliation error",
  "limit": 3,
  "state": "open"
}
```

**期望输出结构：**

```json
[
  {
    "id": "...",
    "content": "Issue #1234: Shoot cluster stuck in reconciling state...",
    "score": 0.82,
    "metadata": {
      "state": "open",
      "labels": ["kind/bug", "area/gardenlet"],
      "issue_number": 1234,
      "url": "https://github.com/gardener/gardener/issues/1234"
    },
    "collection": "gardener_issues",
    "source": "https://github.com/gardener/gardener/issues/1234"
  }
]
```

**验证要点：**

- `metadata` 中包含 `state` 字段，值与过滤参数一致（`"open"`）
- `metadata` 中包含 `labels` 字段（可为空列表）
- `collection` 字段值为 `"gardener_issues"`

**附加测试（带标签过滤）：**

```json
{
  "query": "DNS resolution failure",
  "limit": 5,
  "labels": ["kind/bug"]
}
```

### 5.3 search_prs

**功能：** 对 `gardener_prs` Qdrant collection 执行语义搜索，返回来自 `gardener/gardener` 的 pull request 片段。

**对应模型：** `SearchPRsInput`（`query: str`，`limit: int = 10`，`state: str | None = None`）

**测试输入：**

```json
{
  "query": "fix gardenlet controller",
  "limit": 3
}
```

**期望输出结构：**

```json
[
  {
    "id": "...",
    "content": "PR #5678: Fix gardenlet shoot controller reconciliation loop...",
    "score": 0.79,
    "metadata": {
      "pr_number": 5678,
      "state": "merged",
      "merged": true,
      "url": "https://github.com/gardener/gardener/pull/5678"
    },
    "collection": "gardener_prs",
    "source": "https://github.com/gardener/gardener/pull/5678"
  }
]
```

**验证要点：**

- `metadata` 中包含 `pr_number` 字段
- `metadata` 中包含 `merged` 字段（`true` 或 `false`）
- `collection` 字段值为 `"gardener_prs"`

**附加测试（过滤已合并 PR）：**

```json
{
  "query": "networking policy update",
  "limit": 5,
  "state": "closed"
}
```

### 5.4 search_proposals

**功能：** 在 `gardener_docs` collection 中以固定 payload 过滤条件 `content_type="proposal"` 搜索 Gardener Enhancement Proposals（GEP）。`search_proposals` 是 `search_docs` 的专用视图，不查询独立 collection。

**对应模型：** `SearchProposalsInput`（`query: str`，`limit: int = 10`）

**测试输入：**

```json
{
  "query": "network policy gardener",
  "limit": 5
}
```

**期望输出结构：**

```json
[
  {
    "id": "...",
    "content": "GEP-XX: Network Policy Management...",
    "score": 0.75,
    "metadata": {
      "content_type": "proposal",
      "source": "https://github.com/gardener/documentation/..."
    },
    "collection": "gardener_docs",
    "source": "https://..."
  }
]
```

**验证要点：**

- `metadata` 中的 `content_type` 字段值为 `"proposal"`（这是工具内部强制的过滤条件，`gardener_mcp/tools.py` 第 266 行：`filters: dict[str, Any] = {"content_type": "proposal"}`）
- `collection` 字段值为 `"gardener_docs"`（非独立 collection）

**空结果的特殊说明：**

若 `gardener_docs` collection 已有数据但此工具仍返回空列表，说明摄取的文档中没有 `content_type="proposal"` 的条目。此时检查 `scripts/ingest_docs.py` 中对 GEP 文档的分类逻辑。

### 5.5 search_code

**功能：** 对 `gardener_code` Qdrant collection 执行语义搜索，返回来自 Gardener 仓库的 Go 源代码片段。`repo` 参数限制结果来自特定仓库。

**对应模型：** `SearchCodeInput`（`query: str`，`limit: int = 10`，`repo: str | None = None`）

**测试输入：**

```json
{
  "query": "gardenlet reconcile shoot",
  "limit": 3,
  "repo": "gardener/gardener"
}
```

**期望输出结构：**

```json
[
  {
    "id": "...",
    "content": "func (r *Reconciler) reconcileShoot(ctx context.Context, shoot *gardencorev1beta1.Shoot) ...",
    "score": 0.88,
    "metadata": {
      "language": "go",
      "repo": "gardener/gardener",
      "file_path": "pkg/gardenlet/operation/shoot/reconciler.go",
      "url": "https://github.com/gardener/gardener/blob/..."
    },
    "collection": "gardener_code",
    "source": "https://github.com/gardener/gardener/blob/..."
  }
]
```

**验证要点：**

- `metadata` 中的 `language` 字段值为 `"go"`
- `metadata` 中的 `repo` 字段值为 `"gardener/gardener"`（与输入参数一致）
- `collection` 字段值为 `"gardener_code"`
- `content` 字段包含 Go 代码片段

**不带 repo 过滤的测试：**

```json
{
  "query": "shoot dns record controller",
  "limit": 5
}
```

### 5.6 rag_retrieve

**功能：** 低级 RAG 检索接口，调用方显式指定目标 Qdrant collection 名称。在工具内部动态创建 `SemanticRetriever` 实例（`gardener_mcp/tools.py` 第 334 行）。当需要对特定 collection 进行原始检索而不依赖 `search_*` 工具的预设逻辑时使用此工具。

**对应模型：** `RAGRetrieveInput`（`query: str`，`collection: str`，`limit: int = 10`，`filters: dict | None = None`）

**有效 collection 名称：**

- `gardener_docs`
- `gardener_issues`
- `gardener_prs`
- `gardener_code`

**测试输入（正常路径）：**

```json
{
  "query": "worker pool configuration",
  "collection": "gardener_docs",
  "limit": 5
}
```

**验证要点：**

- 返回结果的 `collection` 字段值与输入的 `collection` 参数一致（`"gardener_docs"`）
- `score` 大于 0

**错误输入验证（无效 collection 名称）：**

```json
{
  "query": "any query",
  "collection": "nonexistent_collection",
  "limit": 3
}
```

期望行为：工具应返回空列表 `[]` 或抛出包含有意义错误信息的异常。不应返回随机数据。具体错误行为取决于 Qdrant 对不存在 collection 的响应方式。

**附加测试（带过滤器）：**

```json
{
  "query": "reconcile loop",
  "collection": "gardener_issues",
  "limit": 5,
  "filters": {"state": "open"}
}
```

### 5.7 root_cause_analysis

**功能：** 最复杂的工具。执行流程：

1. 将 `symptom` 和 `context` 拼接为组合查询
2. 使用 `HybridRetriever`（RRF 融合）跨全部四个 collection 检索相关文档
3. 将检索结果格式化为带编号的 context block
4. 调用 `anthropic_client.messages.create`（SAP Hyperspace Anthropic 兼容端点）
5. 返回 LLM 生成的结构化根因分析文本

**对应模型：** `RootCauseAnalysisInput`（`symptom: str`，`context: str | None = None`，`limit: int = 5`）

**测试输入：**

```json
{
  "symptom": "Shoot cluster stuck in Reconciling state for more than 30 minutes",
  "context": "gardenlet logs show: failed to reconcile shoot: context deadline exceeded",
  "limit": 3
}
```

**期望输出结构（`str` 类型）：**

返回的文本应包含以下三部分结构（由系统提示强制要求，`gardener_mcp/tools.py` 第 404 行）：

```
1) Most likely root cause:
   [LLM 基于检索文档推断的根本原因]

2) Supporting evidence from retrieved documents:
   [引用检索到的文档片段作为证据]

3) Recommended remediation steps:
   [具体的修复建议步骤]
```

**验证要点：**

- 响应为非空字符串
- 响应包含三个结构化部分（根因、证据、修复步骤）
- 响应内容与 Shoot 集群故障主题相关（而非通用答案）

**Hyperspace 连接失败排查：**

若工具返回错误或空字符串，按以下顺序排查：

1. 检查 `ANTHROPIC_AUTH_TOKEN` 是否已在 `.env` 中设置为有效的 Hyperspace token
2. 检查 `ANTHROPIC_BASE_URL` 是否为 `http://localhost:6655/anthropic/`
3. 确认 Hyperspace 隧道（通常为 SSH 隧道或 VPN）已连接：`curl -s http://localhost:6655/` 应有响应
4. 检查 `ANTHROPIC_MODEL` 是否为 Hyperspace 支持的模型（默认 `anthropic--claude-sonnet-latest`）

**仅测试检索而不依赖 LLM 的方法：**

若 Hyperspace 不可用，可通过 `rag_retrieve` 工具单独验证检索层：

```json
{
  "query": "shoot cluster stuck in reconciling state context deadline exceeded",
  "collection": "gardener_docs",
  "limit": 5
}
```

---

## 6. 使用 Claude Desktop 验证（端到端）

Claude Desktop 端到端验证是功能验证的最高层次，可确认工具在真实 AI 代理对话场景中可被正常调用。

### 6.1 配置 claude_desktop_config.json

配置文件位置（macOS）：

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

**SSE 模式配置（推荐：服务器独立运行，Claude Desktop 通过 HTTP 连接）**

适用场景：服务器已以 SSE 模式运行，或部署在 kind 集群中通过 port-forward 暴露。

```json
{
  "mcpServers": {
    "gardener": {
      "transport": "sse",
      "url": "http://localhost:8080/sse"
    }
  }
}
```

**stdio 模式配置（Claude Desktop 启动服务器子进程）**

适用场景：开发阶段，希望 Claude Desktop 直接管理服务器进程生命周期。注意 `--directory` 参数必须替换为实际项目根目录的绝对路径。

```json
{
  "mcpServers": {
    "gardener": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/Users/<你的用户名>/Workdir/Claude_Gardener/gardener-ai-mcp",
        "python",
        "-m",
        "gardener_mcp.server"
      ],
      "env": {
        "ANTHROPIC_BASE_URL": "http://localhost:6655/anthropic/",
        "ANTHROPIC_AUTH_TOKEN": "<your-hyperspace-token>",
        "HYPERSPACE_OPENAI_BASE_URL": "http://localhost:6655/openai/v1",
        "GITHUB_TOKEN": "<your-github-pat>",
        "QDRANT_URL": "http://localhost:6333"
      }
    }
  }
}
```

**重要：** 包名为 `gardener_mcp`（带下划线），不是 `mcp`。`mcp/` 目录在 Phase 5 已重命名为 `gardener_mcp/` 以避免与 `mcp` PyPI 包（MCP SDK）冲突。若使用错误包名 `python -m mcp.server`，会导致 `ModuleNotFoundError` 或加载到 SDK 内部模块。

### 6.2 验证步骤

1. 保存 `claude_desktop_config.json`
2. 完全退出并重启 Claude Desktop（不是最小化，是退出进程后重新打开）
3. 打开新对话
4. 在对话框中输入以下验证提示：

```
使用 gardener MCP 工具搜索关于 shoot cluster 创建的文档，返回 3 条结果。
```

5. Claude Desktop 应在响应中显示正在调用 `search_docs` 工具的指示，并返回包含文档片段的结果。

**预期行为：**

- 对话界面出现工具调用指示（通常显示为"使用工具: search_docs"）
- 工具调用成功后返回文档内容
- 若工具列表未出现（Claude 回复"我没有访问 Gardener MCP 工具的能力"），重新检查配置文件路径和格式，并再次完全重启 Claude Desktop

---

## 7. 路径 B：kind 集群验证

本节为路径 B 的功能验证补充说明。kind 集群的基础设施搭建步骤（创建集群、构建镜像、安装 Helm chart）已在 `docs/deploy-local.md` 中完整描述，本节仅覆盖 **MCP 工具功能验证** 部分。

执行本节步骤的前提：`docs/deploy-local.md` 中的所有步骤已完成，`kubectl get pods -n gardener-mcp` 显示两个 Pod 均处于 `Running` 状态。

### 7.1 确认 Pod 状态与服务器日志

```bash
# 检查 Pod 状态
kubectl get pods -n gardener-mcp

# 期望输出：
# NAME                               READY   STATUS    RESTARTS   AGE
# gardener-ai-mcp-7d9f8b6c4-xk2pj   1/1     Running   0          45s
# gardener-ai-mcp-qdrant-0           1/1     Running   0          45s

# 跟踪 MCP 服务器日志（确认 AppContext 已成功构建）
kubectl logs -n gardener-mcp -l app.kubernetes.io/name=gardener-ai-mcp -f
```

服务器日志中应出现以下两行，表明 AppContext 已构建成功（含 Qdrant 连接建立、embedder 初始化等）：

```
INFO     Starting Gardener AI MCP server — qdrant_url=http://gardener-ai-mcp-qdrant:6333 ...
INFO     AppContext built successfully.
```

注意 kind 集群内部 Qdrant URL 为 `http://gardener-ai-mcp-qdrant:6333`（Kubernetes Service 名称），而非 `http://localhost:6333`。这由 Helm chart 自动注入。

### 7.2 Port-forward 后使用 Inspector 验证

```bash
# 在单独的终端中运行（保持前台运行）
kubectl port-forward -n gardener-mcp svc/gardener-ai-mcp 8080:8080
```

Port-forward 就绪后，服务器的 SSE 端点可通过 `http://localhost:8080/sse` 访问（Helm chart 将 `MCP_TRANSPORT` 默认设置为 `sse`）。

然后按照第 4.2 节和第 5 节的步骤，通过 MCP Inspector 验证所有 7 个工具。验证步骤与路径 A 完全相同——SSE 端点地址 `http://localhost:8080/sse` 一致。

### 7.3 Qdrant Dashboard 验证数据

```bash
# 在另一个单独的终端中运行
kubectl port-forward -n gardener-mcp svc/gardener-ai-mcp-qdrant 6333:6333
```

访问 Qdrant 管理控制台：

```
http://localhost:6333/dashboard
```

验证要点：

- **Collections 选项卡**：应看到 `gardener_docs`、`gardener_issues`、`gardener_prs`、`gardener_code` 四个 collection
- **每个 collection 的向量数量**：若已运行摄取，各 collection 应有大于 0 的向量计数

若 collection 为空，按照 `docs/deploy-local.md` 的"Running Ingestion"章节执行摄取（需要先 port-forward Qdrant 端口，然后运行 `QDRANT_URL=http://localhost:6333 uv run python scripts/ingest_docs.py`）。

---

## 8. 健康检查验证

`scripts/healthcheck.py` 脚本验证 Qdrant 连接是否正常，退出码 0 表示健康，非 0 表示异常。健康检查用于 Docker 容器的 `HEALTHCHECK` 指令和 Kubernetes 的 liveness probe。

**路径 A：在宿主机直接运行**

```bash
QDRANT_URL=http://localhost:6333 uv run python scripts/healthcheck.py
# 期望输出：OK: Qdrant healthy at http://localhost:6333
# 期望退出码：0
```

**路径 A：在运行中的 Docker 容器中验证**

```bash
# 首先获取容器 ID
docker ps --filter "name=qdrant" --format "{{.ID}}"

# 若 MCP 服务器也以容器运行
docker exec <mcp_container_id> python scripts/healthcheck.py
```

**路径 B：在 kind 集群的 Pod 中验证**

```bash
kubectl exec -n gardener-mcp deploy/gardener-ai-mcp -- python scripts/healthcheck.py
# 期望输出：OK: Qdrant healthy at http://gardener-ai-mcp-qdrant:6333
# 期望退出码：0
```

健康检查仅验证 Qdrant 连通性，不验证 LLM 代理连通性。若需测试完整的端到端连接（包括 Hyperspace），使用第 5.7 节的 `root_cause_analysis` 工具测试。

---

## 9. 常见问题排查

| 问题现象 | 可能原因 | 解决方法 |
|---|---|---|
| 服务器启动后终端无任何输出，光标闪烁 | 正常现象：stdio 模式下服务器等待 stdin 输入 | 使用 MCP Inspector（stdio 模式）或切换到 SSE 模式测试 |
| `search_docs` / `search_issues` 等返回空列表 `[]` | Qdrant collection 无数据（未执行摄取）| 执行 `uv run python scripts/ingest_docs.py` |
| `root_cause_analysis` 超时或返回空字符串 | Hyperspace LLM 代理连接失败 | 检查 `ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL`，确认 Hyperspace 隧道已连接 |
| `Connection refused` at port 8080 | 服务器未以 SSE 模式启动 | 设置 `MCP_TRANSPORT=sse`，重启服务器 |
| `Connection refused` at port 6333 | Qdrant 容器未运行 | 执行 `docker start qdrant` 或重新运行 `docker run` 命令 |
| MCP Inspector 提示 `Connection failed` | Transport 类型与服务器模式不匹配 | 确认 Inspector 选择的 Transport（SSE/stdio）与服务器启动模式一致 |
| `ModuleNotFoundError: No module named 'mcp.types'` | 错误使用了 `python -m mcp.server`（MCP SDK 内部模块）| 改为 `python -m gardener_mcp.server`（注意包名有下划线） |
| `pydantic_core.ValidationError: github_token`| `.env` 中未设置 `GITHUB_TOKEN` | 在 `.env` 中设置 `GITHUB_TOKEN=<your-pat>` |
| `search_proposals` 返回空列表，但其他 search 工具有结果 | 摄取的文档中无 `content_type="proposal"` 元数据 | 检查 `scripts/ingest_docs.py` 中 GEP 文档的分类逻辑 |
| `ErrImageNeverPull`（路径 B）| Docker 镜像未加载到 kind 节点 | 重新执行 `kind load docker-image gardener-ai-mcp:dev --name gardener-ai-mcp` |
| Pod `CrashLoopBackOff`（路径 B）| 缺少 Kubernetes Secret 中的必填变量 | 检查 `kubectl logs deploy/gardener-ai-mcp -n gardener-mcp`，重建 Secret |
| Inspector 显示少于 7 个工具 | `register_tools` 未完整注册所有工具 | 检查 `gardener_mcp/tools.py` 中所有 7 个 `@mcp_app.tool` 装饰器 |
| `rag_retrieve` 对无效 collection 返回意外数据 | Qdrant collection 名称大小写敏感 | 确认使用精确名称：`gardener_docs`、`gardener_issues`、`gardener_prs`、`gardener_code` |

---

## 10. 验证检查清单

开发者完成每一项后，将 `[ ]` 改为 `[x]` 记录进度。

```
## 本地功能验证检查清单

### 环境准备
- [ ] Python 3.12+ 已安装（`python --version` 输出 3.12.x）
- [ ] uv 已安装（`uv --version` 正常输出）
- [ ] `uv sync` 成功（无依赖冲突，.venv 目录已创建）
- [ ] .env 文件已从 .env.example 复制并配置
- [ ] GITHUB_TOKEN 已填写有效的 GitHub PAT（read:repo 权限）
- [ ] ANTHROPIC_AUTH_TOKEN 已填写有效的 Hyperspace token
- [ ] ANTHROPIC_BASE_URL 设置为 http://localhost:6655/anthropic/
- [ ] HYPERSPACE_OPENAI_BASE_URL 设置为 http://localhost:6655/openai/v1

### Qdrant
- [ ] Qdrant Docker 容器正在运行（`curl http://localhost:6333/healthz` 有响应）
- [ ] Qdrant Dashboard 可访问（http://localhost:6333/dashboard）
- [ ] `scripts/healthcheck.py` 退出码为 0

### 数据摄取
- [ ] `uv run python scripts/ingest_docs.py` 执行成功（无错误退出）
- [ ] Qdrant gardener_docs collection 有数据（Dashboard 中 vectors_count > 0）
- [ ] Qdrant gardener_issues collection 有数据
- [ ] Qdrant gardener_prs collection 有数据
- [ ] Qdrant gardener_code collection 有数据

### 服务器启动
- [ ] stdio 模式启动无报错（`uv run python -m gardener_mcp.server`）
- [ ] SSE 模式启动成功（`MCP_TRANSPORT=sse uv run python -m gardener_mcp.server`）
- [ ] SSE 模式下 http://localhost:8080/ 有响应

### MCP Inspector 连接
- [ ] MCP Inspector 通过 SSE 或 stdio transport 成功连接到服务器
- [ ] Tools 面板显示 7 个工具（search_docs, search_issues, search_prs,
      search_proposals, search_code, rag_retrieve, root_cause_analysis）

### 工具功能验证
- [ ] search_docs — 查询 "how to create a shoot cluster"，返回非空列表，
      collection 字段为 "gardener_docs"，score > 0
- [ ] search_issues — 查询 "shoot reconciliation error" + state="open"，
      metadata 包含 state 和 labels 字段
- [ ] search_prs — 查询 "fix gardenlet controller"，
      metadata 包含 pr_number 和 merged 字段
- [ ] search_proposals — 查询 "network policy gardener"，
      metadata.content_type = "proposal"
- [ ] search_code — 查询 "gardenlet reconcile shoot" + repo="gardener/gardener"，
      metadata.language = "go"
- [ ] rag_retrieve — 指定 collection="gardener_docs"，返回结果 collection 字段与输入一致
- [ ] rag_retrieve — 指定不存在的 collection 名，返回空列表或明确错误（不崩溃）
- [ ] root_cause_analysis — 输入 Shoot 集群故障症状，
      返回包含根因、证据、修复步骤的结构化非空文本

### Claude Desktop 端到端验证（可选）
- [ ] claude_desktop_config.json 已正确配置（SSE 或 stdio 模式）
- [ ] Claude Desktop 完全重启后工具可用
- [ ] 在对话中请求调用 search_docs 工具，Claude 成功返回文档内容
- [ ] 在对话中触发 root_cause_analysis，Claude 成功返回 LLM 分析结果

### 路径 B 额外验证（kind 集群）
- [ ] `kubectl get pods -n gardener-mcp` 显示两个 Pod 均为 Running
- [ ] `kubectl logs deploy/gardener-ai-mcp -n gardener-mcp` 包含
      "AppContext built successfully."
- [ ] `kubectl exec deploy/gardener-ai-mcp -n gardener-mcp -- python scripts/healthcheck.py`
      退出码为 0
- [ ] Port-forward 8080 后 MCP Inspector 可连接并调用所有 7 个工具
- [ ] Port-forward 6333 后 Qdrant Dashboard 显示四个 collection 均有数据
```

---

## 附录 A：快速参考 — Collection 与工具映射

| Qdrant Collection | 写入脚本 | 对应工具 |
|---|---|---|
| `gardener_docs` | `scripts/ingest_docs.py` | `search_docs`，`search_proposals`（过滤），`rag_retrieve` |
| `gardener_issues` | `scripts/ingest_docs.py` | `search_issues`，`rag_retrieve` |
| `gardener_prs` | `scripts/ingest_docs.py` | `search_prs`，`rag_retrieve` |
| `gardener_code` | `scripts/ingest_docs.py` | `search_code`，`rag_retrieve` |
| 全部四个（RRF） | 无需独立写入 | `root_cause_analysis` |

## 附录 B：快速参考 — 环境变量与工具依赖

| 环境变量 | 必填 | 影响的工具 |
|---|---|---|
| `GITHUB_TOKEN` | 是（服务器启动必填）| 所有工具（服务器启动失败则全部不可用）|
| `ANTHROPIC_AUTH_TOKEN` | root_cause_analysis 必填 | `root_cause_analysis` |
| `ANTHROPIC_BASE_URL` | root_cause_analysis 必填 | `root_cause_analysis` |
| `HYPERSPACE_OPENAI_BASE_URL` | 摄取和检索必填 | 所有 `search_*`，`rag_retrieve`，`root_cause_analysis` |
| `QDRANT_URL` | 否（默认 localhost:6333）| 所有 `search_*`，`rag_retrieve`，`root_cause_analysis` |
