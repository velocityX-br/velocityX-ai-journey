# Gardener AI MCP — 代码结构与实现原理

> 面向开发者的深度技术介绍（中文版）

---

## 目录

1. [项目定位](#1-项目定位)
2. [整体架构](#2-整体架构)
3. [目录结构一览](#3-目录结构一览)
4. [配置层](#4-配置层)
5. [数据摄取层（离线 Pipeline）](#5-数据摄取层离线-pipeline)
6. [Embedding 层](#6-embedding-层)
7. [向量数据库层](#7-向量数据库层)
8. [检索层](#8-检索层)
9. [MCP 服务层](#9-mcp-服务层)
10. [端到端数据流](#10-端到端数据流)
11. [核心设计模式](#11-核心设计模式)
12. [测试策略](#12-测试策略)

---

## 1. 项目定位

**Gardener AI MCP** 是一个面向 [Gardener](https://gardener.cloud/) Kubernetes 项目的生产级 **MCP（Model Context Protocol）服务器**，让 AI Agent（如 Claude）能够：

- 语义搜索 Gardener 官方文档与增强提案（GEP）
- 搜索 GitHub Issues / Pull Requests
- 搜索 Go 源代码
- 执行跨源混合检索（RAG）
- 调用 LLM 进行根因分析（Root Cause Analysis）

后端基于 **Qdrant 向量数据库**、**SAP Hyperspace Embedding API**（OpenAI 兼容）和 **FastMCP** 框架构建。

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                   离线摄取 Pipeline                      │
│                                                         │
│  GitHub API ──► Ingester ──► Chunker ──► Embedder       │
│                                              │           │
│                                         Qdrant Upsert   │
└─────────────────────────────────────────────────────────┘

                          │
                     4 个 Collection
                          │

┌─────────────────────────────────────────────────────────┐
│                   MCP Server（在线）                     │
│                                                         │
│  MCP Client ──► Tool Handler ──► Retriever ──► Qdrant   │
│                     │                                   │
│                     └──► LLM（Root Cause Analysis）      │
└─────────────────────────────────────────────────────────┘
```

**分层关系**（从上到下依赖单向流动）：

```
配置层 (config/settings.py)
    ↓
摄取层 (ingestion/*) ← 离线 CLI，非服务器启动时运行
    ↓
嵌入层 (embeddings/openai_embedder.py)
    ↓
向量存储层 (vectorstore/qdrant.py)
    ↓
检索层 (retrieval/semantic.py + hybrid.py)
    ↓
MCP 工具层 (gardener_mcp/tools.py)
    ↓
FastMCP 服务 (gardener_mcp/server.py)
```

---

## 3. 目录结构一览

```
gardener-ai-mcp/
├── config/
│   └── settings.py           # 统一环境变量读取，Pydantic v2
│
├── ingestion/                # 离线数据摄取
│   ├── base.py               # 抽象基类 BaseIngester + Document 模型
│   ├── github_docs.py        # 文档摄取（gardener/documentation）
│   ├── github_issues.py      # Issues 摄取（gardener/gardener）
│   ├── github_prs.py         # PR 摄取
│   ├── code_indexer.py       # Go 源码摄取
│   └── chunking.py           # Markdown + Code 文本分块器
│
├── embeddings/               # 向量嵌入
│   ├── base.py               # 抽象接口 BaseEmbedder
│   └── openai_embedder.py    # HyperspaceEmbedder（OpenAI 兼容）
│
├── vectorstore/              # 向量数据库
│   ├── base.py               # 抽象接口 BaseVectorStore + SearchResult
│   └── qdrant.py             # QdrantVectorStore 实现
│
├── retrieval/                # 检索逻辑
│   ├── base.py               # 抽象接口 BaseRetriever
│   ├── semantic.py           # 单集合语义检索
│   └── hybrid.py             # 多集合混合检索 + RRF 融合
│
├── gardener_mcp/             # MCP 服务核心
│   ├── models.py             # 工具输入/输出 Pydantic 模型
│   ├── context.py            # AppContext（依赖注入容器）
│   ├── tools.py              # 7 个 MCP 工具实现 + 缓存
│   └── server.py             # FastMCP 服务器入口
│
├── scripts/
│   └── ingest_docs.py        # 摄取 CLI 入口
│
├── tests/                    # 单元 + 集成测试
└── docs/                     # 项目文档
```

---

## 4. 配置层

**文件**: `config/settings.py`

### 核心设计：双前缀环境变量解析

```python
class Settings(BaseSettings):
    github_token: str = ""
    qdrant_url: str = "http://localhost:6333"
    anthropic_model: str = "claude-opus-4-6"
    embedding_dimensions: int = 1536
    # ...

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="GARDENER_MCP_",   # 优先读取 GARDENER_MCP_* 前缀变量
    )
```

**解析优先级**（高 → 低）：
1. 进程环境变量 `GARDENER_MCP_*`（最高优先级，独立于 Claude Code 环境）
2. 进程环境变量无前缀版本
3. `.env` 文件
4. 字段默认值

**意图**：即使与 Claude Code CLI 共享同一 shell 进程，MCP 服务器也能使用独立的 API Token，避免 Token 混用。

### 主要配置分类

| 类别 | 关键字段 | 用途 |
|------|---------|------|
| GitHub | `github_token`, `github_base_url` | API 认证与仓库选择 |
| LLM | `anthropic_base_url`, `anthropic_auth_token`, `anthropic_model` | Hyperspace LLM 代理 |
| Embedding | `hyperspace_openai_base_url`, `embedding_model`, `embedding_dimensions` | 向量化接口 |
| Qdrant | `qdrant_url`, `qdrant_api_key`, `qdrant_batch_size` | 向量数据库 |
| 摄取控制 | `ingestion_max_issues`, `ingestion_max_prs` | 批次上限（0 = 不限）|
| 缓存 | `tool_cache_ttl_seconds`, `tool_cache_max_size` | 工具结果缓存 |

---

## 5. 数据摄取层（离线 Pipeline）

摄取是**离线批处理过程**，通过 CLI 运行，不是服务器启动的一部分。

```bash
uv run python scripts/ingest_docs.py --collections docs issues prs code
```

### 5.1 抽象基类设计

`ingestion/base.py` 定义接口：

```python
@dataclass
class Document:
    id: str                    # UUID
    content: str               # 原始文本
    metadata: dict[str, Any]   # 来源元数据

class BaseIngester(ABC):
    @abstractmethod
    async def ingest(self) -> list[Document]: ...
```

所有摄取器遵循相同合约，便于测试与替换。

### 5.2 四个摄取器

#### GitHubDocsIngester（`ingestion/github_docs.py`）

- **数据源**：`gardener/documentation` 仓库，`website/` 目录
- **工作方式**：递归遍历 GitHub Contents API，解码 base64 Markdown 文件
- **内容分类**：
  - 路径含 `proposal/gep` → `content_type: "proposal"`（GEP）
  - 其他 `.md` → `content_type: "doc"`（普通文档）
- **关键挑战**：GitHub Contents API 是逐文件调用，大仓库需要数十分钟

#### GitHubIssuesIngester（`ingestion/github_issues.py`）

- **数据源**：`gardener/gardener` Issues（开放 + 关闭）
- **文档格式**：拼接为 Markdown：

```markdown
# Issue 标题

Issue 正文

## Comments

**作者**: 评论内容
```

- **分页**：按 `updated DESC` 排序，批次大小可配置
- **限制控制**：`ingestion_max_issues`（0 = 全量）

#### GitHubPRsIngester（`ingestion/github_prs.py`）

- **数据源**：`gardener/gardener` PR（含 Review Comments）
- **关联 Issue 提取**：正则匹配 body 中的 `#123`、`fixes #456` 等
- **元数据**：`merged_at`、`linked_issues`、`state`

#### CodeIngester（`ingestion/code_indexer.py`）

- **数据源**：`gardener/gardener` `.go` 源文件
- **提取策略**（非全量内容，只提取结构）：
  - `package <name>` 声明
  - `func ...` 函数签名
  - `type ...` 类型声明
  - 无匹配则取前 500 字符
- **跳过目录**：`vendor`, `third_party`, `hack`
- **文件上限**：`_MAX_FILES = 2000`（防止 API 超限）

### 5.3 分块层（`ingestion/chunking.py`）

#### MarkdownChunker

```python
chunk_size=1000, chunk_overlap=200
# 使用 LangChain MarkdownTextSplitter
# 感知 Markdown 标题层级，在段落边界切割
```

#### CodeChunker

```python
chunk_size=1500, chunk_overlap=300
# 使用 RecursiveCharacterTextSplitter
# 按 段落 → 行 → 词 → 字符 递进分割
```

**每个 Chunk 附加溯源信息**：

```python
{
    ...父文档的所有 metadata...,
    "chunk_index": 0,          # 在父文档中的位置
    "total_chunks": 5,         # 父文档总 chunk 数
    "parent_id": "<uuid>"      # 父文档 ID
}
```

### 5.4 摄取 CLI Pipeline（`scripts/ingest_docs.py`）

每个 Collection 的 5 步流程：

```
1. 调用对应 Ingester.ingest() → Document 列表
2. 调用 Chunker.chunk() → Chunk 列表
3. 确保 Qdrant Collection 存在（ensure_collection，幂等）
4. 批量 Embed（256 texts/batch，内部 2048/API call）
5. 批量 Upsert 到 Qdrant（100 points/batch）
```

---

## 6. Embedding 层

**文件**: `embeddings/openai_embedder.py`

**类**: `HyperspaceEmbedder`（实现 `BaseEmbedder`）

### 核心特性

| 特性 | 实现 |
|------|------|
| 批处理 | 每次 API 调用最多 2048 条文本 |
| 重试 | Tenacity 指数退避（2s-60s），HTTP 429 触发 |
| Token 感知 | tiktoken 统计 token 数，批次超 8000 时打印 Warning |
| 查询缓存 | FIFO 缓存，SHA-256 作为 key，命中直接返回 |
| 依赖注入 | 接受外部注入 `openai.AsyncOpenAI` 客户端（便于测试） |

### 两个核心方法

```python
async def embed_documents(self, texts: list[str]) -> list[list[float]]:
    # 将 texts 分批（2048/批），逐批调用 API
    # 保持结果顺序与输入一致

async def embed_query(self, text: str) -> list[float]:
    # 先查缓存（SHA-256 key）
    # 缓存满时 FIFO 淘汰
```

---

## 7. 向量数据库层

**文件**: `vectorstore/qdrant.py`

**类**: `QdrantVectorStore`（实现 `BaseVectorStore`）

### 四个 Collection

```
gardener_docs    → 文档 + GEP 提案
gardener_issues  → GitHub Issues
gardener_prs     → Pull Requests
gardener_code    → Go 源代码（结构摘要）
```

### HNSW 索引配置

```python
distance = COSINE                # 余弦相似度
hnsw_config = HnswConfigDiff(
    m=16,           # 每个节点的邻居数
    ef_construct=100 # 构建时的候选邻居数
)
```

### Payload 索引（加速过滤）

创建 Collection 时自动建立以下字段的索引：
- `source_type`、`repo`、`state`、`language`

有了这些索引，按 `state="open"` 或 `language="go"` 过滤不会全表扫描。

### 核心方法

```python
async def ensure_collection(collection: str, vector_size: int)
# 幂等，已存在则跳过

async def upsert(collection, documents, vectors) -> int
# 批量插入（100 points/batch）
# 每个点：PointStruct(id=uuid, vector=embedding, payload=metadata+content)

async def search(collection, query_vector, limit, filters=None) -> list[SearchResult]
# 将 dict filters 转换为 Qdrant FieldCondition
# 多条件 AND 逻辑

async def health_check() -> bool
# 调用 get_collections()，失败返回 False（不抛异常）
```

---

## 8. 检索层

### 8.1 SemanticRetriever（`retrieval/semantic.py`）

**单集合密集向量检索**：

```
查询文本 → embed_query() → 向量 → qdrant.search(collection) → 结果
```

用于 `search_docs`、`search_issues`、`search_prs`、`search_proposals`、`search_code` 工具（每个工具锁定一个 Collection）。

### 8.2 HybridRetriever（`retrieval/hybrid.py`）

**多集合并发检索 + RRF 融合**：

```
查询文本
   ↓ embed_query()
密集向量
   ↓ asyncio.gather() 并发
┌──────────────────────────────────────────────┐
│  dense_search(docs)   sparse_search(docs)    │
│  dense_search(issues) sparse_search(issues)  │
│  dense_search(prs)    sparse_search(prs)     │
│  dense_search(code)   sparse_search(code)    │
└──────────────────────────────────────────────┘
   ↓ Reciprocal Rank Fusion（RRF）
Top-N 融合结果
```

**RRF 公式**：

```
score(d) = Σ  1 / (k + rank(d, list_i))
           i
k = 60（学术默认值）
```

RRF 的优点：不需要分数归一化，对各路检索得分量纲不一致的情况鲁棒。

---

## 9. MCP 服务层

### 9.1 依赖注入容器（`gardener_mcp/context.py`）

```python
@dataclass(frozen=True)      # 不可变，线程安全
class AppContext:
    settings: Settings
    embedder: BaseEmbedder
    vector_store: BaseVectorStore
    semantic_retriever: SemanticRetriever
    hybrid_retriever: HybridRetriever
    anthropic_client: AsyncAnthropic
```

`build_app_context()` 是**唯一的组合根（Composition Root）**：

```python
async def build_app_context(settings: Settings) -> AppContext:
    embedder = HyperspaceEmbedder(settings)
    vector_store = QdrantVectorStore(settings)
    await vector_store.health_check()   # 警告但不中断
    semantic_retriever = SemanticRetriever(embedder, vector_store, "gardener_docs")
    hybrid_retriever = HybridRetriever(embedder, vector_store, ALL_COLLECTIONS)
    client = AsyncAnthropic(base_url=..., api_key=...)
    return AppContext(...)
```

`AppContext` 在 FastMCP lifespan 中创建一次，注入所有工具处理器。

### 9.2 工具缓存（`gardener_mcp/tools.py` 内的 `_ToolCache`）

```python
class _ToolCache:
    def _make_key(tool_name, **kwargs) -> str:
        # JSON 序列化所有参数 → SHA-256 → 缓存 key

    def get(key) -> Any | None:
        # 检查 TTL，未过期则返回

    def set(key, value):
        # 存储 (expiry, value)
        # 容量满时 FIFO 淘汰
```

默认配置：TTL = 3600s，最多 128 条。`max_size=0` 时完全禁用缓存。

### 9.3 七个 MCP 工具

| 工具名 | Collection | 特殊逻辑 |
|--------|-----------|---------|
| `search_docs` | `gardener_docs` | SemanticRetriever |
| `search_issues` | `gardener_issues` | 支持 `state` / `labels` 过滤 |
| `search_prs` | `gardener_prs` | 支持 `state` 过滤（含 `"merged"`）|
| `search_proposals` | `gardener_docs` | 固定 filter: `content_type="proposal"` |
| `search_code` | `gardener_code` | 支持 `repo` 过滤 |
| `rag_retrieve` | 调用方指定 | 低级通用接口 |
| `root_cause_analysis` | 全部（HybridRetriever）| 检索 + LLM 合成 |

### 9.4 根因分析工具详解

```python
async def root_cause_analysis(symptom: str, context: str = None, limit: int = 5) -> str:

    # 1. 组合查询
    combined_query = f"{symptom} {context}"

    # 2. 混合检索（跨全部 4 个 Collection）
    results = await hybrid_retriever.retrieve(combined_query, limit=limit)

    # 3. 构建上下文块
    context_block = "\n\n".join([
        f"[{i}] Collection: {r.collection} | Source: {r.metadata.get('url')}\n{r.content}"
        for i, r in enumerate(results, 1)
    ])

    # 4. 调用 LLM（Hyperspace 代理 → Anthropic）
    response = await anthropic_client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2048,
        system="You are a Gardener Kubernetes expert...",
        messages=[{"role": "user", "content": f"Symptom: {symptom}\n\nDocuments:\n{context_block}"}]
    )

    # 5. 返回结构化分析
    return response.content[0].text
```

### 9.5 FastMCP Server（`gardener_mcp/server.py`）

```python
mcp = FastMCP("gardener-ai-mcp", lifespan=lifespan)

@asynccontextmanager
async def lifespan(app: FastMCP) -> AsyncIterator[dict]:
    settings = get_settings()
    configure_tool_cache(...)
    app_context = await build_app_context(settings)
    yield {"app_context": app_context}   # 注入所有工具
    # 退出时自动清理

# 工具注册
@mcp.tool()
async def search_docs(ctx: Context, query: str, ...) -> list[ToolSearchResult]:
    app_context: AppContext = ctx.lifespan_context["app_context"]
    ...
```

**传输协议选择**（由 `settings.mcp_transport` 决定）：
- `"stdio"`（默认）：标准输入输出，适合 Claude Desktop / CLI
- `"sse"`：HTTP + Server-Sent Events，适合 HTTP 客户端接入

---

## 10. 端到端数据流

### 摄取流程（离线）

```
GitHub API
    ↓ PyGithub / httpx
Ingester.ingest() → [Document, ...]
    ↓ MarkdownChunker / CodeChunker
[Chunk, ...]（附溯源 metadata）
    ↓ HyperspaceEmbedder.embed_documents()
[vector, ...]（1536 维 float32）
    ↓ QdrantVectorStore.upsert()
Qdrant Collection（HNSW 索引完毕）
```

### 查询流程（在线）

```
MCP Client 调用 search_issues(query="unsafe sysctl")
    ↓ 检查 _ToolCache
    ↓（缓存未命中）
SemanticRetriever.retrieve()
    ↓ HyperspaceEmbedder.embed_query()  ← 先查 embedding 缓存
密集向量
    ↓ QdrantVectorStore.search("gardener_issues", vector, filters)
[SearchResult, ...]（按余弦相似度排序）
    ↓ 转换为 ToolSearchResult（加 source URL）
    ↓ 存入 _ToolCache（TTL=3600s）
返回给 MCP Client
```

### 根因分析流程

```
root_cause_analysis(symptom="Shoot stuck in Reconciling")
    ↓ HybridRetriever（8 路并发：4 Collection × 密集+稀疏）
    ↓ RRF 融合 → Top-N 文档
    ↓ 格式化为 context_block
    ↓ anthropic_client.messages.create()  → SAP Hyperspace
LLM 结构化输出（根因 + 证据 + 修复步骤）
    ↓ 返回给 MCP Client
```

---

## 11. 核心设计模式

### 11.1 抽象接口 + 依赖注入

每一层都定义抽象基类（`Base*`），具体实现由 `build_app_context()` 在启动时注入。
工具处理器只依赖接口，不直接 import 具体类。

**好处**：
- 单元测试可注入 Mock 实现
- 替换 Qdrant 为其他向量数据库只需实现 `BaseVectorStore`
- 替换 Hyperspace 为本地 Ollama 只需实现 `BaseEmbedder`

### 11.2 不可变 AppContext

`AppContext` 用 `@dataclass(frozen=True)` 修饰，创建后不可变：

```python
# 错误做法（不存在）
app_context.embedder = new_embedder  # AttributeError

# 正确做法
# 服务重启时重新调用 build_app_context()
```

**好处**：并发安全，无需锁。

### 11.3 异步优先

所有 I/O 操作（GitHub API、Qdrant、Embedding API）均为 `async`：

- 摄取层：`async def ingest()`，`async def chunk()`
- Embedding：`async def embed_documents()`，`async def embed_query()`
- 向量存储：`async def search()`，`async def upsert()`
- 混合检索：`asyncio.gather()` 并发 8 路搜索

### 11.4 分层缓存

```
Level 1 (工具级): _ToolCache
    key = SHA-256(tool_name + all_args)
    TTL = 3600s，容量 128 条
    命中 → 直接返回，无 I/O

Level 2 (Embedding 级): HyperspaceEmbedder 内部 FIFO 缓存
    key = SHA-256(query_text)
    容量 = embedding_cache_size（默认 256）
    仅缓存 embed_query()（单次查询），不缓存文档批量 embed
```

### 11.5 优雅降级

- Qdrant 不可达 → `health_check()` 返回 `False`，打印 Warning，服务继续启动
- Collection 为空 → 打印 Warning，不中断启动（提示用户运行摄取脚本）
- Embedding API 429 → Tenacity 自动重试，不暴露给调用方
- 摄取某个 Collection 失败 → 仅记录错误，其他 Collection 继续进行

---

## 12. 测试策略

测试位于 `tests/` 目录，遵循以下原则：

### 单元测试

- 每个模块有对应测试文件（如 `tests/test_chunking.py`）
- 使用 Mock 注入 `BaseEmbedder`、`BaseVectorStore`，不需要真实服务
- 测试覆盖：分块逻辑、RRF 算法、缓存 TTL/淘汰、配置解析

### 集成测试

- 需要真实 Qdrant 实例（`docker run -p 6333:6333 qdrant/qdrant`）
- 测试完整摄取 → 搜索流程
- 测试 Hybrid Retriever 的并发正确性

### 运行测试

```bash
uv run pytest tests/ -v
uv run pytest tests/ -v -k "unit"       # 仅单元测试
uv run pytest tests/ -v -k "integration" # 需要 Qdrant
```

---

## 关键文件速查表

| 文件 | 职责 | 核心类/函数 |
|------|------|------------|
| `config/settings.py` | 环境配置 | `Settings`, `get_settings()` |
| `ingestion/base.py` | 摄取接口 | `BaseIngester`, `Document` |
| `ingestion/github_docs.py` | 文档摄取 | `GitHubDocsIngester` |
| `ingestion/github_issues.py` | Issues 摄取 | `GitHubIssuesIngester` |
| `ingestion/github_prs.py` | PR 摄取 | `GitHubPRsIngester` |
| `ingestion/code_indexer.py` | 代码摄取 | `CodeIngester` |
| `ingestion/chunking.py` | 文本分块 | `MarkdownChunker`, `CodeChunker` |
| `embeddings/openai_embedder.py` | 向量嵌入 | `HyperspaceEmbedder` |
| `vectorstore/qdrant.py` | 向量数据库 | `QdrantVectorStore` |
| `retrieval/semantic.py` | 单集合检索 | `SemanticRetriever` |
| `retrieval/hybrid.py` | 多集合检索 + RRF | `HybridRetriever`, `reciprocal_rank_fusion()` |
| `gardener_mcp/context.py` | DI 容器 | `AppContext`, `build_app_context()` |
| `gardener_mcp/tools.py` | MCP 工具实现 | 7 个 `@mcp.tool` 函数, `_ToolCache` |
| `gardener_mcp/server.py` | FastMCP 服务器 | `mcp` 实例, `lifespan()` |
| `scripts/ingest_docs.py` | 摄取 CLI | `main()`, `_ingest_collection()` |
