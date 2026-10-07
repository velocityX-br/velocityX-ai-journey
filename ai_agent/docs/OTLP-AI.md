# 把 OTLP 用在 AI 回答链上

一次提问对应一条 trace。它要能回答的不是“服务活着吗”，而是“这次回答为什么是这样”：模型选了哪条工具、检索命中了哪份文档、外部系统有没有失败，以及模型有没有把没发生的调用写进答案。

本文讲这套做法的架构、观测方式和它在 AI 上的增益。打开 Jaeger、转发端口的步骤见 [OBSERVABILITY.md](OBSERVABILITY.md)。

## 传统链路不够用的地方

HTTP 服务的 trace 通常是入口 span 下面挂数据库和下游 RPC。延迟、错误率和依赖图就够用。AI 回答不是这种形状。

一次 `sap-ops-assistant` 的回答会穿过多个进程：agent 里的 ReAct 循环、stdio 拉起的 `gardener-ai-mcp` 或 `sci-ai-mcp`、Qdrant、embedding 接口，以及 Tavily、DuckDuckGo 或根因分析里的 Anthropic 调用。同样的问题，下一次可能换工具、换集合、换模型。回答变差时，服务依赖图只能看出“agent 调过哪个 MCP”，看不出检索分数、命中文档和 token。

所以观测对象从请求改成**一次回答的执行链**。OTLP 仍然只是传输协议。应用不绑定 Jaeger，也不在仓库里另起 Collector。

## 架构：应用只说 OTLP

```text
agent-chat ──OTLP HTTP :4318──▶ otel-collector ──OTLP gRPC──▶ Jaeger
gardener-ai-mcp ─┘                                      （kind / observability）
sci-ai-mcp ──────┘
```

进程里的 SDK 在 `OTEL_TRACES_EXPORTER=otlp` 或设置了 `OTEL_EXPORTER_OTLP_ENDPOINT` 时才导出，协议是 HTTP protobuf。不设置时 `configure()` 是空操作，本地开发不受影响。导出失败只记日志，这次回答照常返回。

查看面用的是 kind 集群 `observability` 命名空间里已经在跑的 Collector 和 `jaegertracing/all-in-one`。Collector 把 traces 转到 `jaeger:4317`。同命名空间的 Prometheus 不接收这些 span。Jaeger 是内存存储，Pod 重启后 trace 消失。两个 Service 都是 ClusterIP，本机用 port-forward 看 UI。

三个进程共用 `packages/ai-observability`，避免 agent 和两个 RAG 服务各写一套 trace 上下文。`service.name` 由各自的 `configure()` 决定：`agent-chat`、`gardener-ai-mcp`、`sci-ai-mcp`。父进程的 `OTEL_SERVICE_NAME` 不会传给子进程，否则 Jaeger 里三个角色会叠成同一个服务。

### 跨进程的那一跳

MCP 的 stdio 传输不是 HTTP。Python MCP SDK 拉起子进程时，默认环境只有 `HOME`、`PATH`、`USER` 这一小撮，`OTEL_*` 会被丢掉。agent 因此在连接参数里显式带上默认安全环境，再加上除 `OTEL_SERVICE_NAME` 以外的 `OTEL_*`。子进程才能把 span 打到同一个 Collector。

上下文也不能靠 HTTP 头。`langchain-mcp-adapters` 不把 `meta` 交给 `ClientSession.call_tool`。agent 在启用遥测时包一层 `call_tool`，把 W3C `traceparent` / `tracestate` 放进 MCP 的 `_meta`。服务端的 FastMCP middleware 从这里取出父 span，再开自己的 SERVER span。HTTP / SSE 传输另外在请求头里放同一份 `traceparent`。

工具名在模型侧是 Claude Code 的形式，`mcp__sci-ai-mcp__search_docs`。发给 MCP 服务器的仍是短名 `search_docs`。这样 gardener 和 sci 都有 `search_docs` 时，trace 上的 `mcp.server.name` 和工具名能对上，不会互相覆盖。

离线入库脚本不在这条在线链上。它们重刷向量库，不参与某一次回答。

## 一条回答在瀑布图里的形状

根 span 是 `invoke_agent`。`gen_ai.conversation.id` 就是这次会话的 `thread_id`。一次提问只有这一条 trace：主 agent 的 `chat` 和 `execute_tool`，以及 `delegate_source` 拉起的子 agent，都挂在同一个 `trace_id` 上。子 agent 的模型调用和工具调用是 `execute_tool delegate_source` 的子 span（`ai.agent.role=subagent`，`ai.delegate.source` 是那一路数据源）。上下文在子 agent 的异步任务里丢了时，span 仍然回到这条 `invoke_agent`，不会另开一条根 trace。它下面按实际发生的步骤展开：

| Span | 所在进程 | 用来看什么 |
|---|---|---|
| `chat {model}` | agent，或 MCP 里的根因分析 | 模型、输入/输出 token、结束原因、提示与回答预览 |
| `execute_tool {name}` | agent，以及 MCP 上的 SERVER span | 工具名、参数预览、结果预览、`mcp.server.name` |
| `retrieve` / `retrieve hybrid` | gardener 或 sci | 集合、结果数、top 来源的 id / score / url |
| `embeddings` | 同上 | 一次查询向量化。向量本身不进属性 |
| `qdrant.search` | 同上 | `db.system=qdrant` 和集合名 |
| `external tavily` / `duckduckgo` / `openai` / `anthropic` | 调用方进程 | `peer.service`、HTTP 状态、错误 |

