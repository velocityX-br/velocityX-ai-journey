# Gardener AI MCP —— 面试讲解文档

> 一个为 SAP Gardener 打造的**生产级 RAG 检索 MCP 服务器**。它把散落在 Gardener 官方文档、GitHub Issues/PRs、GEP 提案、Go 源码，以及 SAP 内网 canary/live 问题追踪仓库里的知识，统一向量化进 Qdrant，通过 **Model Context Protocol (MCP)** 让任何 AI Agent 都能做语义检索 + 根因分析（Root Cause Analysis）。

---

## 第一部分：项目价值（面试电梯陈述）

**一句话定位：** 让 AI Agent 排查 Shoot 集群问题时，不再靠模型记忆去猜，而是**基于 Gardener 一手文档、真实历史 issue 和源码**给出有据可查、可溯源的答案。

下面从六个维度证明这个 MCP 服务器的意义：

### 1. RAG 方面
不是简单的关键字搜索，而是完整的 **ingest → chunk → embed → retrieve** 管线。既有单集合的 `SemanticRetriever`（dense 向量），也有跨四个集合做 **RRF 融合**的 `HybridRetriever`；`root_cause_analysis` 更是在检索到的证据之上让 LLM 合成「最可能根因 + 支撑证据 + 修复步骤」——把检索从"找文档"升级成"给答案"。

### 2. 精准性
数据按来源切成 **4 个独立集合**（`gardener_docs / issues / prs / code`），每个可独立调 HNSW 参数、独立演进 schema；配合 `source_type / repo / state / language / source_origin / sap_github_repo` 的 **payload 索引**做过滤，检索不走全表扫描。Agent 可以精确地说"只查 SAP live 仓库里 open 状态的 issue"，而不是在噪声里捞针。

### 3. Vector 应用
Qdrant + HNSW + cosine，Embedding（1536d）。向量化不只覆盖文档，还覆盖 **Go 源码**和**真实运维 Issue**——这意味着 Agent 能把"文档怎么说"和"线上实际踩过什么坑"关联起来检索。

### 4. 增量更新
**page-by-page** 分页摄取，一次只在内存里保留一页，内存有界、尊重 GitHub 限流、每页可见进度；`--check` 随时核对点数；upsert 语义天然去重。实际用这套流程把 SAP 两个仓库 **1,000 个 issue → 25,950 向量**增量灌进已有集合，不影响存量数据。

### 5. 文档库灵活性
同一个 `gardener_issues` 集合里，用 `source_origin` 标签同时承载**公网 gardener/gardener** 和 **SAP 内网 github.tools.sap** 两个来源——新数据源只需打标签、加索引，不用重建集合。canary/live 这种"仓库本体很薄、核心信息全在 issue 里"的场景，正好被这套设计吃下。

### 6. Tooling
对外暴露一组语义清晰的 MCP 工具：`search_docs / search_issues / search_prs / search_proposals / search_code / rag_retrieve / root_cause_analysis`，外加 `cache_sap_github_content` 做 SAP 内网内容的即时 write-through 缓存。Pydantic 输入模型自带 Agent 可读的 JSON schema，参数边界（`ge/le`）和过滤语义都是自解释的。

---

## 第二部分：具体实现细节（逻辑 + 代码）

> 本节配合上面六点，逐一给出**真实代码**与设计逻辑，面试时可以按维度深入。

### 架构总览

```
Documentation / Issues / PRs / Code
        │  (ingestion, page-by-page)
        ▼
   MarkdownChunker / CodeChunker
        │  (chunking)
        ▼
   HyperspaceEmbedder (1536d)
        │  (embedding)
        ▼
   Qdrant  ── 4 collections, HNSW + cosine + payload indexes
        │  (retrieval)
        ▼
   SemanticRetriever  /  HybridRetriever (RRF)
        │
        ▼
   FastMCP Server ── 8 tools ── AI Agent
```

分层遵循**依赖注入**：retriever 只接收 `BaseEmbedder` + `BaseVectorStore` 接口，不 import 具体实现，使得单测无需真实 Qdrant/Embedding。

---

### 维度 1 & 2 — RAG 管线 & 精准性

