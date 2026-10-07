# SSE → Streamable HTTP 迁移评估报告

**项目**: gardener-ai-mcp
**评估日期**: 2026-06-09
**结论**: **建议迁移（低优先级，改动极小）**

---

## 1. 背景：为什么要评估这个问题？

MCP 协议在 2025-03-26 版本中将传输协议从两种（stdio + HTTP+SSE）调整为两种（stdio + Streamable HTTP），旧的 HTTP+SSE 被标注为 deprecated。gardener-ai-mcp 当前配置支持 `stdio`（默认，本地开发使用）和 `sse`（HTTP 服务部署使用），需要评估 SSE 是否需要迁移到新的 Streamable HTTP。

---

## 2. 协议层现状

### MCP 官方规范（2025-03-26）

```
当前标准传输：
  1. stdio           ← 本地/CLI 首选，无变化
  2. Streamable HTTP ← 取代旧版 HTTP+SSE

旧版已废弃：
  HTTP+SSE transport（协议版本 2024-11-05）← deprecated
```

官方规范原文：
> "This replaces the HTTP+SSE transport from protocol version 2024-11-05."

### Claude Code CLI 文档（官方）

> ⚠️ **Warning**: The SSE (Server-Sent Events) transport is **deprecated**. Use HTTP servers instead, where available.

Claude Code CLI 明确支持的传输类型：

| Transport | 命令 | 状态 |
|-----------|------|------|
| `http` (Streamable HTTP) | `claude mcp add --transport http` | **推荐** |
| `sse` | `claude mcp add --transport sse` | **已废弃** |
| `stdio` | `claude mcp add --transport stdio` | 本地首选 |
| `ws` (WebSocket) | `.mcp.json` 配置 | 特殊场景 |

注：`streamable-http` 是 `http` 的别名（来自 MCP 规范原名）。

### FastMCP 框架

FastMCP 文档将 SSE 描述为 "original" / "legacy" 传输，Streamable HTTP 为当前推荐：

```python
mcp.run(transport="http", host="0.0.0.0", port=8080)   # 推荐
mcp.run(transport="sse", host="0.0.0.0", port=8080)    # Legacy
```

---

## 3. 两种传输的技术差异

| 维度 | 旧版 HTTP+SSE | Streamable HTTP |
|------|-------------|-----------------|
| 端点数量 | 两个（`/sse` GET + `/message` POST）| **单一端点**（`/mcp` POST + GET）|
| 初始化方式 | 客户端先 GET /sse 建立流，再 POST 发消息 | 客户端直接 POST InitializeRequest |
| 服务端推送 | 仅通过 /sse 长连接推送 | POST 响应可选 SSE 流，GET 可建立推送流 |
| Session 管理 | 无官方标准 | `Mcp-Session-Id` header，有完整生命周期 |
| 断线续传 | 不支持 | 支持 `Last-Event-ID` 重传 |
| 安全性 | 无 DNS Rebinding 防护标准 | 规范要求验证 `Origin` header |
| 批量请求 | 不支持 | 支持 JSON-RPC batch |
| 客户端感知 | 需要感知两个端点 | 单一 URL，行为统一 |

**核心区别**：Streamable HTTP 是 HTTP+SSE 的超集——它仍然可以使用 SSE 作为响应流，但统一了端点设计，并增加了 Session、断线重传、批量等能力。

---

## 4. 当前代码实现分析

### server.py 传输层（当前实现）

```python
# gardener_mcp/server.py:96-105
transport = getattr(_settings, "mcp_transport", "stdio")

if transport == "sse":
    host = getattr(_settings, "mcp_host", "0.0.0.0")
    port = getattr(_settings, "mcp_port", 8080)
    mcp.run(transport="sse", host=host, port=port)
else:
    mcp.run(transport=transport)   # ← 直接传字符串给 FastMCP
```

### 关键发现

**好消息**：当前的 `else` 分支会将 `transport` 字符串原样传给 `mcp.run()`，这意味着：

> 现在只需将环境变量设为 `GARDENER_MCP_TRANSPORT=http`，理论上即可启用 Streamable HTTP。

**问题**：`http` transport 走 `else` 分支时，**`host` 和 `port` 不会被传入** `mcp.run()`，FastMCP 会使用其自身默认值（127.0.0.1:8000），与项目的 `mcp_host` / `mcp_port` 设置脱节。

### settings.py 问题

