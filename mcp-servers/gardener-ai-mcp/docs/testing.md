# gardener-ai-mcp 测试文档

> 版本：0.1.0
> 最后更新：2026-06-04
> Python 要求：3.12+

---

## 目录

1. [概述](#1-概述)
2. [环境准备](#2-环境准备)
3. [快速开始](#3-快速开始)
4. [测试模块详解](#4-测试模块详解)
5. [测试数据与 Fixture 说明](#5-测试数据与-fixture-说明)
6. [覆盖率目标](#6-覆盖率目标)
7. [集成测试 vs 单元测试](#7-集成测试-vs-单元测试)
8. [常见问题排查](#8-常见问题排查)
9. [CI 中的测试流程](#9-ci-中的测试流程)
10. [扩展测试](#10-扩展测试)

---

## 1. 概述

### 1.1 测试策略

本项目采用**单元测试为主**的策略。所有外部依赖（GitHub API、Qdrant、OpenAI Embeddings API、Anthropic API）均通过 mock 隔离，测试套件在不需要任何真实网络连接或运行中的外部服务的情况下即可执行。

核心原则：

- 每个被测单元通过**依赖注入**接收其外部依赖，这使得在测试中替换为 mock 对象变得直接。
- 异步代码（`async def`）通过 `pytest-asyncio`（配置为 `asyncio_mode = "auto"`）统一处理，无需在每个测试中手动管理事件循环。
- 网络层 mock 采用两种策略：针对 PyGithub 的同步阻塞调用使用 `unittest.mock.MagicMock`；针对 OpenAI SDK 的 httpx 层使用 `respx` 进行 HTTP 级拦截。

### 1.2 测试框架

| 工具 | 版本要求 | 用途 |
|------|----------|------|
| `pytest` | >=8.4 | 测试运行器和断言框架 |
| `pytest-asyncio` | >=0.24 | 异步测试支持（`asyncio_mode = "auto"`）|
| `pytest-mock` | >=3.14.0 | `mocker` fixture，提供 `mocker.patch()` 等便利方法 |
| `pytest-cov` | >=6.0 | 代码覆盖率收集与报告 |
| `respx` | >=0.22.0 | httpx 请求拦截，用于 mock OpenAI HTTP 调用 |

### 1.3 测试层次结构

测试按照与生产代码相同的层次结构组织，从底层数据获取到顶层 MCP 工具注册，自底向上依次为：

```
tests/
│
├── ingestion/            # 第 2 阶段：数据摄取层
│   ├── test_base.py          Document 模型 + BaseIngester 抽象契约
│   ├── test_chunking.py      MarkdownChunker + CodeChunker
│   ├── test_github_docs.py   GitHubDocsIngester（文档 + 提案）
│   ├── test_github_issues.py GitHubIssuesIngester
│   ├── test_github_prs.py    GitHubPRsIngester + _extract_linked_issues
│   └── test_code_indexer.py  CodeIngester + _extract_package + _extract_signatures
│
├── embeddings/           # 第 3 阶段：向量嵌入层
│   └── test_openai_embedder.py  HyperspaceEmbedder（respx HTTP mock）
│
├── vectorstore/          # 第 3 阶段：向量存储层
│   └── test_qdrant.py    QdrantVectorStore（AsyncMock 客户端）
│
├── retrieval/            # 第 4 阶段：检索层
│   ├── test_semantic.py  SemanticRetriever（fixture 驱动）
│   └── test_hybrid.py    HybridRetriever + reciprocal_rank_fusion（RRF 纯函数测试）
│
└── gardener_mcp/         # 第 5 阶段：MCP 工具层
    └── test_tools.py     所有 7 个 MCP 工具（AppContext mock_construct）
```

整个流水线的数据流向：

```
ingestion → embeddings → vectorstore → retrieval → gardener_mcp/tools
```

---

## 2. 环境准备

### 2.1 安装依赖

项目使用 `uv` 进行依赖管理。测试依赖声明在 `pyproject.toml` 的 `[project.optional-dependencies]` `dev` 组中。

安装所有依赖（包括 dev 依赖）：

```bash
uv sync --extra dev
```

或使用 dependency-groups 方式（两者均可）：

```bash
uv sync
```

验证安装是否成功：

```bash
uv run python -c "import pytest, respx, fastmcp; print('OK')"
```

### 2.2 环境变量

**测试不需要任何真实的 API 密钥**。所有外部调用在测试中均被 mock，不会发出真实的网络请求。

参考文件 `/Users/I577081/Workdir/Claude_Gardener/gardener-ai-mcp/.env.example`，实际运行服务时需要的变量如下，但**运行测试时均不需要设置**：

| 变量名 | 用途 | 测试中是否需要 |
|--------|------|----------------|
| `GITHUB_TOKEN` | GitHub API 访问令牌 | 否（mock） |
| `ANTHROPIC_AUTH_TOKEN` | Anthropic LLM 调用令牌 | 否（mock） |
| `ANTHROPIC_BASE_URL` | SAP Hyperspace Anthropic 代理地址 | 否（mock） |
| `HYPERSPACE_OPENAI_BASE_URL` | SAP Hyperspace OpenAI 代理地址 | 否（respx mock） |
| `QDRANT_URL` | Qdrant 向量数据库地址 | 否（AsyncMock） |
| `QDRANT_API_KEY` | Qdrant API 密钥 | 否（AsyncMock） |

对于需要构造真实 `Settings` 对象的测试（如 `test_openai_embedder.py`、`test_qdrant.py`），测试内部通过 `_make_settings()` 辅助函数硬编码最小化的假值（如 `GITHUB_TOKEN="test-token"`），完全不依赖环境变量。

### 2.3 Python 版本

项目要求 Python **3.12 或更高版本**，在 `pyproject.toml` 中声明为 `requires-python = ">=3.12"`。可通过 `uv` 自动管理 Python 版本，也可手动确认：

```bash
python --version   # 须为 3.12.x 或更高
```

---

## 3. 快速开始

### 3.1 运行全部测试

```bash
uv run pytest
```

输出示例（`--tb=short` 为默认选项）：

```
collected 68 items

tests/ingestion/test_base.py ............
tests/ingestion/test_chunking.py ....................
...
68 passed in 3.21s
```

### 3.2 运行单个模块

```bash
# 仅运行摄取层测试
uv run pytest tests/ingestion/ -v

# 仅运行嵌入层测试
uv run pytest tests/embeddings/ -v

# 仅运行检索层测试
uv run pytest tests/retrieval/ -v

# 仅运行 MCP 工具层测试
uv run pytest tests/gardener_mcp/ -v

# 运行单个测试文件
uv run pytest tests/retrieval/test_hybrid.py -v

# 运行单个测试函数
uv run pytest tests/retrieval/test_hybrid.py::test_rrf_merges_two_lists -v
```

### 3.3 运行带覆盖率报告

```bash
# 生成终端覆盖率摘要
uv run pytest --cov=. --cov-report=term-missing

# 生成 HTML 覆盖率报告
uv run pytest --cov=. --cov-report=html

# 查看 HTML 报告（macOS）
open htmlcov/index.html
```

### 3.4 覆盖率门槛检查

```bash
# 覆盖率低于 80% 时测试失败（CI 强制要求）
uv run pytest --cov=. --cov-fail-under=80
```

---

## 4. 测试模块详解

---

### 4.1 `tests/ingestion/test_base.py`

**测试目标**：`ingestion/base.py` — `Document` Pydantic 模型、`BaseIngester` 抽象基类约束、`IngestionError` 异常类。

**测试列表**：

| 测试函数 | 说明 |
|----------|------|
| `TestDocument::test_document_requires_content_and_source` | Document 仅需 `content` 和 `source` 两个字段即可实例化 |
| `TestDocument::test_document_id_auto_generated` | 每个 Document 自动生成唯一 UUID，两个实例的 `id` 不同 |
| `TestDocument::test_document_id_is_stable` | `id` 创建后保持不变（非动态属性） |
| `TestDocument::test_document_metadata_accepts_arbitrary_types` | `metadata` 接受字符串、列表、整数、布尔值、`None` 等任意 JSON 可序列化类型 |
| `TestBaseIngesterAbstract::test_direct_instantiation_raises` | 直接实例化 `BaseIngester` 必须抛出 `TypeError` |
| `TestBaseIngesterAbstract::test_subclass_without_ingest_raises` | 未实现 `ingest()` 的子类实例化时也必须抛出 `TypeError` |
| `TestBaseIngesterAbstract::test_concrete_subclass_is_instantiable` | 实现了 `ingest()` 的具体子类可以正常实例化 |
| `TestBaseIngesterAbstract::test_concrete_ingest_returns_documents` | `ingest()` 实现必须返回 `list[Document]` |
| `TestBaseIngesterAbstract::test_concrete_ingest_may_return_empty_list` | `ingest()` 返回空列表是合法的 |
| `TestIngestionError::test_str_without_source` | 无 `source` 参数时，`str(err)` 包含消息和类名 |
| `TestIngestionError::test_str_with_source` | 有 `source` 参数时，`str(err)` 包含 source 字符串 |
| `TestIngestionError::test_is_exception` | `IngestionError` 是可抛出和可捕获的 `Exception` 子类 |

**Mock 策略**：本文件无任何外部依赖，不需要 mock。所有测试均针对纯 Pydantic 模型和 Python ABC 机制，使用内联定义的最小具体子类（`ConcreteIngester`、`EmptyIngester`）验证抽象合约。

**运行命令**：

```bash
uv run pytest tests/ingestion/test_base.py -v
```

---

### 4.2 `tests/ingestion/test_chunking.py`

**测试目标**：`ingestion/chunking.py` — `MarkdownChunker`（使用 LangChain `MarkdownHeaderTextSplitter`）和 `CodeChunker`（使用 LangChain `RecursiveCharacterTextSplitter`）。

**测试列表**：

| 测试函数 | 说明 |
|----------|------|
| `TestMarkdownChunker::test_returns_list_of_documents` | `chunk()` 返回非空 `list[Document]` |
| `TestMarkdownChunker::test_chunk_index_in_metadata` | 每个 chunk 的 `metadata` 中必须包含 `chunk_index` |
| `TestMarkdownChunker::test_total_chunks_in_metadata` | 每个 chunk 的 `metadata` 中必须包含 `total_chunks` |
| `TestMarkdownChunker::test_parent_id_in_metadata` | 每个 chunk 的 `metadata` 中必须包含 `parent_id` |
| `TestMarkdownChunker::test_parent_id_matches_source_document` | `parent_id` 必须等于源 Document 的 `id` |
| `TestMarkdownChunker::test_chunk_index_is_sequential` | `chunk_index` 从 0 开始连续递增，无跳跃 |
| `TestMarkdownChunker::test_total_chunks_is_consistent` | 所有 chunk 的 `total_chunks` 与实际 chunk 数量一致 |
| `TestMarkdownChunker::test_chunk_inherits_parent_metadata` | chunk 的 `metadata` 中包含父 Document 的所有原始字段 |
| `TestMarkdownChunker::test_source_is_preserved` | chunk 的 `source` 与父 Document 的 `source` 相同 |
| `TestMarkdownChunker::test_empty_content_produces_one_chunk` | 空内容文档仍然产生恰好 1 个 chunk |
| `TestMarkdownChunker::test_chunk_many_flattens_results` | `chunk_many()` 将多个文档的 chunk 合并为单一扁平列表 |
| `TestMarkdownChunker::test_chunk_size_affects_output_count` | 更小的 `chunk_size` 产生更多 chunk |
| `TestCodeChunker::test_returns_list_of_documents` | `chunk()` 返回非空 `list[Document]` |
| `TestCodeChunker::test_chunk_index_in_metadata` | 每个 chunk 携带 `chunk_index` |
| `TestCodeChunker::test_total_chunks_in_metadata` | 每个 chunk 携带 `total_chunks` |
| `TestCodeChunker::test_parent_id_in_metadata` | 每个 chunk 携带 `parent_id` |
| `TestCodeChunker::test_parent_id_matches_source_document` | `parent_id` 等于源 Document 的 `id` |
| `TestCodeChunker::test_chunk_index_is_sequential` | `chunk_index` 连续递增 |
| `TestCodeChunker::test_total_chunks_is_consistent` | `total_chunks` 与实际 chunk 数量一致 |
| `TestCodeChunker::test_inherits_parent_metadata` | 继承父 Document 的任意 metadata 字段（如 `language`、`package`） |
| `TestCodeChunker::test_empty_content_produces_one_chunk` | 空内容文档产生 1 个 chunk |
| `TestCodeChunker::test_chunk_many_returns_flat_list` | `chunk_many()` 对混合类型文档（Markdown + 代码）返回扁平列表 |

**Mock 策略**：无外部依赖。测试使用模块顶部定义的两个字符串常量作为测试内容：`SAMPLE_MARKDOWN`（Gardener 架构文档片段）和 `SAMPLE_CODE`（Go 控制器源码片段）。通过 `_make_doc()` 辅助函数将字符串封装为 `Document`。

**运行命令**：

```bash
uv run pytest tests/ingestion/test_chunking.py -v
```

---

### 4.3 `tests/ingestion/test_github_docs.py`

**测试目标**：`ingestion/github_docs.py` — `GitHubDocsIngester` 和 `_is_proposal_path` 路径分类辅助函数。

**测试列表**：

| 测试函数 | 说明 |
|----------|------|
| `TestIsProposalPath::test_proposal_directory_detected` | `docs/proposals/001-new-feature.md` 识别为提案路径 |
| `TestIsProposalPath::test_proposals_plural_detected` | `website/proposals/index.md` 识别为提案路径 |
| `TestIsProposalPath::test_gep_directory_detected` | `gep/001/README.md` 识别为提案路径 |
| `TestIsProposalPath::test_regular_doc_not_detected` | `website/documentation/concepts/shoot.md` 不识别为提案 |
| `TestIsProposalPath::test_case_insensitive` | `PROPOSALS/001.md`（大写）仍然识别为提案路径 |
| `TestGitHubDocsIngesterInit::test_instantiation_with_client_and_settings` | 接受 `github_client` 和 `settings` 参数正常实例化 |
| `TestGitHubDocsIngesterIngest::test_ingest_is_coroutine` | `ingest()` 是可 `await` 的协程函数 |
| `TestGitHubDocsIngesterIngest::test_ingest_returns_list_of_documents` | API 成功时返回 `list[Document]` |
| `TestGitHubDocsIngesterIngest::test_ingest_raises_on_repo_access_failure` | `get_repo()` 失败时抛出包含仓库名的 `IngestionError` |
| `TestGitHubDocsIngesterIngest::test_document_metadata_has_required_fields` | 每个 Document 包含 `repo`、`path`、`sha`、`url`、`content_type` 字段 |
| `TestGitHubDocsIngesterIngest::test_content_type_doc_for_website_files` | `website/` 目录下的文件 `content_type` 为 `'doc'` |
| `TestGitHubDocsIngesterIngest::test_content_type_proposal_for_proposal_directories` | 提案目录下的文件 `content_type` 为 `'proposal'` |
| `TestGitHubDocsIngesterIngest::test_non_md_files_are_skipped` | `.yaml`、`.png` 等非 `.md` 文件被忽略，不生成 Document |

**Mock 策略**：

- `gh = MagicMock()`：mock 整个 `Github` 客户端对象。
- `gh.get_repo.return_value = repo_mock`：mock 仓库访问。
- `repo_mock.get_contents.side_effect = get_contents_side_effect`：通过 `side_effect` 函数模拟目录树遍历，根据不同路径参数返回不同的 `MagicMock` ContentFile 对象。
- `_make_content_file()`：工厂函数，构造模拟 PyGithub `ContentFile` 的 MagicMock，包含 `path`、`type`、`sha`、`html_url`、`encoding`、`content`、`decoded_content` 等属性（`content` 默认为 `"IyBIZWxsbw=="` 即 base64 编码的 `"# Hello"`）。
- `mocker.patch("ingestion.github_docs.asyncio.to_thread", side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs))`：将 `asyncio.to_thread`（用于将同步 PyGithub 调用卸载到线程池）替换为直接同步调用，使异步测试可以在单线程中运行。

**运行命令**：

```bash
uv run pytest tests/ingestion/test_github_docs.py -v
```

---

### 4.4 `tests/ingestion/test_github_issues.py`

**测试目标**：`ingestion/github_issues.py` — `GitHubIssuesIngester`，验证 Issue 到 Document 的映射、评论合并、元数据字段。

**测试列表**：

| 测试函数 | 说明 |
|----------|------|
| `TestGitHubIssuesIngesterInit::test_instantiation` | 接受 `github_client` 和 `settings` 正常实例化 |
| `TestGitHubIssuesIngesterIngest::test_ingest_is_coroutine` | `ingest()` 是协程函数 |
| `TestGitHubIssuesIngesterIngest::test_ingest_returns_list_of_documents` | 返回 `list[Document]`，每个 issue 对应一个 Document |
| `TestGitHubIssuesIngesterIngest::test_get_issues_called_with_state_all` | `repo.get_issues()` 必须以 `state='all'` 调用以捕获开放和关闭的 issue |
| `TestGitHubIssuesIngesterIngest::test_document_has_labels_field` | Document `metadata` 中的 `labels` 是包含标签名称的列表 |
| `TestGitHubIssuesIngesterIngest::test_document_has_state_field` | Document `metadata` 中包含 `state`（`'open'` 或 `'closed'`） |
| `TestGitHubIssuesIngesterIngest::test_document_has_created_at_field` | Document `metadata` 中的 `created_at` 是 ISO 8601 格式字符串 |
| `TestGitHubIssuesIngesterIngest::test_closed_at_is_none_for_open_issue` | 开放 issue 的 `closed_at` 为 `None` |
| `TestGitHubIssuesIngesterIngest::test_comments_included_in_content` | Document `content` 中包含 issue 评论的文本内容 |
| `TestGitHubIssuesIngesterIngest::test_ingest_raises_on_repo_failure` | `get_repo()` 失败时抛出 `IngestionError` |
| `TestGitHubIssuesIngesterIngest::test_multiple_issues_returned` | 5 个 issue 产生 5 个 Document，`issue_number` 元数据正确 |

**Mock 策略**：

- `_make_issue()`：构造模拟 PyGithub `Issue` 对象，支持配置 `number`、`title`、`body`、`state`、`labels`、`created_at`、`closed_at`、`html_url` 属性。
- `_make_label(name)`：构造 `label.name = name` 的 mock 标签对象。
- `_make_comment(body, author)`：构造带 `body` 和 `user.login` 的 mock 评论对象。
- `issue.get_comments.return_value = [comment]`：控制 issue 评论列表。
- `repo_mock.get_issues.return_value = [issue]`：控制 issue 列表返回值。
- `mocker.patch("ingestion.github_issues.asyncio.to_thread", ...)`：同 `test_github_docs.py`，用 `_async_call` shim 替代线程池。

**运行命令**：

```bash
uv run pytest tests/ingestion/test_github_issues.py -v
```

---

### 4.5 `tests/ingestion/test_github_prs.py`

**测试目标**：`ingestion/github_prs.py` — `GitHubPRsIngester` 和 `_extract_linked_issues` 正则表达式辅助函数。

**测试列表**：

| 测试函数 | 说明 |
|----------|------|
| `TestExtractLinkedIssues::test_bare_hash_reference` | `#123` 裸引用被正确提取 |
| `TestExtractLinkedIssues::test_fixes_keyword` | `Fixes #123` 提取 123 |
| `TestExtractLinkedIssues::test_closes_keyword` | `Closes #456` 提取 456 |
| `TestExtractLinkedIssues::test_resolves_keyword` | `Resolves #789` 提取 789 |
| `TestExtractLinkedIssues::test_multiple_references_deduped_and_sorted` | 多个引用去重并升序排序 |
| `TestExtractLinkedIssues::test_duplicate_references_deduplicated` | 同一 issue 号出现两次只保留一个 |
| `TestExtractLinkedIssues::test_no_references_returns_empty_list` | 无引用时返回空列表 |
| `TestExtractLinkedIssues::test_none_body_returns_empty_list` | `body=None` 时返回空列表（防止 NoneType 错误） |
| `TestExtractLinkedIssues::test_case_insensitive_keyword` | `FIXES`、`Closes` 等大小写不敏感 |
| `TestExtractLinkedIssues::test_complex_body` | 真实 PR 描述中混合关键字和裸引用均能正确提取 |
| `TestGitHubPRsIngesterInit::test_instantiation` | 正常实例化 |
| `TestGitHubPRsIngesterIngest::test_ingest_is_coroutine` | `ingest()` 是协程函数 |
| `TestGitHubPRsIngesterIngest::test_ingest_returns_list_of_documents` | 返回 `list[Document]` |
| `TestGitHubPRsIngesterIngest::test_document_has_pr_number` | `metadata['pr_number']` 包含正确的 PR 编号 |
| `TestGitHubPRsIngesterIngest::test_document_has_state` | `metadata['state']` 包含 PR 状态 |
| `TestGitHubPRsIngesterIngest::test_document_has_merged_flag` | `metadata['merged']` 为布尔值 |
| `TestGitHubPRsIngesterIngest::test_linked_issues_extracted_from_body` | `metadata['linked_issues']` 包含从 body 提取的 issue 编号列表 |
| `TestGitHubPRsIngesterIngest::test_linked_issues_empty_for_no_refs` | 无 issue 引用时 `linked_issues` 为空列表 |
| `TestGitHubPRsIngesterIngest::test_review_comments_included_in_content` | Document `content` 中包含 review comment 文本 |
| `TestGitHubPRsIngesterIngest::test_raises_on_repo_failure` | `get_repo()` 异常时抛出 `IngestionError` |
| `TestGitHubPRsIngesterIngest::test_get_pulls_called_with_state_all` | `repo.get_pulls()` 必须以 `state='all'` 调用 |

**Mock 策略**：

- `_make_pr()`：构造模拟 PyGithub `PullRequest` 对象，支持配置所有核心属性。
- `_make_review_comment(body, path)`：构造 review comment mock，包含 `body`、`path`、`user.login` 属性。
- `pr.get_review_comments.return_value`：控制 review comment 列表。
- `repo_mock.get_pulls.return_value`：控制 PR 列表。
- `mocker.patch("ingestion.github_prs.asyncio.to_thread", ...)`：同其他摄取器测试。

**运行命令**：

```bash
uv run pytest tests/ingestion/test_github_prs.py -v
```

---

### 4.6 `tests/ingestion/test_code_indexer.py`

**测试目标**：`ingestion/code_indexer.py` — `CodeIngester`、`_extract_package`（Go package 声明提取）、`_extract_signatures`（函数/类型签名提取）。

**测试列表**：

| 测试函数 | 说明 |
|----------|------|
| `TestExtractPackage::test_extracts_package_name` | 从 `SAMPLE_GO_SOURCE` 提取 `'controller'` |
| `TestExtractPackage::test_extracts_simple_package` | 从 `package main` 声明提取 `'main'` |
| `TestExtractPackage::test_returns_unknown_when_missing` | 无 `package` 声明时返回 `'unknown'` |
| `TestExtractPackage::test_constants_package` | 从 `SAMPLE_GO_NO_FUNCS` 提取 `'constants'` |
| `TestExtractSignatures::test_extracts_func_signatures` | 提取到 `func (r *ShootReconciler) Reconcile(` |
| `TestExtractSignatures::test_extracts_multiple_funcs` | `Reconcile` 和 `syncShoot` 均出现在结果中 |
| `TestExtractSignatures::test_extracts_type_declarations` | 提取到 `type ShootReconciler struct` |
| `TestExtractSignatures::test_extracts_multiple_types` | `ShootReconciler` 和 `ShootStatus` 均出现 |
| `TestExtractSignatures::test_no_funcs_returns_fallback` | 无函数/类型声明时返回前 500 个字符作为 fallback |
| `TestCodeIngesterInit::test_instantiation` | 正常实例化 |
| `TestCodeIngesterIngest::test_ingest_is_coroutine` | `ingest()` 是协程函数 |
| `TestCodeIngesterIngest::test_ingest_returns_list_of_documents` | 单个 `.go` 文件仓库返回 `list[Document]` |
| `TestCodeIngesterIngest::test_document_has_language_go` | 所有 Document 的 `metadata['language']` 为 `'go'` |
| `TestCodeIngesterIngest::test_document_has_package_metadata` | `metadata['package']` 包含从 Go 源码提取的包名 |
| `TestCodeIngesterIngest::test_non_go_files_are_skipped` | `.yaml` 等非 `.go` 文件不产生 Document |
| `TestCodeIngesterIngest::test_function_signature_extracted_in_content` | Document `content` 中至少包含一个 `func ` 模式 |
| `TestCodeIngesterIngest::test_raises_on_repo_failure` | `get_repo()` 失败时抛出 `IngestionError` |
| `TestCodeIngesterIngest::test_vendor_directory_skipped` | `vendor/` 目录被完全跳过，不产生任何 Document |
| `TestCodeIngesterIngest::test_document_has_repo_metadata` | `metadata['repo']` 包含正确的仓库名 |

**Mock 策略**：

- `_make_content_file(path, source=SAMPLE_GO_SOURCE)`：构造模拟 PyGithub ContentFile，`decoded_content` 为 Go 源码字节串，`content` 为 base64 编码版本（通过 `base64.b64encode()` 动态生成）。
- `get_contents_side_effect(path)`：模拟仓库根目录遍历，`path == ""` 返回文件列表，`path == "具体文件路径"` 返回单个文件对象。
- `vendor_dir = _make_content_file(path="vendor", file_type="dir")`：模拟 `vendor/` 目录，验证其内容不被递归爬取。
- `mocker.patch("ingestion.code_indexer.asyncio.to_thread", ...)`：同其他摄取器测试。

**运行命令**：

```bash
uv run pytest tests/ingestion/test_code_indexer.py -v
```

---

### 4.7 `tests/embeddings/test_openai_embedder.py`

**测试目标**：`embeddings/openai_embedder.py` — `HyperspaceEmbedder`，包括批处理逻辑、tenacity 重试机制、模型参数透传。

**测试列表**：

| 测试函数 | 说明 |
|----------|------|
| `TestHyperspaceEmbedder::test_embed_query_returns_vector` | `embed_query()` 返回长度等于配置维度的 `list[float]` |
| `TestHyperspaceEmbedder::test_embed_documents_batches_at_2048` | 2049 个文本产生恰好 2 次 HTTP 调用（2048 + 1 的批次分割） |
| `TestHyperspaceEmbedder::test_retry_on_429` | 第一次 429 响应触发 tenacity 重试，第二次 200 成功后返回正确向量 |
| `TestHyperspaceEmbedder::test_uses_model_from_settings` | 发送给 API 的请求体中 `model` 字段与 `settings.embedding_model` 一致 |

**Mock 策略**：

本文件使用 `respx` 在 HTTP 传输层进行拦截，是项目中最底层的 mock 方式：

```python
# 构造目标 http://test/v1 的 AsyncOpenAI 客户端（max_retries=0 禁止 SDK 自带重试）
client = openai.AsyncOpenAI(
    base_url="http://test/v1",
    api_key="test",
    max_retries=0,
)
embedder = HyperspaceEmbedder(settings=settings, client=client)

# respx.mock(base_url="http://test") 拦截该 origin 的所有 httpx 请求
async with respx.mock(base_url="http://test") as mock:
    mock.post("/v1/embeddings").mock(return_value=_ok_response([expected_vector]))
    result = await embedder.embed_query("What is Gardener?")
```

- `_ok_response(vectors)`：构造 200 响应，body 为符合 OpenAI API 格式的 JSON（包含 `object`、`data`、`model`、`usage` 字段）。
- `_rate_limit_response()`：构造 429 响应，body 包含 `error.code = "rate_limit_exceeded"`。
- `route.side_effect = [resp1, resp2]`：为同一路由配置响应序列，模拟先失败后成功的场景。
- `patch("embeddings.openai_embedder.wait_exponential", return_value=lambda *_: 0)`：将 tenacity 的指数退避等待替换为零等待，使重试测试不会真正等待。

**重要 ADR 说明**：`respx.mock(base_url="http://test")` 中的 `base_url` 必须与 `AsyncOpenAI(base_url="http://test/v1")` 的 origin（scheme + host，不含路径）完全一致。路由路径 `/v1/embeddings` 为完整路径（包含 base_url 的路径前缀 `/v1`）。

**运行命令**：

```bash
uv run pytest tests/embeddings/test_openai_embedder.py -v
```

---

### 4.8 `tests/vectorstore/test_qdrant.py`

**测试目标**：`vectorstore/qdrant.py` — `QdrantVectorStore`，包括集合创建、批量 upsert、向量搜索、健康检查。

**测试列表**：

| 测试函数 | 说明 |
|----------|------|
| `TestQdrantVectorStore::test_ensure_collection_creates_when_not_exists` | 集合不存在时以正确的 `VectorParams`（size=1536, distance=COSINE）调用 `create_collection` |
| `TestQdrantVectorStore::test_ensure_collection_skips_when_exists` | 集合已存在时不调用 `create_collection` |
| `TestQdrantVectorStore::test_upsert_batches_correctly` | 250 个 document 在 `batch_size=100` 时产生恰好 3 次 upsert 调用（100+100+50） |
| `TestQdrantVectorStore::test_search_returns_search_results` | `search()` 返回 `SearchResult` 对象列表，`collection` 字段正确 |
| `TestQdrantVectorStore::test_health_check_returns_true` | `get_collections()` 成功时 `health_check()` 返回 `True` |

**Mock 策略**：

使用 `_make_mock_client()` 工厂函数构造模拟 `AsyncQdrantClient`：

```python
def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.collection_exists = AsyncMock()
    client.create_collection = AsyncMock()
    client.create_payload_index = AsyncMock()
    client.upsert = AsyncMock()
    client.query_points = AsyncMock()
    client.delete = AsyncMock()
    client.get_collections = AsyncMock()
    return client
```

关键点：使用 `AsyncMock` 而非普通 `MagicMock` 来声明 Qdrant 客户端的异步方法，确保 `await client.upsert(...)` 等调用能正常工作。

- `mock_client.collection_exists.return_value = False/True`：控制集合存在性检查。
- `mock_client.query_points.return_value = QueryResponse(points=[...])`：返回包含 `ScoredPoint` 对象的真实 Qdrant 模型实例（非 mock），确保反序列化逻辑被正确测试。
- `_make_scored_point(doc_id, content, score)`：构造真实的 `ScoredPoint` 实例用于搜索结果测试。

**运行命令**：

```bash
uv run pytest tests/vectorstore/test_qdrant.py -v
```

---

### 4.9 `tests/retrieval/test_semantic.py`

**测试目标**：`retrieval/semantic.py` — `SemanticRetriever`，验证嵌入器和向量存储的编排逻辑、参数透传。

**测试列表**：

| 测试函数 | 说明 |
|----------|------|
| `test_retrieve_embeds_query_and_searches` | `embed_query` 以原始 query 字符串调用；`search` 以返回的向量和配置的 collection 调用；结果原样返回 |
| `test_retrieve_passes_filters` | 调用方传入的 `filters` 字典原样透传到 `vector_store.search` 的第四个位置参数 |
| `test_retrieve_passes_limit` | 自定义 `limit` 值原样透传到 `vector_store.search` 的第三个位置参数 |

**Mock 策略**：

本文件采用 **fixture 驱动** 的模式，fixtures 声明在文件顶部：

```python
@pytest.fixture()
def mock_embedder() -> AsyncMock:
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return embedder

@pytest.fixture()
def mock_vector_store() -> AsyncMock:
    store = AsyncMock()
    store.search = AsyncMock(
        return_value=[_make_result("doc-1", 0.95), _make_result("doc-2", 0.80)]
    )
    return store

@pytest.fixture()
def retriever(mock_embedder, mock_vector_store) -> SemanticRetriever:
    return SemanticRetriever(
        embedder=mock_embedder,
        vector_store=mock_vector_store,
        collection="gardener_docs",
    )
```

断言使用 `assert_awaited_once_with()` 方法（而非 `assert_called_once_with()`），以正确验证协程被 `await` 的情况：

```python
mock_embedder.embed_query.assert_awaited_once_with(
    "how does Gardener manage shoot clusters?"
)
mock_vector_store.search.assert_awaited_once_with(
    "gardener_docs", [0.1, 0.2, 0.3], 10, None,
)
```

**运行命令**：

```bash
uv run pytest tests/retrieval/test_semantic.py -v
```

---

### 4.10 `tests/retrieval/test_hybrid.py`

**测试目标**：`retrieval/hybrid.py` — `reciprocal_rank_fusion` 纯函数（RRF 算法）和 `HybridRetriever`（多集合并发检索 + 结果融合）。

**测试列表**：

| 测试函数 | 说明 |
|----------|------|
| `test_rrf_single_list` | 单列表 RRF 分配 `1/(60+rank)` 分值，结果保持原顺序 |
| `test_rrf_merges_two_lists` | 出现在两个列表中的文档累加 RRF 分值，排名高于仅出现在一个列表的文档 |
| `test_rrf_deduplicates_by_id` | 同一文档 ID 在两个列表中出现时，输出中只出现一次 |
| `test_rrf_empty_lists_are_ignored` | 空列表不引发错误，不贡献任何文档 |
| `test_rrf_all_empty_returns_empty_list` | 全空输入返回空列表 |
| `test_rrf_score_set_to_rrf_not_original` | 输出分值是 RRF 公式值（`1/61` ≈ 0.01639），而非原始相似度分值（0.99） |
| `test_hybrid_uses_asyncio_gather` | 2 个集合产生恰好 4 次 search 调用（2 集合 × 2 搜索类型），两个集合各被调用 2 次 |
| `test_hybrid_queries_all_collections` | 2 个集合时至少有 4 次 search 调用，两个集合名均出现在调用参数中 |
| `test_hybrid_returns_top_k` | 最终结果数量不超过请求的 `limit`（即使各搜索返回大量结果） |
| `test_hybrid_default_collections_are_all_four` | `collections=None` 时搜索所有 4 个标准集合（`gardener_docs`、`gardener_issues`、`gardener_prs`、`gardener_code`），总计 8 次 search 调用 |

**Mock 策略**：

- RRF 纯函数测试（`test_rrf_*`）：**无任何 mock**，直接使用 `_result()` 辅助函数构造 `SearchResult` 对象。
- `HybridRetriever` 测试：使用 `AsyncMock` fixtures（`mock_embedder`、`mock_vector_store`）。
- `test_hybrid_returns_top_k` 使用 `MagicMock` 而非 `AsyncMock` 作为 store 容器，仅将 `store.search` 设为 `AsyncMock`，避免在 MagicMock 属性访问时产生未被 await 的协程：

```python
store = MagicMock()
store.search = AsyncMock(side_effect=side_effect)
```

**重要 ADR 说明**：不要使用 `patch("asyncio.gather")` 来测试并发行为，这会导致 `RuntimeWarning: coroutine was never awaited`。改为通过断言 `mock_vector_store.search.await_count == 4` 来验证所有协程均被执行。

**运行命令**：

```bash
uv run pytest tests/retrieval/test_hybrid.py -v
```

---

### 4.11 `tests/gardener_mcp/test_tools.py`

**测试目标**：`gardener_mcp/tools.py` — 所有 7 个 MCP 工具的注册和路由逻辑：`search_docs`、`search_issues`、`search_proposals`、`search_code`、`root_cause_analysis`、`rag_retrieve`。（测试文件顶部注释列出 7 项，其中 `search_prs` 包含在工具注册中。）

**测试列表**：

| 测试函数 | 说明 |
|----------|------|
| `test_search_docs_calls_semantic_retriever` | `search_docs` 委托给 `semantic_retriever.retrieve`，传入正确的 query；返回 `list[ToolSearchResult]` |
| `test_search_issues_passes_state_filter` | `search_issues` 将 `state='open'` 作为 payload filter 传递给检索器 |
| `test_search_proposals_adds_content_type_filter` | `search_proposals` 始终附加 `filters={'content_type': 'proposal'}` |
| `test_search_code_passes_repo_filter` | `search_code` 将 `repo='gardener/gardener'` 传递给检索器 |
| `test_root_cause_analysis_uses_hybrid_retriever` | `root_cause_analysis` 调用 `hybrid_retriever.retrieve`，而非 `semantic_retriever.retrieve` |
| `test_root_cause_analysis_calls_anthropic` | `root_cause_analysis` 调用 `anthropic_client.messages.create`，使用 settings 中指定的模型名 |
| `test_rag_retrieve_uses_specified_collection` | `rag_retrieve` 以指定的 `collection` 名称构造 `SemanticRetriever` |

**Mock 策略**：

本文件采用 `AppContext.model_construct()` 模式（绕过 Pydantic 字段验证）注入完整的 mock 上下文：

```python
return AppContext.model_construct(
    settings=mock_settings,
    embedder=mock_embedder,
    vector_store=mock_vector_store,
    semantic_retriever=mock_semantic_retriever,   # retrieve = AsyncMock(return_value=...)
    hybrid_retriever=mock_hybrid_retriever,       # retrieve = AsyncMock(return_value=...)
    anthropic_client=mock_anthropic_client,       # messages.create = AsyncMock(...)
)
```

工具通过 `_call_tool()` 辅助函数调用，该函数直接调用 FastMCP 工具注册表中的底层函数，绕过 MCP 协议层：

```python
fresh_mcp = FastMCP("test-gardener")
register_tools(fresh_mcp)
mock_ctx = MagicMock()
mock_ctx.lifespan_context = {"app_context": app_ctx}
tool = await fresh_mcp.get_tool(tool_name)
return await tool.fn(inp=inp, ctx=mock_ctx)
```

对于需要验证过滤器传递的测试（`test_search_issues_passes_state_filter` 等），在 `with patch("gardener_mcp.tools.SemanticRetriever") as MockRetriever:` 块内实例化新的 `FastMCP` 并注册工具，捕获 `retrieve` 的调用参数后进行断言。

**注意**：patch 路径必须是 `"gardener_mcp.tools.SemanticRetriever"`（模块重命名后的正确路径），而非旧的 `"mcp.tools.SemanticRetriever"`。

**运行命令**：

```bash
uv run pytest tests/gardener_mcp/test_tools.py -v
```

---

## 5. 测试数据与 Fixture 说明

### 5.1 conftest.py

当前项目在 `tests/` 目录或其子目录下没有 `conftest.py` 文件。所有 fixture 均声明在各自的测试文件中，保持本地化和模块隔离。

如未来需要跨模块共享 fixture（例如共享的 `Settings` 实例或公共的 `mock_embedder`），建议在 `tests/conftest.py` 中集中声明。

### 5.2 各模块典型 Fixture 示例

**fixture 声明（retrieval/semantic 风格）**：

```python
@pytest.fixture()
def mock_embedder() -> AsyncMock:
    """返回固定向量 [0.1, 0.2, 0.3] 的 mock 嵌入器。"""
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return embedder

@pytest.fixture()
def mock_vector_store() -> AsyncMock:
    """返回两个固定结果的 mock 向量存储。"""
    store = AsyncMock()
    store.search = AsyncMock(
        return_value=[_make_result("doc-1", 0.95), _make_result("doc-2", 0.80)]
    )
    return store
```

**`_make_settings()` 模式（Settings 对象构造）**：

`pydantic-settings` 的 `AliasChoices` 字段通过别名名称（非 Python 属性名）传入关键字参数：

```python
def _make_settings() -> Settings:
    return Settings(
        GITHUB_TOKEN="test-token",             # 别名，对应 github_token 属性
        HYPERSPACE_OPENAI_BASE_URL="http://test/v1",
        ANTHROPIC_AUTH_TOKEN="test-key",
        EMBEDDING_MODEL="text-embedding-3-small",
        EMBEDDING_DIMENSIONS=3,
    )
```

### 5.3 Mock 对象创建模式

#### AsyncMock — 异步方法 mock

用于需要 `await` 的方法：

```python
from unittest.mock import AsyncMock

mock_method = AsyncMock(return_value=some_value)
await mock_method(arg1, arg2)
mock_method.assert_awaited_once_with(arg1, arg2)  # 注意：用 assert_awaited_once_with，不是 assert_called_once_with
```

#### MagicMock — 同步对象/属性 mock

用于同步属性访问或不需要 `await` 的方法：

```python
from unittest.mock import MagicMock

mock_obj = MagicMock()
mock_obj.some_attr = "value"
mock_obj.sync_method.return_value = 42
```

#### respx — HTTP 层 mock（仅用于 embeddings 测试）

```python
import respx, httpx

async with respx.mock(base_url="http://test") as mock:
    # 注册路由：POST http://test/v1/embeddings
    mock.post("/v1/embeddings").mock(return_value=httpx.Response(200, json={...}))

    # 模拟响应序列（先失败后成功）
    route = mock.post("/v1/embeddings")
    route.side_effect = [error_response, success_response]

    # 执行被测代码
    result = await embedder.embed_query("test")

    # 验证调用次数
    assert route.call_count == 2
```

#### `mocker.patch()` — `asyncio.to_thread` mock（摄取器测试）

```python
async def _async_call(fn, *args, **kwargs):
    """将同步函数包装为协程，无需真实线程池。"""
    return fn(*args, **kwargs)

mocker.patch(
    "ingestion.github_docs.asyncio.to_thread",
    side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
)
```

---

## 6. 覆盖率目标

### 6.1 当前配置

`pyproject.toml` 中的覆盖率配置：

```toml
[tool.coverage.run]
source = ["ingestion", "embeddings", "vectorstore", "retrieval", "config", "scripts", "gardener_mcp"]
omit = ["tests/*", "*/conftest.py"]

[tool.coverage.report]
show_missing = true
skip_covered = false
fail_under = 70        # 当前基线：70%
```

### 6.2 CI/CD 强制要求

Phase 8 CI/CD 流水线将强制执行 **80%** 覆盖率门槛（高于当前 `pyproject.toml` 中的 70% 基线）：

```bash
uv run pytest --cov=. --cov-fail-under=80
```

### 6.3 各模块覆盖率优先级

按优先级从高到低：

| 优先级 | 模块 | 原因 |
|--------|------|------|
| 最高 | `gardener_mcp/tools.py` | MCP 服务对外暴露的核心接口，直接影响 AI 代理行为 |
| 高 | `retrieval/hybrid.py` | RRF 算法逻辑复杂，多集合并发路径多 |
| 高 | `ingestion/chunking.py` | 分块策略直接影响检索质量，边界条件多 |
| 中 | `ingestion/github_*.py` | 数据摄取质量影响整个 RAG 管道 |
| 中 | `vectorstore/qdrant.py` | 批处理逻辑和搜索参数映射 |
| 中 | `embeddings/openai_embedder.py` | 批次边界和重试逻辑 |
| 低 | `retrieval/semantic.py` | 逻辑简单，主要是参数透传 |

### 6.4 运行覆盖率检查

```bash
# 终端报告（显示缺失行号）
uv run pytest --cov=. --cov-report=term-missing

# HTML 报告（可在浏览器中浏览每行覆盖情况）
uv run pytest --cov=. --cov-report=html
open htmlcov/index.html

# CI 门槛检查（失败时退出码非零）
uv run pytest --cov=. --cov-fail-under=80

# XML 格式（用于 CI 系统上传）
uv run pytest --cov=. --cov-report=xml:coverage.xml
```

---

## 7. 集成测试 vs 单元测试

### 7.1 当前状态

**当前测试套件中的所有测试均为单元测试**。没有任何测试需要：

- 运行中的 Qdrant 实例
- 真实的 GitHub API token 和网络连接
- SAP Hyperspace LLM 代理访问权限
- 任何形式的 Docker 或 Kubernetes 环境

### 7.2 未来集成测试规划

随着项目进入 Phase 8（CI/CD）及生产部署阶段，将引入集成测试。集成测试使用 `@pytest.mark.integration` 标记与单元测试区分：

```python
import pytest

@pytest.mark.integration
async def test_qdrant_real_upsert_and_search() -> None:
    """验证真实 Qdrant 实例的 upsert 和向量搜索端对端流程。

    前置条件：
        - Qdrant 运行于 http://localhost:6333
        - 环境变量 QDRANT_URL 已设置
    """
    ...

@pytest.mark.integration
async def test_hyperspace_embedding_real_api() -> None:
    """验证 SAP Hyperspace OpenAI 代理的真实嵌入生成。

    前置条件：
        - HYPERSPACE_OPENAI_BASE_URL 已设置
        - 有效的 ANTHROPIC_AUTH_TOKEN（用于代理认证）
    """
    ...
```

在 `pyproject.toml` 中注册自定义 marker 以避免 `--strict-markers` 报错：

```toml
[tool.pytest.ini_options]
markers = [
    "integration: marks tests that require real external services (deselect with '-m not integration')",
]
```

### 7.3 运行排除集成测试

```bash
# 仅运行单元测试（默认，当前套件的全部测试）
uv run pytest -m "not integration"

# 仅运行集成测试（需要真实外部服务）
uv run pytest -m "integration"

# 同时运行所有测试（包括集成测试）
uv run pytest
```

---

## 8. 常见问题排查

### 8.1 `ImportError: No module named 'mcp.types'`

**现象**：

```
ImportError: No module named 'mcp.types'
# 或
from mcp.types import Tool
ModuleNotFoundError: No module named 'mcp.types'
```

**原因**：项目早期将 MCP 工具包命名为 `mcp/`，与 PyPI 上的 `mcp` 包（FastMCP 的底层依赖）发生命名冲突。Python 的模块解析优先加载本地目录，导致 `import mcp` 加载了本项目的目录而非 PyPI 包。

**解决方案**：MCP 工具包已重命名为 `gardener_mcp/`。所有导入路径均已更新：

```python
# 正确：
from gardener_mcp.tools import register_tools
from gardener_mcp.context import AppContext

# 错误（已废弃，不要使用）：
from mcp.tools import register_tools
```

如果在 `mocker.patch()` 中看到 `patch("mcp.tools.SemanticRetriever")`，必须改为 `patch("gardener_mcp.tools.SemanticRetriever")`。

### 8.2 `asyncio event loop` 警告或错误

**现象**：

```
PytestUnraisableExceptionWarning: Exception ignored in: ...
RuntimeError: Event loop is closed
```

**原因**：`pytest-asyncio` 在 `asyncio_mode = "auto"` 以外的模式下需要手动在每个异步测试上添加 `@pytest.mark.asyncio` 装饰器。

**解决方案**：确认 `pyproject.toml` 包含以下配置：

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

启用此选项后，所有 `async def test_*` 函数自动被视为异步测试，无需额外装饰器（虽然显式添加 `@pytest.mark.asyncio` 也不会报错）。

### 8.3 `respx` mock 未匹配请求

**现象**：

```
httpx.ConnectError: [Errno 8] nodename nor servname provided, or not known
# 或
respx.exceptions.AllMockedError: All outgoing HTTPX requests must be mocked
```

**原因**：`respx.mock(base_url="http://test")` 中指定的 base_url 与 `AsyncOpenAI(base_url="http://test/v1")` 不匹配。respx 的 `base_url` 仅匹配 origin（scheme + host + 可选端口），不包含路径。

**解决方案**：

```python
# 正确：base_url 只包含 origin
async with respx.mock(base_url="http://test") as mock:
    mock.post("/v1/embeddings").mock(...)  # 路径包含完整的 /v1 前缀

# 错误：base_url 包含路径前缀会导致路由匹配失败
async with respx.mock(base_url="http://test/v1") as mock:
    mock.post("/embeddings").mock(...)
```

同时确保 `openai.AsyncOpenAI` 的 `max_retries=0`，否则 SDK 会在 respx mock 范围外发起重试请求导致超时。

### 8.4 `RuntimeWarning: coroutine was never awaited`

**现象**：

```
RuntimeWarning: coroutine 'AsyncMock._execute_mock_call' was never awaited
```

**原因**：对 `asyncio.gather` 本身使用 `patch()` 会阻止 `gather` 实际 await 其内部的协程对象，这些协程在被垃圾回收时产生警告。

**解决方案**：不要 patch `asyncio.gather`。改为通过断言 mock 的调用次数来验证并发行为：

```python
# 正确：验证 await_count 而非 patch gather
assert mock_vector_store.search.await_count == 4

# 错误：patch gather 会留下未 await 的协程
with patch("asyncio.gather") as mock_gather:  # 不要这样做
    ...
```

### 8.5 `Settings` 构造失败

**现象**：

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
github_token
  Field required [type=missing, ...]
```

**原因**：`Settings` 使用 `pydantic-settings` 的 `AliasChoices`，字段通过别名（环境变量名）解析，在测试中通过关键字参数传入时需使用别名名称。

**解决方案**：

```python
# 正确：使用别名（大写环境变量名）
Settings(
    GITHUB_TOKEN="test-token",
    HYPERSPACE_OPENAI_BASE_URL="http://test/v1",
)

# 错误：使用 Python 属性名
Settings(
    github_token="test-token",           # ValidationError
    hyperspace_openai_base_url="http://test/v1",
)
```

---

## 9. CI 中的测试流程

### 9.1 流水线阶段顺序

Phase 8 CI/CD 流水线按以下顺序执行（每个阶段失败则后续阶段不运行）：

```
lint → test → build
```

各阶段具体命令：

```bash
# 1. lint（代码风格和静态分析）
uv run ruff check .
uv run ruff format --check .
uv run mypy .

# 2. test（测试 + 覆盖率门槛）
uv run pytest --cov=. --cov-report=xml:coverage.xml --cov-fail-under=80

# 3. build（Docker 镜像构建）
docker build -f docker/Dockerfile -t gardener-ai-mcp:ci .
```

### 9.2 覆盖率报告上传

Phase 8 CI/CD 会将 `coverage.xml` 上传到覆盖率追踪服务（如 Codecov 或 SonarQube），在 PR 评论中展示覆盖率变化趋势。XML 格式报告的生成命令：

```bash
uv run pytest --cov=. --cov-report=xml:coverage.xml
```

### 9.3 失败时查看详细输出

CI 中默认使用 `--tb=short`（在 `pyproject.toml` 的 `addopts` 中配置）。当需要调试失败原因时，在本地运行：

```bash
# 详细回溯，显示完整堆栈
uv run pytest -v --tb=long

# 在第一个失败时停止（快速定位问题）
uv run pytest -v --tb=long -x

# 显示最慢的 10 个测试（性能分析）
uv run pytest --durations=10

# 重新运行上次失败的测试
uv run pytest --lf -v --tb=long
```

---

## 10. 扩展测试

### 10.1 为新的 Ingester 添加测试

以下模板适用于新增 `ingestion/github_releases.py` 等新摄取器：

```python
# tests/ingestion/test_github_releases.py

"""Tests for ingestion/github_releases.py — GitHubReleasesIngester."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ingestion.base import Document, IngestionError
from ingestion.github_releases import GitHubReleasesIngester


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_settings(gardener_repo: str = "gardener/gardener") -> Any:
    settings = MagicMock()
    settings.github_gardener_repo = gardener_repo
    return settings


def _make_release(
    tag_name: str = "v1.90.0",
    name: str = "Gardener v1.90.0",
    body: str = "## Changes\n- Fix shoot reconciliation",
    html_url: str = "https://github.com/gardener/gardener/releases/tag/v1.90.0",
) -> MagicMock:
    release = MagicMock()
    release.tag_name = tag_name
    release.name = name
    release.body = body
    release.html_url = html_url
    return release


async def _async_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGitHubReleasesIngesterInit:
    def test_instantiation(self) -> None:
        ingester = GitHubReleasesIngester(MagicMock(), _make_settings())
        assert ingester is not None


class TestGitHubReleasesIngesterIngest:

    @pytest.mark.asyncio
    async def test_ingest_is_coroutine(self) -> None:
        import inspect
        ingester = GitHubReleasesIngester(MagicMock(), _make_settings())
        assert inspect.iscoroutinefunction(ingester.ingest)

    @pytest.mark.asyncio
    async def test_ingest_returns_list_of_documents(self, mocker: Any) -> None:
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock
        repo_mock.get_releases.return_value = [_make_release()]

        mocker.patch(
            "ingestion.github_releases.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubReleasesIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert isinstance(result, list)
        assert all(isinstance(d, Document) for d in result)

    @pytest.mark.asyncio
    async def test_raises_on_repo_failure(self, mocker: Any) -> None:
        gh = MagicMock()
        gh.get_repo.side_effect = Exception("network error")
        settings = _make_settings()

        mocker.patch(
            "ingestion.github_releases.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubReleasesIngester(github_client=gh, settings=settings)
        with pytest.raises(IngestionError):
            await ingester.ingest()

    # 根据新 ingester 的实际 metadata 字段补充更多断言测试
    # ...
```

### 10.2 为新的 MCP Tool 添加测试

以下模板适用于在 `gardener_mcp/tools.py` 中新增工具（例如 `search_releases`）：

```python
# 在 tests/gardener_mcp/test_tools.py 中追加

@pytest.mark.asyncio
async def test_search_releases_passes_version_filter() -> None:
    """search_releases 必须将 version 参数作为 payload filter 传递。"""
    app_ctx = make_mock_context()

    captured_retrieve = AsyncMock(return_value=[_make_search_result("gardener_releases")])

    with patch("gardener_mcp.tools.SemanticRetriever") as MockRetriever:
        instance = MagicMock()
        instance.retrieve = captured_retrieve
        MockRetriever.return_value = instance

        fresh_mcp = FastMCP("test-gardener")
        register_tools(fresh_mcp)

        mock_ctx = MagicMock()
        mock_ctx.lifespan_context = {"app_context": app_ctx}

        tool = await fresh_mcp.get_tool("search_releases")
        inp = SearchReleasesInput(query="shoot hibernation fix", version="v1.90")
        await tool.fn(inp=inp, ctx=mock_ctx)

    captured_retrieve.assert_called_once()
    call_kwargs = captured_retrieve.call_args
    filters_passed = (
        call_kwargs.kwargs.get("filters")
        or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
    )
    assert filters_passed is not None
    assert filters_passed.get("version") == "v1.90"
```

**新增工具测试检查清单**：

1. 在 `gardener_mcp/models.py` 中定义输入模型（如 `SearchReleasesInput`），并在 `test_tools.py` 的导入块中引入。
2. 若工具使用 `SemanticRetriever`，使用 `patch("gardener_mcp.tools.SemanticRetriever")` 模式捕获构造参数和 `retrieve` 调用参数。
3. 若工具使用 `hybrid_retriever`，通过 `app_ctx.hybrid_retriever.retrieve.assert_called_once()` 直接断言（参见 `test_root_cause_analysis_uses_hybrid_retriever`）。
4. 若工具调用 LLM，通过 `app_ctx.anthropic_client.messages.create.assert_called_once()` 验证（参见 `test_root_cause_analysis_calls_anthropic`）。
5. 验证返回类型（`list[ToolSearchResult]` 或 `str`）。