**核心逻辑：** 单集合语义检索是最小单元。`SemanticRetriever` 只做两件事：把 query 编码成向量、把向量交给 vector store 搜。filters 原样透传，AND 组合，走 payload 索引。

```python
# retrieval/semantic.py
class SemanticRetriever(BaseRetriever):
    def __init__(self, embedder, vector_store, collection: str) -> None:
        self._embedder = embedder          # 依赖注入：只依赖接口
        self._vector_store = vector_store
        self._collection = collection      # 集合名构造期固定

    async def retrieve(self, query, filters=None, limit=10):
        query_vector = await self._embedder.embed_query(query)
        return await self._vector_store.search(
            self._collection, query_vector, limit, filters,
        )
```

**精准性落在 vector store 的 filter 翻译上** —— 每个 filter 键翻成一个 `FieldCondition + MatchValue`，全部塞进 `must`（隐式 AND）：

```python
# vectorstore/qdrant.py :: QdrantVectorStore.search
qdrant_filter = None
if filters:
    conditions = [
        FieldCondition(key=key, match=MatchValue(value=value))
        for key, value in filters.items()
    ]
    qdrant_filter = Filter(must=conditions)

query_response = await self._client.query_points(   # qdrant-client >= 1.13
    collection_name=collection,
    query=query_vector,
    limit=limit,
    query_filter=qdrant_filter,
    with_payload=True,
)
```

> **面试点：** 为什么用 `query_points` 而不是 `search`？—— qdrant-client ≥ 1.13 弃用了 `client.search()`，全模块统一走 `query_points()`。

---

### 维度 3 — Vector 应用（Qdrant + HNSW + payload 索引）

**集合创建逻辑：** 固定 cosine 距离、HNSW `m=16 / ef_construct=100`（文档规模下召回与建索引速度的平衡点），并为可过滤字段建 `KEYWORD` payload 索引。索引失败**只告警不致命**——集合仍可用，只是搜得慢。

```python
# vectorstore/qdrant.py :: ensure_collection
await self._client.create_collection(
    collection_name=collection,
    vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    hnsw_config=HnswConfigDiff(m=16, ef_construct=100),
)
for field_name in _INDEXED_PAYLOAD_FIELDS:
    try:
        await self._client.create_payload_index(
            collection_name=collection,
            field_name=field_name,
            field_schema=PayloadSchemaType.KEYWORD,
        )
    except Exception as exc:
        logger.warning("Failed to create payload index on %r: %s", field_name, exc)
```

被索引的字段（近期新增了后两个，用于区分 SAP 内网来源）：

```python
_INDEXED_PAYLOAD_FIELDS = (
    "source_type", "repo", "state", "language",
    "source_origin", "sap_github_repo",   # 新增：SAP GitHub 过滤
)
```

**向量维度永不 hardcode**，始终来自 `settings.embedding_dimensions`（1536，`text-embedding-3-small`）。

---

### 维度 4 — 增量更新（page-by-page + upsert 去重）

**核心逻辑：** 分页拉取 → 逐页 chunk → 逐页 embed → 逐页 upsert。一次只有一页在内存里，内存有界；每页之间可插入 `--page-delay` 尊重 GitHub 限流；每页打印进度。`PointStruct.id = document.id` 保证同一 issue 重新灌入是**覆盖而非追加**（upsert 语义天然去重）。

```python
# vectorstore/qdrant.py :: upsert  (分批，默认 batch_size=100)
for batch_start in range(0, len(documents), batch_size):
    doc_batch = documents[batch_start : batch_start + batch_size]
    vec_batch = vectors[batch_start : batch_start + batch_size]
    points = [
        PointStruct(
            id=doc.id,                       # 稳定 ID ⇒ 幂等 upsert
            vector=vec,
            payload={**doc.metadata, "content": doc.content, "source": doc.source},
        )
        for doc, vec in zip(doc_batch, vec_batch, strict=True)
    ]
    await self._client.upsert(collection_name=collection, points=points)
```

**实测结果（一次真实增量摄取）：**

