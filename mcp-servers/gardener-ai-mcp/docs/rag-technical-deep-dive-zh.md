# Gardener AI MCP — RAG 与核心技术深度解析

> 基于源码逐行解析，覆盖 RAG 管道、Embedding 机制、检索匹配算法、缓存设计与 Token/Context 优化。

---

## 目录

1. [整体 RAG 管道架构](#1-整体-rag-管道架构)
2. [数据摄取层：内容结构与 Metadata Schema](#2-数据摄取层内容结构与-metadata-schema)
3. [文本分块策略（Chunking）](#3-文本分块策略chunking)
4. [Embedding 向量化机制](#4-embedding-向量化机制)
5. [向量存储层：Qdrant 设计细节](#5-向量存储层qdrant-设计细节)
6. [检索匹配机制：语义检索与混合检索](#6-检索匹配机制语义检索与混合检索)
7. [三层缓存体系设计](#7-三层缓存体系设计)
8. [Token 与 Context 优化策略](#8-token-与-context-优化策略)
9. [根因分析：RAG + LLM 合成](#9-根因分析rag--llm-合成)
10. [SAP GitHub 按需写入缓存（write-through）](#10-sap-github-按需写入缓存write-through)
11. [架构决策（ADR）速查](#11-架构决策adr速查)

---

## 1. 整体 RAG 管道架构

```
【离线摄取 Pipeline（一次性 / 定期运行）】

GitHub API (docs/issues/PRs/code)
        │
        ▼
  ┌─────────────────┐
  │  Ingestion Layer │   GitHubDocsIngester
  │                  │   GitHubIssuesIngester   ← asyncio.to_thread 包装同步 PyGithub
  │                  │   GitHubPRsIngester
  │                  │   CodeIndexer
  └─────────────────┘
        │  list[Document]
        ▼
  ┌─────────────────┐
  │  Chunking Layer  │   MarkdownChunker  (1000 chars / 200 overlap)
  │                  │   CodeChunker      (1500 chars / 300 overlap)
  └─────────────────┘
        │  list[Document] (chunks + provenance metadata)
        ▼
  ┌─────────────────┐
  │ Embedding Layer  │   HyperspaceEmbedder → text-embedding-3-small (1536-dim)
  │                  │   批量 2048 texts/call，指数退避重试
  └─────────────────┘
        │  list[list[float]]
        ▼
  ┌─────────────────┐
  │  Qdrant Store    │   4 个独立 Collection（HNSW m=16, ef=100, Cosine 距离）
  │                  │   Payload 索引：source_type / repo / state / language
  └─────────────────┘

【在线检索 Pipeline（每次 MCP 工具调用）】

MCP Tool Call (query + filters)
        │
        ├─ Layer 2 Cache (_ToolCache) ──命中──► 直接返回
        │
        ▼ 缓存未命中
  ┌─────────────────┐
  │ Retrieval Layer  │   SemanticRetriever  (单集合密集向量)
  │                  │   HybridRetriever    (多集合 dense+sparse + RRF)
  └─────────────────┘
        │  query → embed_query()
        │             ├─ Layer 1 Cache (_query_cache) ──命中──► 跳过 API
        │             └─ 未命中 → Hyperspace API → 写入缓存
        ▼
  Qdrant query_points() → list[SearchResult]
        │
        ▼ 转换为 ToolSearchResult + 写入 _ToolCache
MCP Client ◄──── 返回结果
```

---

## 2. 数据摄取层：内容结构与 Metadata Schema

### 2.1 Document 基础模型

所有摄取内容统一抽象为 `Document`（`ingestion/base.py`）：

```python
class Document(BaseModel):
    id: str      = Field(default_factory=lambda: str(uuid.uuid4()))  # UUID，Qdrant PointID
    content: str                                                       # 原始文本
    metadata: dict[str, Any] = Field(default_factory=dict)            # 来源溯源信息
    source: str                                                        # GitHub HTML URL（去重主键）
```

### 2.2 各类型 Metadata Schema

**文档（`gardener_docs`）**：
```json
{
  "repo": "gardener/documentation",
  "path": "website/documentation/getting-started/architecture.md",
  "sha": "<git blob sha>",
  "url": "https://github.com/gardener/documentation/blob/...",
  "content_type": "doc"          // "doc" 或 "proposal"（GEP）
}
```

**Issues（`gardener_issues`）**：
```json
{
  "repo": "gardener/gardener",
  "issue_number": 1234,
  "title": "Shoot cluster stuck in Reconciling state",
  "state": "open",
  "labels": ["bug", "area/shoot"],
  "created_at": "2024-01-15T10:30:00",
  "closed_at": null,
  "url": "https://github.com/gardener/gardener/issues/1234"
}
```

**Pull Requests（`gardener_prs`）**：
```json
{
  "repo": "gardener/gardener",
  "pr_number": 5678,
  "title": "Fix reconcile loop for hibernated shoots",
  "state": "merged",
  "created_at": "2024-01-10T09:00:00",
  "merged_at": "2024-01-12T15:00:00",
  "url": "https://github.com/gardener/gardener/pull/5678"
}
```

**源代码（`gardener_code`）**：
```json
{
  "repo": "gardener/gardener",
  "file_path": "pkg/operation/botanist/shoot.go",
  "language": "go",
  "ref": "HEAD",
  "url": "https://github.com/gardener/gardener/blob/HEAD/pkg/..."
}
```

### 2.3 Issues 内容格式化设计

Issues 被格式化为完整 Markdown 文档，合并标题、正文、所有评论：

```python
def _format_issue_content(issue, comments) -> str:
    parts = [
        f"# {issue.title}",
        "",
        issue.body or "_No description provided._",
        "",
        "## Comments",
    ]
    for comment in comments:
        author = comment.user.login if comment.user else "unknown"
        parts.append(f"\n**{author}:**")
        parts.append(comment.body or "")
    return "\n".join(parts)
```

这种格式使一个 Issue 的完整讨论上下文被单一向量化，显著提升语义检索对问题讨论的覆盖。

### 2.4 异步化与内存控制

所有 PyGithub 同步 I/O 通过 `asyncio.to_thread` 包装，避免阻塞事件循环：

```python
repo = await asyncio.to_thread(self._github.get_repo, repo_slug)
page_items = await asyncio.to_thread(_get_page, paginated_issues, page_index)
```

分页处理确保内存峰值有界（每次只有一页 GitHub 对象在内存中）：
- `GARDENER_MCP_INGESTION_MAX_ISSUES=1000`
- `GARDENER_MCP_INGESTION_ISSUES_BATCH_SIZE=100`

---

## 3. 文本分块策略（Chunking）

### 3.1 两种分块器的参数差异

| 参数 | MarkdownChunker | CodeChunker |
|---|---|---|
| `chunk_size` | **1000 chars** | **1500 chars** |
| `chunk_overlap` | **200 chars** | **300 chars** |
| 底层分割器 | `MarkdownTextSplitter` | `RecursiveCharacterTextSplitter` |
| 适用内容 | 文档、Issues、PRs | Go 源代码 |

代码块更大（1500 vs 1000）是因为函数签名、注释、逻辑通常需要更多字符才能形成有意义的语义单元。更大的重叠（300 vs 200）确保跨函数边界的上下文不丢失。

### 3.2 Markdown 感知分割

`MarkdownTextSplitter` 优先在 Markdown 标题（`#`、`##`、`###`）和段落边界处分割，保持章节内容的完整性：

```
原始文档：
# Shoot Architecture
## Control Plane
The control plane runs...

## Data Plane
Workers communicate...

→ 分块结果（chunk_size=1000）：
Chunk 0: "# Shoot Architecture\n## Control Plane\nThe control plane runs..."
Chunk 1: "## Data Plane\nWorkers communicate..."  (含 200 char overlap 自前一块)
```

### 3.3 Provenance Envelope（溯源信封）

每个 chunk 继承父文档全部 metadata，并附加三个溯源字段：

```python
chunk_metadata = {
    **document.metadata,          # 继承原始 metadata（url, repo, state 等）
    "chunk_index": index,         # 0-based，在父文档中的位置
    "total_chunks": total,        # 父文档总分块数
    "parent_id": document.id,     # 父文档 UUID（支持重新组合）
}
```

溯源信封使检索结果能精确定位到原始文档的具体位置，支持上下文扩展和重排序。

### 3.4 选择逻辑（运行时）

在 `cache_sap_github_content` 工具中，分块器的选择基于 `content_type`：

```python
chunker = CodeChunker() if content_type == "code" else MarkdownChunker()
```

---

## 4. Embedding 向量化机制

### 4.1 HyperspaceEmbedder 架构

`embeddings/openai_embedder.py` 通过 OpenAI SDK 重定向 `base_url` 调用 SAP Hyperspace 代理：

```python
self._client = openai.AsyncOpenAI(
    base_url=settings.hyperspace_openai_base_url,  # 例：http://localhost:6655/openai/v1
    api_key=settings.anthropic_auth_token,         # SAP 认证 token
)
```

不需要真实 OpenAI 账号——完全通过 SAP 内部 Hyperspace 代理路由，符合数据合规要求（ADR-006）。

### 4.2 核心配置参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `embedding_model` | `text-embedding-3-small` | OpenAI 第三代小型模型 |
| `embedding_dimensions` | `1536` | 输出向量维度 |
| `embedding_cache_size` | `256` | query 缓存最大条目数 |

### 4.3 批量处理策略

```python
_BATCH_SIZE = 2048               # OpenAI API 硬限制（单次最多 2048 条输入）
_TOKEN_WARNING_THRESHOLD = 8_000 # 超过时触发 Warning（soft limit）

async def embed_documents(self, texts: list[str]) -> list[list[float]]:
    all_vectors = []
    for batch_start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[batch_start : batch_start + _BATCH_SIZE]
        self._warn_if_tokens_exceed(batch, batch_start)  # tiktoken 预估
        vectors = await self._embed_batch(batch)
        all_vectors.extend(vectors)
    return all_vectors
```

结果顺序通过 `sorted(response.data, key=lambda item: item.index)` 强制保证与输入一致。

### 4.4 指数退避重试（Tenacity）

```python
@retry(
    retry=retry_if_exception(lambda exc: isinstance(exc, openai.RateLimitError)),
    wait=wait_exponential(multiplier=1, min=2, max=60),   # 2s → 4s → 8s → ... → 60s
    stop=stop_after_attempt(5),                            # 最多 5 次
)
async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
    response = await self._client.embeddings.create(
        model=self._model,
        input=batch,
        encoding_format="float",
    )
    ...
```

只在 HTTP 429 时重试，其他错误直接抛出，避免对不可恢复错误的无效重试。

### 4.5 两路 Embedding 接口

```
embed_documents(texts: list[str]) → list[list[float]]
  └── 离线批量嵌入（ingestion pipeline）
  └── 无缓存，直接批量调用 API

embed_query(text: str) → list[float]
  └── 在线单条嵌入（检索时调用）
  └── 先查 _query_cache（SHA-256 key）
       ├── 命中 → 直接返回（0 API 调用）
       └── 未命中 → 调用 embed_documents([text]) → 写入缓存 → 返回
```

---

## 5. 向量存储层：Qdrant 设计细节

### 5.1 四集合独立架构

```python
COLLECTIONS = [
    "gardener_docs",    # 文档 + GEP 提案（content_type 字段区分）
    "gardener_issues",  # GitHub Issues（含评论）
    "gardener_prs",     # Pull Requests（含 review comments）
    "gardener_code",    # Go 源代码结构摘要
]
```

四个独立集合（而非单一集合加字段过滤）的理由（ADR-001）：
- 各类型内容的 chunk 密度、长度、语义特征差异显著
- 独立 HNSW 调优（未来可为 code 集合用更大 `m` 提升稠密代码检索）
- Schema 演进互不影响

### 5.2 HNSW 索引配置

```python
await self._client.create_collection(
    collection_name=collection,
    vectors_config=VectorParams(
        size=vector_size,          # 1536，从 settings 读取，不硬编码
        distance=Distance.COSINE,  # 余弦相似度（text embedding 的标准度量）
    ),
    hnsw_config=HnswConfigDiff(
        m=16,              # 每节点双向边数：内存 vs 查询精度权衡点
        ef_construct=100,  # 构建时候选搜索宽度：索引质量控制
    ),
)
```

**参数含义**：
- `m=16`：中等连接密度，文档规模数据集（数十万向量）的平衡配置
- `ef_construct=100`：构建时每插入节点搜索 100 个候选邻居，高索引质量

### 5.3 Payload 索引（加速有条件过滤）

```python
_INDEXED_PAYLOAD_FIELDS = ("source_type", "repo", "state", "language")

# 每个 Collection 建立 KEYWORD 类型索引
await self._client.create_payload_index(
    collection_name=collection,
    field_name=field_name,
    field_schema=PayloadSchemaType.KEYWORD,   # 精确字符串匹配
)
```

有了这些索引，按 `state="open"` 或 `repo="gardener/gardener"` 过滤时不触发全集合扫描，大幅降低搜索延迟。

### 5.4 PointStruct：向量点的数据结构

```python
PointStruct(
    id=doc.id,          # UUID（来自 Document.id）
    vector=vec,         # float32 list，1536-dim
    payload={
        **doc.metadata, # 所有来源 metadata（url, state, labels 等）
        "content": doc.content,   # chunk 原文（用于返回给用户）
        "source": doc.source,     # GitHub HTML URL
    },
)
```

`content` 存储在 payload 中，而非仅存向量，使搜索结果直接携带文本，无需二次查询。

### 5.5 Payload 过滤条件构建

```python
if filters:
    conditions = [
        FieldCondition(key=key, match=MatchValue(value=value))
        for key, value in filters.items()
    ]
    qdrant_filter = Filter(must=conditions)  # 所有条件 AND 逻辑
```

所有过滤条件以 `must`（AND）组合，意味着多条件过滤时必须全部满足。

---

## 6. 检索匹配机制：语义检索与混合检索

### 6.1 SemanticRetriever：密集向量单集合检索

**执行路径**（`retrieval/semantic.py`）：

```
query (str)
  ↓ embedder.embed_query(query)   → float32 [1536]
  ↓ vector_store.search(
      collection,
      query_vector,
      limit,
      filters                     → Qdrant FieldCondition AND 组合
    )
  ↓ list[SearchResult]            → 按余弦相似度降序排列
```

**余弦相似度含义**：
- 得分范围 0-1（余弦距离 = 1 - 余弦相似度）
- 得分越高语义越接近
- Qdrant 的 `COSINE` 距离模式会自动归一化向量

**按需实例化策略**：

搜索工具（`search_issues`、`search_prs` 等）在工具处理函数内动态创建 `SemanticRetriever`，而非复用单一实例：

```python
retriever = SemanticRetriever(
    embedder=app_ctx.embedder,       # 共享 embedder（含缓存）
    vector_store=app_ctx.vector_store, # 共享 vector_store
    collection="gardener_issues",    # 运行时绑定目标集合
)
```

只有 `gardener_docs` 的检索器在 `AppContext` 中预构建（最常用路径优化）。

### 6.2 HybridRetriever：多集合并发 + RRF 融合

**完整执行流程**（`retrieval/hybrid.py`）：

```
query (str)
  │
  ├─ Step 1: embed_query(query) → query_vector [1536]
  │
  ├─ Step 2: 构建 8 个协程
  │    ┌─ dense_search("gardener_docs",   query_vector, limit, filters)
  │    ├─ sparse_search("gardener_docs",  query_vector, limit, {**filters, "$text": query})
  │    ├─ dense_search("gardener_issues", query_vector, limit, filters)
  │    ├─ sparse_search("gardener_issues", ...)
  │    ├─ dense_search("gardener_prs",    query_vector, limit, filters)
  │    ├─ sparse_search("gardener_prs",   ...)
  │    ├─ dense_search("gardener_code",   query_vector, limit, filters)
  │    └─ sparse_search("gardener_code",  ...)
  │
  ├─ Step 3: asyncio.gather(*coroutines)  # 8 个并发 Qdrant 查询
  │
  ├─ Step 4: reciprocal_rank_fusion(all_results, k=60)
  │
  └─ Step 5: fused[:limit]  # 返回 Top-N 跨集合结果
```

**并发优势**：8 个 Qdrant 查询并发发出，总耗时约等于单个查询延迟（而非 8 倍）。

#### Sparse Search 的实现方式

```python
# sparse_filters 在 filters 基础上追加 $text 信号
sparse_filters: dict[str, Any] = dict(filters) if filters else {}
sparse_filters["$text"] = query  # Qdrant 将此作为 payload filter

# QdrantVectorStore.search() 中的处理：
# "$text" key 触发关键词过滤（当前是 payload match 方式）
# 未来 Qdrant ≥1.10 可替换为原生 sparse vector (BM42)
```

注意：当前 `$text` 实现为 payload 过滤而非真正的 BM25，因此 sparse search 的效果是"包含完整查询字符串"的文档优先，而非基于词频的相关性排序。这是一个渐进式设计，未来可透明升级。

#### RRF 算法实现

```python
def reciprocal_rank_fusion(ranked_lists: list[list[SearchResult]], k: int = 60):
    """
    公式：score(d) = Σ 1 / (k + rank(d, list_i))
    k=60 是 BEIR 基准测试的标准默认值

    关键属性：
    - 不依赖各列表的分数量纲（密集 0-1 vs 稀疏任意值）
    - 文档在多个列表中均高排名时分数显著提升（融合加分）
    - 每个 list 内同一文档只计最高排名（seen_in_list 去重）
    """
    rrf_scores: dict[str, float] = {}
    results_by_id: dict[str, SearchResult] = {}

    for ranked_list in ranked_lists:
        seen_in_list: set[str] = set()
        for rank_zero, result in enumerate(ranked_list):
            if result.id in seen_in_list:
                continue
            seen_in_list.add(result.id)
            rank_one = rank_zero + 1
            rrf_scores[result.id] = rrf_scores.get(result.id, 0.0) + 1.0 / (k + rank_one)
            if result.id not in results_by_id:
                results_by_id[result.id] = result  # 保留首次出现的 SearchResult 对象

    # 重建带 RRF 分数的结果列表
    fused = [
        SearchResult(id=doc_id, score=rrf_score, ...)
        for doc_id, rrf_score in rrf_scores.items()
    ]
    fused.sort(key=lambda r: r.score, reverse=True)
    return fused
```

**RRF 分数示例**（k=60，3 个结果列表）：

| 文档 | 在 list1 排名 | 在 list2 排名 | 在 list3 排名 | RRF 分数 |
|---|---|---|---|---|
| Doc A | #1 | #2 | - | 1/61 + 1/62 = 0.0327 |
| Doc B | #3 | #1 | #1 | 1/63 + 1/61 + 1/61 = 0.0485 |
| Doc C | #1 | - | - | 1/61 = 0.0164 |

Doc B 因在多个列表中均高排名，最终 RRF 分数反超 Doc A。

---

## 7. 三层缓存体系设计

### 7.1 架构总览

```
Layer 1: Query Embedding Cache          （HyperspaceEmbedder）
  缓存粒度：单条 query → embedding vector
  淘汰策略：FIFO
  容量：256 条（可配置）
  键：SHA-256(query_text)

Layer 2: Tool Result Cache              （_ToolCache，tools.py）
  缓存粒度：完整工具调用 → 结果列表
  淘汰策略：FIFO（到容量上限时淘汰最旧条目）
  过期机制：TTL（默认 3600s），惰性检查
  容量：128 条（可配置）
  键：SHA-256(tool_name + sorted_args_json)

Layer 3: Qdrant 服务端缓存             （透明，Qdrant 内置）
  缓存粒度：HNSW 搜索结果
  管理：Qdrant 服务自动管理
```

### 7.2 Layer 1：Query Embedding Cache（嵌入缓存）

```python
# HyperspaceEmbedder.__init__
self._cache_max_size: int = settings.embedding_cache_size  # 默认 256
self._query_cache: dict[str, list[float]] = {}             # SHA-256 → vector

async def embed_query(self, text: str) -> list[float]:
    if self._cache_max_size > 0:
        cache_key = hashlib.sha256(text.encode()).hexdigest()

        # 缓存命中
        if cache_key in self._query_cache:
            logger.debug("embed_query cache hit for key %s", cache_key[:8])
            return self._query_cache[cache_key]

        # 缓存未命中 → API 调用
        results = await self.embed_documents([text])

        # FIFO 淘汰
        if len(self._query_cache) >= self._cache_max_size:
            self._query_cache.pop(next(iter(self._query_cache)))  # 删除插入最早的条目

        self._query_cache[cache_key] = results[0]
        return results[0]

    # 缓存禁用（cache_max_size=0）时直接调用
    return (await self.embed_documents([text]))[0]
```

**缓存命中场景**：
- 同一 session 内多次搜索相同关键词（例如：用户迭代提问）
- `root_cause_analysis` 中 symptom 与前一次查询相同
- AI agent 对同一问题发起多次搜索工具调用

### 7.3 Layer 2：Tool Result Cache（工具结果缓存）

```python
class _ToolCache:
    def __init__(self, ttl_seconds: int, max_size: int):
        self._ttl = ttl_seconds     # 默认 3600s
        self._max_size = max_size   # 默认 128
        self._store: dict[str, tuple[float, Any]] = {}  # key → (expiry_timestamp, value)

    @property
    def enabled(self) -> bool:
        return self._ttl > 0 and self._max_size > 0

    def _make_key(self, tool_name: str, **kwargs: Any) -> str:
        # 所有参数序列化为 JSON（sort_keys=True 确保参数顺序无关）
        payload = json.dumps({"tool": tool_name, **kwargs}, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, key: str) -> tuple[bool, Any]:
        if not self.enabled or key not in self._store:
            return False, None
        expiry, value = self._store[key]
        if time.monotonic() > expiry:
            del self._store[key]   # 惰性过期删除（TTL 检查在读取时）
            return False, None
        return True, value

    def set(self, key: str, value: Any):
        if not self.enabled:
            return
        if len(self._store) >= self._max_size:
            self._store.pop(next(iter(self._store)))  # FIFO 淘汰
        self._store[key] = (time.monotonic() + self._ttl, value)
```

**工具缓存键的构成示例**：

```python
# search_issues(query="Shoot reconciling", limit=10, state="open", labels=["bug"])
key = SHA-256({
    "tool": "search_issues",
    "labels": ["bug"],      # sort_keys 确保字典序
    "limit": 10,
    "query": "Shoot reconciling",
    "state": "open"
})
```

**TTL 惰性过期**：没有后台清理线程——过期检查在 `get()` 调用时执行（适合单线程异步模型，无锁设计）。

**配置与禁用**：

```bash
GARDENER_MCP_TOOL_CACHE_TTL_SECONDS=3600  # 设为 0 完全禁用
GARDENER_MCP_TOOL_CACHE_MAX_SIZE=128       # 设为 0 完全禁用
```

### 7.4 两层缓存协同流程

```
search_docs("Shoot hibernation")
  │
  ├── [L2] _tool_cache.get(key)
  │      ├── HIT  → 返回 (0 延迟，0 API 调用)
  │      └── MISS ↓
  │
  ├── embedder.embed_query("Shoot hibernation")
  │      ├── [L1] _query_cache HIT  → 返回 vector (0 API 调用)
  │      └── [L1] MISS → Hyperspace API (~50ms) → 写入 L1 缓存
  │
  ├── qdrant.search("gardener_docs", vector, ...) (~20ms)
  │
  ├── [L2] _tool_cache.set(key, result)  → 写入 L2 缓存
  │
  └── 返回结果
```

---

## 8. Token 与 Context 优化策略

### 8.1 Embedding 阶段 Token 监控

```python
def _warn_if_tokens_exceed(self, batch: list[str], batch_start: int):
    try:
        total_tokens = sum(
            len(self._tokenizer.encode(text)) for text in batch
        )
        if total_tokens > _TOKEN_WARNING_THRESHOLD:  # 8000
            logger.warning(
                "Batch starting at index %d contains %d tokens (threshold: %d).",
                batch_start, total_tokens, _TOKEN_WARNING_THRESHOLD,
            )
    except Exception:
        pass  # Token 计数失败不阻塞嵌入
```

使用 `tiktoken cl100k_base`（与 `text-embedding-3-*` 同族编码器）。这是一个**软警告**：不阻塞调用，但提示运维人员分块策略可能需要调整。

### 8.2 Context 构建的 Token 预算

`root_cause_analysis` 工具精确控制传入 LLM 的 context 大小：

```python
# 检索上限：limit 参数（默认 5，最大 20）
# 4 个集合各 5 个文档 = 20 个文档上限
retrieved = await app_ctx.hybrid_retriever.retrieve(
    query=combined_query,
    limit=limit,     # 每次调用由 MCP client 控制
)

# Context 格式：编号 + 集合标签 + 来源 + 内容
context_block = "\n\n---\n\n".join([
    f"[{idx}] Collection: {doc.collection} | Source: {source}\n{doc.content}"
    for idx, doc in enumerate(retrieved, start=1)
])

# LLM 调用：约束输出 token
response = await app_ctx.anthropic_client.messages.create(
    max_tokens=2048,    # 输出 token 上限
    system=system_prompt,  # ~50 tokens（精简 system prompt）
    messages=[{"role": "user", "content": user_message}],
)
```

**典型 token 预算估算**（limit=5）：

| 组成部分 | 估算 token |
|---|---|
| System prompt | ~50 |
| Symptom + context | ~100 |
| 4 集合 × 5 chunk × ~250 tokens/chunk | ~5000 |
| 格式标签（`[N] Collection: ... Source: ...`）| ~200 |
| **总 input** | **~5350** |
| **max output** | **2048** |

设置 `limit=5` 使整个 RCA 调用在约 7400 tokens 内完成，对应 Claude Sonnet 的成本约 $0.02/次。

### 8.3 搜索工具的 limit 参数控制

所有搜索工具统一提供 `limit` 参数，由 AI agent 按需控制 context 消耗：

```python
limit: int = Field(
    default=10,
    ge=1,
    le=50,       # 硬上限，防止过大 context 消耗
    description="Maximum number of results to return (1-50)",
)
```

agent 可以按任务复杂度调整：
- 快速确认：`limit=3`（节省 token）
- 深度研究：`limit=20`（全面覆盖）
- 根因分析：`limit=5`（平衡精度与 token）

### 8.4 Chunk 大小与 context 效率

```
MarkdownChunker: 1000 chars ÷ 4 chars/token ≈ 250 tokens/chunk
CodeChunker:     1500 chars ÷ 4 chars/token ≈ 375 tokens/chunk

默认 limit=10 时：
  文档搜索：10 × 250 = ~2500 tokens
  代码搜索：10 × 375 = ~3750 tokens
```

1000/1500 字符的分块大小在"语义完整性"与"context 空间效率"之间取得了良好平衡——每个 chunk 有足够上下文，又不会过度消耗 AI agent 的上下文窗口。

---

## 9. 根因分析：RAG + LLM 合成

`root_cause_analysis` 是系统最高价值的工具，体现了 RAG 的完整价值：检索增强 + LLM 推理。

### 9.1 完整调用链

```python
async def root_cause_analysis(symptom, context=None, limit=5):

    # Step 1: 工具级缓存检查
    cache_key = _tool_cache._make_key("root_cause_analysis", symptom=symptom, context=context, limit=limit)
    hit, cached = _tool_cache.get(cache_key)
    if hit:
        return cached

    # Step 2: 组合查询（symptom + 可选的额外上下文）
    combined_query = f"{symptom} {context}" if context else symptom

    # Step 3: 混合检索（4 集合 × dense+sparse 并发，RRF 融合）
    retrieved = await app_ctx.hybrid_retriever.retrieve(
        query=combined_query,
        limit=limit,     # 每集合检索 limit 个，最终 RRF 后取 top-limit
    )

    # Step 4: 格式化 context block（带编号、集合标签、来源 URL）
    context_lines = []
    for idx, doc in enumerate(retrieved, start=1):
        source_label = doc.metadata.get("url") or doc.metadata.get("source", "unknown")
        context_lines.append(
            f"[{idx}] Collection: {doc.collection} | Source: {source_label}\n"
            f"{doc.content}"
        )
    context_block = "\n\n---\n\n".join(context_lines)

    # Step 5: 构建 LLM 消息
    user_parts = [f"Symptom: {symptom}"]
    if context:
        user_parts.append(f"Additional context:\n{context}")
    user_parts.append(f"Retrieved documents:\n\n{context_block}")
    user_message = "\n\n".join(user_parts)

    system_prompt = (
        "You are a Gardener Kubernetes expert. Analyse the symptom and retrieved"
        " context to produce a structured root cause analysis with:"
        " 1) Most likely root cause,"
        " 2) Supporting evidence from retrieved documents,"
        " 3) Recommended remediation steps."
    )

    # Step 6: 调用 LLM（Claude via SAP Hyperspace，不直接调 api.anthropic.com）
    response = await app_ctx.anthropic_client.messages.create(
        model=app_ctx.settings.anthropic_model,  # 默认：anthropic--claude-sonnet-latest
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    # Step 7: 提取文本 + 写入工具缓存
    result = response.content[0].text
    _tool_cache.set(cache_key, result)
    return result
```

### 9.2 LLM 输出结构

System prompt 要求模型按固定结构回答：

```
1) Most likely root cause
   <分析最可能的根本原因>

2) Supporting evidence from retrieved documents
   引用 [1]、[3] 等编号文档作为证据支撑

3) Recommended remediation steps
   具体的修复步骤
```

编号格式（`[1]`、`[2]`）与 context_block 中的 `[idx]` 对应，使 LLM 能准确引用来源。

---

## 10. SAP GitHub 按需写入缓存（write-through）

`cache_sap_github_content` 工具实现了独特的**按需向量化写入**模式，使 AI agent 能即时将 SAP 内部 GitHub 内容纳入 RAG 检索范围。

### 10.1 设计动机

公共 `github.com/gardener/*` 内容通过离线 ingestion 脚本批量向量化。SAP 内部 `github.tools.sap` 上的私有仓库内容无法预先批量摄取，需要在 AI agent 访问时动态写入。

### 10.2 执行流程

```
agent 调用 github-tools MCP（SAP GitHub Enterprise）
  └── 获取到 issue/PR/code 原始内容
  │
  ▼
cache_sap_github_content(
    content_type="issue",
    content="<issue body + comments>",
    sap_github_url="https://github.tools.sap/my-org/my-repo/issues/42",
    sap_github_repo="my-org/my-repo",
    issue_metadata={...},
)
  │
  ├─ 1. 验证 content_type（必须是 issue/pr/code/doc）
  │
  ├─ 2. 构建 metadata（标记 source_origin="sap_github"，区分来源）
  │
  ├─ 3. 存在性探测：
  │       probe_retriever.retrieve(query=sap_github_url, filters={"url": sap_github_url}, limit=1)
  │       already_existed = len(probe_results) > 0
  │
  ├─ 4. 选择 Chunker（code → CodeChunker，其他 → MarkdownChunker）
  │
  ├─ 5. 分块 → 批量 embed → ensure_collection + upsert
  │
  └─ 返回 CacheSapGithubContentResult{chunks_upserted, collection, already_existed}
```

### 10.3 来源区分 Tag

```python
metadata = {
    "source_origin": "sap_github",    # 区分标记
    "sap_github_repo": sap_github_repo,
    "url": sap_github_url,
    "content_type": content_type,
    # ... 类型特定 metadata
}
```

搜索结果中 `metadata.source_origin` 可用于区分公共 `github.com` 内容与 SAP 内部 `github.tools.sap` 内容。

### 10.4 集合路由

```python
_SAP_GITHUB_COLLECTION_MAP = {
    "issue": "gardener_issues",   # SAP issue → 与公共 issue 同集合
    "pr":    "gardener_prs",
    "code":  "gardener_code",
    "doc":   "gardener_docs",
}
```

SAP 内容与公共内容**混合存储**在同一集合中，`source_origin` 字段区分来源。这样可以在一次 `search_issues` 调用中同时返回公共 GitHub 和 SAP 内部的相关 Issues。

---

## 11. 架构决策（ADR）速查

| ADR | 决策 | 理由 |
|---|---|---|
| **ADR-001** | 四个独立 Qdrant 集合 | 独立调优、清晰 schema 演进、各类型数据特征不同 |
| **ADR-003** | HNSW m=16, ef_construct=100 | 文档规模（数十万向量）的召回率/速度/内存三角平衡 |
| **ADR-004** | 依赖注入（AppContext 冻结 Pydantic 模型） | 可测试、可替换实现、无并发修改风险 |
| **ADR-005** | HybridRetriever：dense+sparse 并发 + RRF(k=60) | BEIR 基准验证的融合算法，无需分数归一化 |
| **ADR-006** | LLM 通过 SAP Hyperspace 代理（不直接调 api.anthropic.com） | 数据合规，SAP 内部网络不出境 |

---

## 附录：关键参数速查

```bash
# === Embedding ===
GARDENER_MCP_EMBEDDING_MODEL=text-embedding-3-small
GARDENER_MCP_EMBEDDING_DIMENSIONS=1536
GARDENER_MCP_EMBEDDING_CACHE_SIZE=256       # Layer 1 缓存大小（0=禁用）

# === Tool Cache ===
GARDENER_MCP_TOOL_CACHE_TTL_SECONDS=3600    # Layer 2 TTL（0=禁用）
GARDENER_MCP_TOOL_CACHE_MAX_SIZE=128        # Layer 2 最大条目数（0=禁用）

# === Qdrant ===
GARDENER_MCP_QDRANT_URL=http://localhost:6333
GARDENER_MCP_QDRANT_BATCH_SIZE=100          # upsert 批量大小

# === LLM ===
GARDENER_MCP_ANTHROPIC_MODEL=anthropic--claude-sonnet-latest
GARDENER_MCP_ANTHROPIC_BASE_URL=http://localhost:6655/anthropic/
GARDENER_MCP_API_TIMEOUT_MS=3000000         # LLM 超时（毫秒）

# === Ingestion ===
GARDENER_MCP_INGESTION_MAX_ISSUES=1000      # 0=不限
GARDENER_MCP_INGESTION_MAX_PRS=500          # 0=不限
GARDENER_MCP_INGESTION_ISSUES_BATCH_SIZE=100

# === Transport ===
GARDENER_MCP_TRANSPORT=stdio                # stdio | http | streamable-http | sse
```
