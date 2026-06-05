# Gardener AI MCP — 架构文档

## 一、项目使命

为 Gardener 构建一个生产级 MCP 服务器，允许 AI 智能体：搜索文档、GitHub Issues、Pull Requests、增强提案（GEP）、Go 源码，并执行 RAG 检索和根因分析。

---

## 二、整体架构数据流

```
GitHub APIs (docs/issues/PRs/code)
        │
        ▼
   Ingesters (4种)          ← ingestion/ 层
        │
        ▼
   Chunkers (2种)            ← 文档分块
        │
        ▼
   HyperspaceEmbedder        ← embeddings/ 层（SAP OpenAI 代理）
        │
        ▼
   QdrantVectorStore         ← vectorstore/ 层（4个集合）
        │
     ┌──┴────────────┐
     ▼               ▼
SemanticRetriever  HybridRetriever   ← retrieval/ 层
     └──────┬────────┘
            ▼
      FastMCP Server            ← gardener_mcp/ 层
      (7 个 MCP 工具)
            ▼
      AI Agent (Claude 等)
```

---

## 三、目录结构

```
gardener-ai-mcp/
├── config/           # Pydantic 配置管理（GARDENER_MCP_* 双前缀）
├── ingestion/        # 数据摄入 — 4 种 Ingester + 2 种 Chunker
├── embeddings/       # 向量嵌入 — HyperspaceEmbedder（OpenAI 兼容代理）
├── vectorstore/      # 向量数据库 — Qdrant（4个集合，HNSW，余弦距离）
├── retrieval/        # 检索策略 — 语义检索 + 混合检索（RRF 融合）
├── gardener_mcp/     # MCP 服务器 + 7 个工具 + AppContext DI 容器
├── scripts/          # 运维脚本（ingestion CLI、healthcheck、kind 部署）
├── docker/           # 多阶段 Dockerfile（非 root、read-only 文件系统）
├── helm/             # Helm Chart（含 Qdrant 子 Chart）
└── tests/            # 单元/集成测试（覆盖率阈值 70%）
```

---

## 四、各层详解

### 1. 配置层 — `config/settings.py`

- 基于 **Pydantic v2 BaseSettings**
- **双前缀解析**：`GARDENER_MCP_*` 优先于未前缀的同名环境变量（避免与 Claude Code 本身的环境变量冲突）
- 关键配置项：Qdrant URL、Hyperspace 代理 URL、GitHub Token、MCP 传输模式（`stdio` / `sse`）

---

### 2. 摄入层 — `ingestion/`

| 模块 | 职责 |
|---|---|
| `base.py` | `Document` 模型 + `BaseIngester` ABC |
| `github_docs.py` | 递归遍历 GitHub Contents API，抓取 `gardener/documentation` 的 `.md` 文件 |
| `github_issues.py` | 分页抓取 `gardener/gardener` 的所有 Issue + 评论 |
| `github_prs.py` | 分页抓取 Pull Request 数据 |
| `code_indexer.py` | 用正则提取 `.go` 文件中的 package/函数/类型声明 |
| `chunking.py` | `MarkdownChunker`（1000/200 char）+ `CodeChunker`（1500/300 char），附加 provenance 元数据 |

所有 PyGithub 同步调用通过 `asyncio.to_thread` 包装，保持异步非阻塞。

---

### 3. 嵌入层 — `embeddings/`

- **`HyperspaceEmbedder`**：OpenAI SDK 指向 SAP Hyperspace 代理（`text-embedding-3-small`，1536 维）
- 每批最多 2048 条文本，tenacity 重试（HTTP 429，指数退避，最多 5 次）
- 通过 `ANTHROPIC_AUTH_TOKEN` 鉴权

---

### 4. 向量库层 — `vectorstore/`

`QdrantVectorStore` 管理 4 个集合：

| 集合名 | 内容 |
|---|---|
| `gardener_docs` | 文档 + GEP 提案 |
| `gardener_issues` | GitHub Issues |
| `gardener_prs` | GitHub Pull Requests |
| `gardener_code` | Go 源码片段 |

- **HNSW 索引**：m=16, ef_construct=100
- **余弦距离**相似度度量
- **Payload 索引**：source_type、repo、state、language（快速过滤）
- 批量 upsert（默认每批 100 条）

---

### 5. 检索层 — `retrieval/`

