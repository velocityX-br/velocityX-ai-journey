# sci-ai-mcp 安装与配置指南

本文档说明如何在本机安装 `sci-ai-mcp` 并将其注册到 Claude Code。
配套文档：[`README.md`](./README.md)（功能概览）、[`USAGE.md`](./USAGE.md)（使用流程与工具选择）。

`sci-ai-mcp` 是一个**文档检索 RAG** MCP server，基于 Qdrant 向量库检索 SCI
（SAP Converged Infrastructure）的 operation / customer 文档。

---

## 前置依赖

安装前请确认以下条件（“提供服务”与“灌数据”所需依赖不同）：

| 依赖 | 用途 | 默认地址 | 何时需要 |
| ---- | ---- | -------- | -------- |
| **uv** | Python 包管理 / 运行器 | — | 始终需要（`brew install uv` 或见 astral.sh/uv） |
| **Qdrant** | 向量库（RAG 语料） | `http://localhost:6333` | **提供服务时必需**，且必须已灌好数据 |
| **Hyperspace（OpenAI 端点）** | 生成 query embedding | `http://localhost:6655/openai/v1` | **每次检索都需要** |
| **Hyperspace（Anthropic 端点）** | `root_cause_analysis` 的 LLM | `http://localhost:6655/anthropic/` | 仅 `root_cause_analysis` 需要 |
| **SAP GHE PAT** | 灌数据时拉取文档 | `github.wdf.sap.corp` | **仅灌数据时需要**，提供服务不需要 |

一句话原则：
- **提供检索服务**只需要：Qdrant（含数据）+ Hyperspace embeddings。
- **灌数据（ingestion）**还额外需要：有效的 SAP GHE PAT + SAP CA 证书链。

---

## 方式一：一键脚本（推荐）

仓库根目录的 `mcp-servers/install.sh` 会自动完成依赖安装、`.env` 检查，并**打印**注册命令
（脚本本身不会替你执行 `claude mcp add`，也不会写入任何密钥）。

```bash
cd mcp-servers

# 只处理 sci-ai-mcp（会执行 uv sync 并检查 .env）
./install.sh --only sci

# 或处理全部 server
./install.sh
```

脚本会：
1. 检查 `uv` / `node` / `python3` 工具链；
2. 在 `sci-ai-mcp/` 执行 `uv sync` 安装依赖；
3. 若无 `.env` 则从 `.env.example` 复制，并**提示**哪些密钥仍是占位符；
4. 打印可直接复制粘贴的 `claude mcp add` 注册命令。

按提示把 `.env` 里被标红的密钥填好，然后复制脚本打印的注册命令执行即可。

---

## 方式二：手动安装（分步）

### 1. 安装依赖

```bash
cd mcp-servers/sci-ai-mcp
uv sync
```

### 2. 配置 `.env`

```bash
cp .env.example .env
```

编辑 `.env`，至少填入：

| 变量 | 说明 |
| ---- | ---- |
| `ANTHROPIC_AUTH_TOKEN` | Hyperspace bearer token（embeddings + RCA LLM 调用） |
| `ANTHROPIC_BASE_URL` | 默认 `http://localhost:6655/anthropic/` |
| `HYPERSPACE_OPENAI_BASE_URL` | 默认 `http://localhost:6655/openai/v1`（embeddings） |
| `QDRANT_URL` | 默认 `http://localhost:6333` |
| `GITHUB_TOKEN` | SAP GHE PAT — **仅灌数据时需要** |
| `GITHUB_CA_BUNDLE` | SAP CA 证书链 — **仅灌数据时需要**（构建方法见 `.env.example`） |

> **切勿提交 `.env`**（已被 `.gitignore` 忽略）。
> 带 `SCI_MCP_` 前缀的变量优先级高于环境里已有的 `ANTHROPIC_*`，因此本 server
> 可与 Claude Code CLI 自己的环境变量独立配置。

### 3. 健康检查

```bash
uv run python scripts/healthcheck.py    # 探测 Qdrant /healthz；退出码 0 = 正常
```

### 4. 确认语料已灌入

server 只提供检索，**不会**自动灌数据。首次使用需确保两个 collection 已有数据：

```bash
curl -s localhost:6333/collections/sci_docs_operation | python3 -c "import sys,json;print(json.load(sys.stdin)['result']['points_count'])"
curl -s localhost:6333/collections/sci_docs_customer  | python3 -c "import sys,json;print(json.load(sys.stdin)['result']['points_count'])"
```

若为空或不存在，执行灌数据（需 PAT + CA bundle，详见 `USAGE.md` §3）：

```bash
uv run ingest-docs --collections operation customer
uv run ingest-docs --check
```

预期规模：`sci_docs_operation` ≈ 16k，`sci_docs_customer` ≈ 5.3k。

### 5. 注册到 Claude Code

```bash
claude mcp add --scope user sci-ai-mcp -- \
  uv --directory /绝对路径/mcp-servers/sci-ai-mcp run python -m sci_mcp.server
```

- `--scope user`：全局可用（与本仓库其它 server 一致）。也可用 `--scope project` 仅在当前项目生效。
- 无需在注册命令里传 `env` —— server 从 `.env` + `config/settings.py` 读取配置。

验证：

```bash
claude mcp list        # 应能看到 sci-ai-mcp
```

---

## 注册配置形态（供参考）

注册后写入 `~/.claude.json` 的形态如下（stdio transport）：

```json
{
  "mcpServers": {
    "sci-ai-mcp": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory",
        "/绝对路径/mcp-servers/sci-ai-mcp",
        "run", "python", "-m", "sci_mcp.server"
      ],
      "env": {}
    }
  }
}
```

如需以网络服务（SSE）方式运行，见 `USAGE.md` §4。

---

## 冒烟测试（可选）

不经 Claude 直接验证 server 能启动并加载 AppContext：

```bash
cd mcp-servers/sci-ai-mcp
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  | uv run python -m sci_mcp.server
```

正常应返回一段包含 `"serverInfo":{"name":"sci-ai-mcp",...}` 的 JSON，
stderr 日志出现 `AppContext built successfully.`。

---

## 常见问题

| 现象 | 可能原因 / 处理 |
| ---- | -------------- |
| `claude mcp list` 看不到 sci-ai-mcp | 注册 scope 不对或未执行 add；重跑注册命令 |
| server 启动但检索全空 | collection 为空或 `QDRANT_URL` 不对 —— 跑 `ingest-docs --check` |
| 灌数据报 `SSLCertVerificationError` | `GITHUB_CA_BUNDLE` 缺失/不对（见 `.env.example` 构建方法） |
| `root_cause_analysis` 报错但普通检索正常 | Anthropic 端点/token 配错（`ANTHROPIC_BASE_URL` / `_AUTH_TOKEN`） |
| 灌数据后结果仍是旧的 | 工具结果有 TTL 缓存 —— 等 TTL 过期或重启 server |

更多使用与运维细节见 [`USAGE.md`](./USAGE.md)。
