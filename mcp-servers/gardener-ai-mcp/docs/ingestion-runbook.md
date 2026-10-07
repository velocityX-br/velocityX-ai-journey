# Ingestion & Vector Store Runbook

本文档说明如何运行 `gardener-ai-mcp` 的 ingestion 管道（GitHub → Chunking → Embedding → Qdrant），
并解释 **增量更新** 的语义、限制与推荐操作方式。

---

## 1. 组件与数据流

```
GitHub (docs / issues / prs / code)
   → ingestion/*.py           (fetch)
   → ingestion/chunking.py    (chunk)
   → embeddings/openai_embedder.py (embed via Hyperspace)
   → vectorstore/qdrant.py    (upsert)
   → Qdrant (4 collections)
```

四个集合：

| Collection          | 数据源                          | Chunker 参数        |
| ------------------- | ------------------------------- | ------------------- |
| `gardener_docs`     | `gardener/documentation` (.md)  | 1000/200            |
| `gardener_issues`   | `gardener/gardener` issues       | 1000/200            |
| `gardener_prs`      | `gardener/gardener` PRs         | 1000/200            |
| `gardener_code`     | `gardener/gardener` (.go)       | 800/100             |

---

## 2. 前置条件

### 2.1 依赖服务

- **Qdrant** 运行在 `QDRANT_URL`（默认 `http://localhost:6333`）
- **Hyperspace / Embedding endpoint** 可达（`HYPERSPACE_OPENAI_BASE_URL`）
- **GitHub PAT** 可读 `gardener/documentation` 与 `gardener/gardener`

### 2.2 环境变量（`.env`）

```dotenv
# 必填
GITHUB_TOKEN=<your-pat>
ANTHROPIC_AUTH_TOKEN=<hyperspace-bearer-token>

# Qdrant
QDRANT_URL=http://localhost:6333
QDRANT_BATCH_SIZE=100

# Embedding
HYPERSPACE_OPENAI_BASE_URL=http://localhost:6655/openai/v1
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536

# 可选覆盖
GITHUB_DOCS_REPO=gardener/documentation
GITHUB_GARDENER_REPO=gardener/gardener
INGESTION_MAX_ISSUES=1000     # 0 = 无限制
INGESTION_ISSUES_BATCH_SIZE=100
```

### 2.3 Python 环境

```bash
cd mcp-servers/gardener-ai-mcp
uv sync
```

---

## 3. 常用命令

### 3.1 检查 Qdrant 状态（无副作用）

```bash
uv run python scripts/ingest_docs.py --check
```

输出每个集合的点数量；有空集合则 exit 1。

### 3.2 增量更新 docs + issues（本次目标）

```bash
uv run python scripts/ingest_docs.py --collections docs issues
```

### 3.3 只跑单一集合

```bash
uv run python scripts/ingest_docs.py --collections docs
uv run python scripts/ingest_docs.py --collections issues
uv run python scripts/ingest_docs.py --collections prs
uv run python scripts/ingest_docs.py --collections code
```

### 3.4 全量四集合

```bash
uv run python scripts/ingest_docs.py
```

---

## 4. 增量更新语义（重要）

### 4.1 Upsert 行为（`vectorstore/qdrant.py:186`）

Qdrant `upsert` 按 **point ID** 决定：
- 相同 ID → **覆盖**
- 新 ID → **插入**

### 4.2 当前实现的 ID 生成（`ingestion/base.py:32`）

```python
id: str = Field(default_factory=lambda: str(uuid.uuid4()))
```

每次 ingest 生成 **随机 UUID** → **不是稳定 ID**。

### 4.3 后果

| 场景                        | 结果                                                       |
| --------------------------- | ---------------------------------------------------------- |
| 上游新增文档                | ✅ 新内容会被检索到                                        |
| 上游更新文档                | ⚠️ **新旧 chunks 同时存在**（旧的不会被覆盖）              |
| 上游删除文档                | ❌ **Qdrant 中的旧向量不会被清除**                          |
| 重复运行相同 ingest         | ⚠️ 集合点数持续增长（chunks 累积）                          |

**结论：** 当前 ingestion 是 **append-mostly**，不是 idempotent 的增量更新。

### 4.4 推荐工作流

根据你对"一致性 vs 效率"的偏好选一种：

#### 模式 A — 追加式增量（快，允许少量重复）

适用：日常小规模更新，检索质量对重复不敏感。

```bash
uv run python scripts/ingest_docs.py --collections docs issues
uv run python scripts/ingest_docs.py --check
```

#### 模式 B — 干净重建（推荐用于 docs / issues 定期刷新）

适用：需要与上游 100% 一致；docs 集合规模小，重建成本低。

```bash
# 1. 用 Qdrant HTTP API 删除集合
curl -X DELETE http://localhost:6333/collections/gardener_docs
curl -X DELETE http://localhost:6333/collections/gardener_issues

# 2. 重新 ingest（会自动 ensure_collection）
uv run python scripts/ingest_docs.py --collections docs issues

# 3. 校验
uv run python scripts/ingest_docs.py --check
```

#### 模式 C — 严格 idempotent 增量（未来增强）

需要修改 `ingestion/base.py` 让 `Document.id` 基于 `source + chunk_index`
的确定性哈希（如 `uuid5`）。当前仓库尚未实现。

---

## 5. 本次执行计划：docs + issues 增量

**目标：** 拉取最新 Gardener 文档与 Issues 并入库。

**推荐命令（模式 A，最小侵入）：**

```bash
cd mcp-servers/gardener-ai-mcp
uv run python scripts/ingest_docs.py --check                    # 记录当前点数
uv run python scripts/ingest_docs.py --collections docs issues  # 执行增量
uv run python scripts/ingest_docs.py --check                    # 对比点数增长
```

**预期日志（示意）：**

```
──────────────────────────────────────────────────────────────
  Collection: gardener_docs
──────────────────────────────────────────────────────────────
  [1/5] Fetching documents from GitHub...
         Fetched   <N> documents  (<t>s)
  [2/5] Chunking documents...
         Produced  <M> chunks  (<t>s)
  [3/5] Ensuring Qdrant collection (dims=1536)...
  [4/5] Embedding <M> chunks in <B> batches...
  [5/5] Upserting <M> vectors to Qdrant...
```

---

## 6. 故障排查

| 症状                              | 检查点                                                 |
| --------------------------------- | ------------------------------------------------------ |
| `Cannot reach Qdrant`             | `curl $QDRANT_URL` / docker container status           |
| `GitHub 403 rate limit`           | `GITHUB_TOKEN` 权限与 rate limit                       |
| Embedding 请求超时                | `HYPERSPACE_OPENAI_BASE_URL` 可达性、token 有效性      |
| Docs ingest 慢                    | 由 Contents API 逐文件递归造成，正常                    |
| Issues 少于预期                   | `INGESTION_MAX_ISSUES` cap；设为 0 解除                |

---

## 7. 相关文件参考

- `scripts/ingest_docs.py:1` — CLI 入口
- `ingestion/base.py:17` — `Document` 模型（含 `id` 字段）
- `ingestion/github_docs.py:77` — Docs ingester
- `ingestion/github_issues.py:85` — Issues ingester
- `vectorstore/qdrant.py:186` — Upsert 实现
- `vectorstore/qdrant.py:105` — Collection 创建（HNSW m=16, ef=100, cosine）