| Repository | Issues | Pages | Chunks | Vectors |
|---|---|---|---|---|
| kubernetes-canary/issues-canary | 500 | 17 | 8,660 | 8,660 |
| kubernetes-live/issues-live | 500 | 17 | 17,290 | 17,290 |
| **TOTAL** | **1,000** | **34** | **25,950** | **25,950** |

`--check` 核对：`gardener_issues` 共 35,104 点，其中 `source_origin=sap_github` 25,950 点 —— 存量的 9,154 条公网 issue 完全没被动。

> **补充脚本 `scripts/add_payload_indexes.py`：** 幂等地为**已存在**的集合补建 payload 索引。因为 `_INDEXED_PAYLOAD_FIELDS` 新增字段后，已存在的集合不会自动获得新索引；此脚本 `create_payload_index` 逐字段执行，`already exists` 视为成功（`[present]`），其余按 `[created] / [FAILED]` 统计并据此返回退出码。

---

### 维度 5 — 文档库灵活性（一个集合，多来源，靠标签区分）

**核心逻辑：** SAP 内网 issue 和公网 issue 存**同一个** `gardener_issues` 集合，靠 `source_origin="sap_github"` 元数据区分。新增来源零成本——不建新集合、不改检索层，只在 ingest 时打标签、在 `_INDEXED_PAYLOAD_FIELDS` 加字段即可。

```python
# ingestion/sap_github_issues.py :: _build_document 的 metadata
metadata = {
    "source_origin": "sap_github",           # ← 区分公网 / SAP 内网的关键标签
    "sap_github_repo": self._repo_slug,      # ← 精确到具体仓库
    "issue_number": issue.number,
    "title": issue.title,
    "state": issue.state,                    # open / closed
    "labels": labels,
    "created_at": ...,
    "closed_at": ...,
    "url": issue.html_url,
}
```

对应的检索工具新增了两个过滤参数（近期改动），把标签暴露给 Agent：

```python
# gardener_mcp/tools.py :: search_issues
extra: dict[str, Any] = {}
if state is not None:            extra["state"] = state
if labels is not None:          extra["labels"] = labels
if source_origin is not None:   extra["source_origin"] = source_origin       # 新增
if sap_github_repo is not None: extra["sap_github_repo"] = sap_github_repo    # 新增
filters = _merge_filters(None, extra) or None
```

于是 Agent 可以精确调用：
```
search_issues(
    query="etcd defrag failing",
    source_origin="sap_github",
    sap_github_repo="kubernetes-live/issues-live",
    state="open",
)
```

---

### 维度 6 — Tooling（8 个 MCP 工具 + RCA + 缓存）

**（a）Hybrid Retrieval + RRF —— `root_cause_analysis` 的检索底座**

跨 4 个集合、每集合发 dense + sparse 两路搜，共 `2 × N` 个协程用**单次 `asyncio.gather`** 并发拉起，再用 Reciprocal Rank Fusion 融合。RRF 对 dense/sparse 分数不可比这一点天然鲁棒，`k=60` 是 BEIR 上的文献默认值。

```python
# retrieval/hybrid.py :: HybridRetriever.retrieve
query_vector = await self._embedder.embed_query(query)
sparse_filters = dict(filters) if filters else {}
sparse_filters["$text"] = query               # sparse/关键字信号

coroutines = []
for collection in self._collections:
    coroutines.append(self._vector_store.search(collection, query_vector, limit, filters))         # dense
    coroutines.append(self._vector_store.search(collection, query_vector, limit, sparse_filters))  # sparse
all_results = list(await asyncio.gather(*coroutines))   # 单次 gather，最大化 I/O 并发
return reciprocal_rank_fusion(all_results)[:limit]
```

RRF 是**模块级纯函数**（无需 embedder / store 即可单测）：

```python
# score(d) = Σ 1 / (k + rank(d, list_i))   —— 每个 list 内只计最高排名那次
def reciprocal_rank_fusion(ranked_lists, k: int = 60):
    rrf_scores: dict[str, float] = {}
    results_by_id: dict[str, SearchResult] = {}
    for ranked_list in ranked_lists:
        seen: set[str] = set()
        for rank_zero, result in enumerate(ranked_list):
            if result.id in seen:
                continue
            seen.add(result.id)
            rrf_scores[result.id] = rrf_scores.get(result.id, 0.0) + 1.0 / (k + rank_zero + 1)
            results_by_id.setdefault(result.id, result)
    fused = [SearchResult(..., score=rrf_scores[i]) for i in rrf_scores]
    fused.sort(key=lambda r: r.score, reverse=True)
    return fused
```