每一轮回答结束时，Web UI 和 CLI 都给出 Jaeger 地址，默认是 `http://127.0.0.1:16686/trace/{id}`。`OTEL_UI_BASE_URL` 可以换成别的 Jaeger；设成空字符串则不给链接。

属性默认截断到大约 2KB。`OTEL_AI_CAPTURE_CONTENT=true` 才放到大约 8KB。`api_key`、`token`、`authorization`、`password`、`secret` 会打成 `[REDACTED]`。`input_tokens` 保留，它不是密钥。检索 span 只留来源的 id、分数、集合和 url，不留原文，也不留 embedding 向量。

LangSmith 继续按它自己的环境变量工作。OTLP 是另一条导出，不替换它。

## 观测性上实际在看什么

把两次回答的 `trace_id` 并排打开，或者在 Jaeger 里按 `gen_ai.conversation.id` 列出同一会话。对照四组 tag：

1. **模型。** `gen_ai.request.model` 和 token。回答变长、变贵或中途截断，先看这里。
2. **工具。** `execute_tool` 的名字和结果预览。模型在正文里写“已调用 sci-ai-mcp”，但 span 上是 `mcp__gardener-ai-mcp__rag_retrieve`，就说明它换了工具。
3. **检索。** collection、结果数、top 来源的 id 和 score。同一问题两次分数差很多，质量变化在检索，不在措辞。
4. **外部系统。** `peer.service`、HTTP 状态、错误。embedding 或搜索失败时，后面的空检索是结果，不是原因。

Service 依赖图只回答“调了哪个进程”。质量问题以这条 trace 的 tag 为准。

## 用在 AI 上时，和普通服务不一样的地方

**一次用户意图，多段非确定步骤。** 传统请求的下游在代码里写死。这里的下游由模型当场决定。trace 记录的是这条决策实际走出来的树，而不是配置里注册过的全部工具。没被调用的 MCP 不会出现。被调用但内部没接 OTEL 的服务，agent 侧仍有一条 `execute_tool`（名字、参数、结果预览），它内部的检索和 HTTP 不会成为子 span。

**协议边界不是 HTTP。** 上下文要穿过 MCP 的 `_meta`，环境变量还要绕开 stdio 的安全子集。少了任何一跳，Jaeger 里就只有 `agent-chat`，RAG 和 embedding 像没发生过。这是 AI 工具链比普通微服务更容易断链的地方。

**载荷又大又敏感。** 提示、工具结果和文档块不适合整段进 span。向量更不该进。观测要的是“打到了哪份文档、分数多少”，不是把语料再复制一份到追踪系统。截断和脱敏是这条链的一部分，不是事后补的合规。

**失败不能吃掉回答。** 用户要的是答案。Collector 不可达时，span 可以丢，请求不能 500。因此默认关闭，导出与业务解耦。代价是“Jaeger 里没有”有时只是没开导出或端口没转发，要和“代码没打到 span”分开看。

**质量是一等信号。** 延迟和错误率仍然有用，尤其是 embedding 和 Qdrant。但 AI 上更常见的故障是：回答看起来完整，工具却用错了，或者检索命中了不相关的块。trace 把这种失败留在可对照的 tag 里。比较两次 `trace_id` 就是质量回归的办法，不需要再做一套 diff 产品。

**并行工具调用会留下半截历史。** 模型可以在一条消息里发出多个 `tool_use`。浏览器断开或请求被取消时，助手消息已经进了会话，工具结果还没写上。下一次请求如果原样送给模型，Anthropic 会返回 400：`tool_use` 后面没有对应的 `tool_result`。调用模型之前会把这种中断补成错误结果，让同一条 `thread_id` 还能继续。这也是 AI 会话状态和普通无状态 RPC 的差别：trace 断了，对话状态也可能断。

## 增益

接到这套链上之后，排障从“看模型最终输出”变成“看它走过的步骤”。

点名 `sci-ai-mcp` 时，若 Jaeger 里只有 `skill_sci_multi_source_research` 和 gardener 的 `rag_retrieve`，就能确定技能只返回了说明文字，sci 进程根本没启动。这和模型在答案里声称已经查过 SCI 文档是两回事。工具名改成 `mcp__服务器__工具` 之后，同名的 `search_docs` 也不会再被另一个服务盖掉。

检索变差时，不用重放整段对话。`retrieve` 上的集合、条数和 top 来源 id 足够判断是问错了库，还是问对了库但分数低。外部 span 则把“模型胡说”和“上游 500”分开。

同一会话用 `gen_ai.conversation.id` 串起来，可以看后面几轮是不是还在用上一轮的工具结果，还是模型换了一条路。token 和模型名留在 `chat` 上，成本和质量可以对着同一次回答看。

## 边界

当前接进在线回答链的是 agent、`gardener-ai-mcp` 和 `sci-ai-mcp`。sap-wiki 等没有注册的服务，技能里点到它们时应当说明未接入。agent 如果将来去调一个没接 middleware 的 MCP，执行链在 agent 的 `execute_tool` 处截止。

Collector 大约 5 秒才把一批 span 交给 Jaeger。刚返回的 `trace_id` 可能要等几秒才打得开。