```python
mcp_transport: str = Field(
    default="stdio",
    description="MCP transport mechanism. 'stdio' for local/CLI use; 'sse' for HTTP+SSE server mode."
    # ↑ 没有提到 'http' 选项
)
```

---

## 5. 迁移建议

### 结论：**建议迁移，改动极小，风险极低**

**理由**：
1. MCP 规范官方已废弃 SSE，Claude Code CLI 官方文档有明确警告
2. FastMCP 仍支持 SSE，短期内不会移除，但长期维护重心在 Streamable HTTP
3. 代码改动量仅约 10 行，无架构变化
4. 大多数场景使用 `stdio`，完全不受影响

### 影响范围

| 组件 | 影响程度 | 说明 |
|------|---------|------|
| `gardener_mcp/server.py` | **低**（~5行）| 调整 transport 分支逻辑 |
| `config/settings.py` | **低**（docstring）| 更新字段说明，加入 `'http'` 选项 |
| `docs/deploy-local.md` | **低**（文档）| 更新 transport 示例 |
| `docs/deploy-cloud.md` | **低**（文档）| 更新 transport 示例 |
| Helm values.yaml | **低**（配置）| 更新 MCP_TRANSPORT 默认示例 |
| Claude Desktop / Claude Code 本地用户 | **零影响** | 使用 stdio，无关 HTTP 传输 |

---

## 6. 具体代码改动方案

### 改动 1：`gardener_mcp/server.py`（核心改动）

```python
# 改前（当前代码）
if transport == "sse":
    host = getattr(_settings, "mcp_host", "0.0.0.0")
    port = getattr(_settings, "mcp_port", 8080)
    logger.info("SSE server binding on %s:%s", host, port)
    mcp.run(transport="sse", host=host, port=port)
else:
    mcp.run(transport=transport)

# 改后（推荐）
if transport in ("sse", "http", "streamable-http"):
    host = getattr(_settings, "mcp_host", "0.0.0.0")
    port = getattr(_settings, "mcp_port", 8080)
    logger.info("HTTP server binding on %s:%s (transport=%s)", host, port, transport)
    mcp.run(transport=transport, host=host, port=port)
else:
    mcp.run(transport=transport)  # stdio 或自定义传输
```

### 改动 2：`config/settings.py`（docstring 更新）

```python
# 改前
description="MCP transport mechanism. 'stdio' for local/CLI use; 'sse' for HTTP+SSE server mode."

# 改后
description=(
    "MCP transport mechanism. "
    "'stdio' for local/CLI use (default). "
    "'http' for Streamable HTTP server mode (recommended for remote deployment). "
    "'sse' for legacy HTTP+SSE mode (deprecated per MCP spec 2025-03-26)."
)
```

### 改动 3：环境变量配置更新

```bash
# .env 或 Helm values 中
# 旧（已废弃）
GARDENER_MCP_TRANSPORT=sse

# 新（推荐）
GARDENER_MCP_TRANSPORT=http
```

---

## 7. 迁移风险评估

| 风险 | 级别 | 缓解措施 |
|------|------|---------|
| FastMCP API 变化 | **低** | `mcp.run(transport="http")` 已在 FastMCP 文档中确认 |
| 客户端不兼容 | **低** | Claude Code CLI 明确推荐 `--transport http`；MCP 规范提供向后兼容指导 |
| 部署中断 | **低** | 可在新配置验证后再切换；SSE 仍可作为回退方案 |
| stdio 用户影响 | **零** | 与 HTTP 传输完全独立 |

---

## 8. 行动项

### 立即可做（低风险）
- [ ] 修改 `gardener_mcp/server.py`（5 行）
- [ ] 更新 `config/settings.py` docstring

### 部署时更新
- [ ] 将 Docker/Helm 环境变量从 `sse` 改为 `http`
- [ ] 更新 `docs/deploy-cloud.md`、`docs/deploy-local.md` 中的 transport 示例

### 无需改动
- stdio 传输路径（本地开发 / Claude Code CLI 默认用法）
- 向量检索逻辑、embedding、Qdrant 连接——与传输层完全解耦

---

## 9. 参考资料

- [MCP 传输规范 (2025-03-26)](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports) — 官方 Streamable HTTP 定义，SSE 废弃说明
- [Claude Code MCP 文档](https://code.claude.com/docs/en/mcp) — SSE deprecated 警告，`--transport http` 推荐
- [FastMCP 运行服务器文档](https://gofastmcp.com/deployment/running-server) — `transport="http"` vs `"sse"` 用法