**（b）RCA 工具 —— 检索证据 → LLM 合成**

先用 HybridRetriever 拿证据，编号拼成 context block，配一个约束输出结构的 system prompt，交给 Anthropic 兼容端点（SAP Hyperspace）合成。

```python
# gardener_mcp/tools.py :: root_cause_analysis
combined_query = f"{symptom} {context}" if context else symptom
retrieved = await app_ctx.hybrid_retriever.retrieve(query=combined_query, limit=limit)

context_block = "\n\n---\n\n".join(
    f"[{i}] Collection: {d.collection} | Source: {d.metadata.get('url') or 'unknown'}\n{d.content}"
    for i, d in enumerate(retrieved, start=1)
)
system_prompt = (
    "You are a Gardener Kubernetes expert. ... produce a structured root cause analysis with:"
    " 1) Most likely root cause, 2) Supporting evidence, 3) Recommended remediation steps."
)
response = await app_ctx.anthropic_client.messages.create(
    model=app_ctx.settings.anthropic_model,
    max_tokens=2048,
    system=system_prompt,
    messages=[{"role": "user", "content": user_message}],
)
```

**（c）工具结果缓存 —— 进程内 TTL + FIFO**

单线程 async，无需锁。cache key 由 `sha256(json.dumps({tool, **kwargs}, sort_keys=True))` 生成 —— 所有过滤参数都进 key，保证 `source_origin` 不同的调用不会撞缓存。

```python
# gardener_mcp/tools.py :: _ToolCache
def _make_key(self, tool_name, **kwargs):
    payload = json.dumps({"tool": tool_name, **kwargs}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
# get(): 命中且未过期返回 (True, value)；过期则删除并 miss
# set(): 满容量时 FIFO 淘汰最老一条，写入 (monotonic()+ttl, value)
```

**（d）Write-through 缓存工具 `cache_sap_github_content`**

Agent 现场从 SAP 内网拉到的 issue/pr/code/doc，可即时向量化写回对应集合（`content_type → collection` 路由），同样打上 `source_origin="sap_github"`。

```python
_SAP_GITHUB_COLLECTION_MAP = {
    "issue": "gardener_issues", "pr": "gardener_prs",
    "code":  "gardener_code",   "doc": "gardener_docs",
}
```

---

## 第三部分：可能被追问的问题（速答）

| 追问 | 速答 |
|---|---|
| 为什么 4 个集合而不是 1 个？ | 独立 HNSW 调参、独立 schema 演进、检索时天然隔离噪声；跨集合需求由 HybridRetriever+RRF 覆盖。 |
| RRF 为什么优于加权分数融合？ | dense/sparse 分数量纲不可比，RRF 只用 rank、无需校准，`k=60` 文献默认即可用。 |
| 增量更新如何保证不重复？ | `PointStruct.id = document.id` 稳定 ID + Qdrant upsert 语义，重灌即覆盖。 |
| 新数据源接入成本？ | 打 `source_origin/sap_github_repo` 标签 + 加 payload 索引字段，检索层零改动。 |
| 已存在集合怎么补索引？ | `scripts/add_payload_indexes.py`，幂等，`already exists` 视为成功。 |
| sparse 现在是真 BM25 吗？ | 当前用 `$text` payload 过滤模拟关键字意图；接口稳定，未来可无缝替换为 Qdrant 原生 sparse vector / BM42。 |
| 依赖注入体现在哪？ | retriever 只依赖 `BaseEmbedder`/`BaseVectorStore` 接口，工具经 FastMCP lifespan 的 `AppContext` 拿单例，单测可注入 mock。 |

---

*本文档所有代码片段均摘自本仓库真实实现（`retrieval/`、`vectorstore/qdrant.py`、`gardener_mcp/tools.py`、`ingestion/sap_github_issues.py`、`scripts/add_payload_indexes.py`），可直接对照源码深入。*