**SemanticRetriever**（`retrieval/semantic.py`）
- 单集合密集向量检索
- 嵌入查询 → 最近邻搜索

**HybridRetriever**（`retrieval/hybrid.py`）
- 跨全部 4 个集合并行搜索
- 每个集合执行两路搜索：① 密集向量 ② 关键词（`$text` 过滤器）
- `asyncio.gather()` 并发所有查询
- **RRF（Reciprocal Rank Fusion，k=60）** 融合多路结果，与分数尺度无关

---

### 6. MCP 服务层 — `gardener_mcp/`

**服务器**（`server.py`）：FastMCP + lifespan 上下文管理器，启动时初始化 `AppContext` 并注入所有工具。

**7 个 MCP 工具**（`tools.py`）：

| 工具 | 功能 |
|---|---|
| `search_docs` | 语义搜索 Gardener 文档 |
| `search_issues` | 搜索 GitHub Issues（可过滤 state/labels）|
| `search_prs` | 搜索 Pull Requests（可过滤 state）|
| `search_proposals` | 搜索 GEP 增强提案（固定 `content_type=proposal` 过滤）|
| `search_code` | 搜索 Go 源码（可过滤 repo）|
| `rag_retrieve` | 低级 RAG 检索（手动指定集合）|
| `root_cause_analysis` | 混合检索所有集合 → 格式化上下文 → 调用 LLM 输出「根因 + 证据 + 修复步骤」|

**AppContext**（`context.py`）：冻结 Pydantic DI 容器，保存全部单例（embedder、vector_store、retrievers、anthropic_client），在 FastMCP lifespan 中一次性初始化。

---

### 7. 部署层 — Docker + Helm

**Docker**（`docker/Dockerfile`）：
- 多阶段构建（builder + runtime）
- 非 root 用户（uid 1001），read-only 根文件系统
- 内建 healthcheck：`scripts/healthcheck.py` → 探测 Qdrant `/healthz`

**Helm Chart**（`helm/`）：
- 包含 Qdrant 子 Chart（可禁用，对接外部实例）
- Pod 安全上下文：runAsNonRoot、drop all capabilities
- 资源限制：250m/256Mi 请求，1 CPU/1Gi 上限
- 支持 HPA 自动扩缩（默认关闭，最大 4 副本）

**本地开发**（`scripts/setup_kind.sh`）：
1. 创建 kind 集群 → 2. 构建镜像 → 3. 加载到 kind → 4. 创建 namespace/Secret → 5. Helm 安装

---

## 五、关键设计决策（ADR）

| 决策 | 说明 |
|---|---|
| **ADR-004：依赖注入** | 所有具体实现通过构造注入，工具层仅依赖抽象接口，无模块级生产代码导入 |
| **ADR-005：RRF 混合检索** | 密集向量 + 关键词双路并行搜索，RRF 融合解决多集合分数不可比问题 |
| **ADR-006：双前缀环境变量** | `GARDENER_MCP_*` 前缀避免与同一 shell 中 Claude Code 的环境变量冲突 |

---

## 六、开发阶段进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | 架构设计 | 完成 |
| Phase 2 | 摄入管道 | 完成 |
| Phase 3 | 向量库 | 完成 |
| Phase 4 | 检索层 | 完成 |
| Phase 5 | MCP 工具 | 完成 |
| Phase 6 | Docker | 完成 |
| Phase 7 | Helm | 完成 |
| Phase 8 | CI/CD | 待实现 |

---

## 七、技术栈一览

| 组件 | 技术 |
|---|---|
| 语言 | Python 3.12 |
| MCP 框架 | FastMCP >= 3.3.1 |
| 向量数据库 | Qdrant >= 1.18 |
| 嵌入模型 | text-embedding-3-small（1536 维，via SAP Hyperspace）|
| LLM | Claude（via SAP Hyperspace Anthropic 兼容接口）|
| GitHub 数据源 | PyGithub >= 2.9.1 |
| 文档分块 | LangChain TextSplitters |
| 配置管理 | Pydantic v2 BaseSettings |
| 容器化 | Docker 多阶段构建 |
| K8s 部署 | Helm v2（含 Qdrant 子 Chart）|
| 本地开发 | kind |
| 测试 | pytest + pytest-asyncio（覆盖率 >= 70%）|
