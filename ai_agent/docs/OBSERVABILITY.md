# 查看 OTEL 执行链

架构、AI 场景里这条链和普通 APM 的差别，以及它怎么用来看回答质量，见 [OTLP-AI.md](OTLP-AI.md)。本文只写怎么在本机打开 Jaeger。

应用只把 span 发到 OTLP。界面在 kind 集群 `kind-gardener-ai-mcp` 的 `observability` 命名空间里。不在这个仓库里另起 Collector 或 Jaeger，也不改集群里已有的 Jaeger / Collector 清单。

- `svc/otel-collector`：OTLP HTTP `4318`。traces 已经导出到 `jaeger:4317`。
- `svc/jaeger`：`jaegertracing/all-in-one`，UI `16686`，内存存储。Pod 重启后 trace 消失。Collector 批处理大约 5 秒，界面里会晚几秒出现。

同命名空间的 Prometheus 不接收这次的 traces。

## 把集群端口转到本机

两个 Service 都是 ClusterIP。在 Mac 上先做 port-forward，并保持这两个进程不退出：

```bash
kubectl --context kind-gardener-ai-mcp -n observability port-forward svc/otel-collector 4318:4318
kubectl --context kind-gardener-ai-mcp -n observability port-forward svc/jaeger 16686:16686
```

确认：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:16686/
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:4318/v1/traces
```

第一条是 `200`。第二条是 `400` 或 `405`（GET 不是合法的 OTLP 请求，但说明 Collector 在听）。`4318` 或 `16686` 已被占用时，先腾出端口，不要改 Service。

## 让进程把 span 打进来

stdio 子进程不会自动继承 agent 的环境。agent 在拉起 gardener-ai-mcp 和 sci-ai-mcp 时，把 MCP SDK 的默认安全环境加上除 `OTEL_SERVICE_NAME` 以外的 `OTEL_*` 传进去。子进程因此打到同一个 Collector，并用 `traceparent` 接进同一条 trace。各自的 `service.name` 仍是 `gardener-ai-mcp` 或 `sci-ai-mcp`。

本机 `uv run`：

```bash
export OTEL_TRACES_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
export OTEL_UI_BASE_URL=http://127.0.0.1:16686
uv run agent-chat serve
```

`docker compose` 里的 `agent` 和 `agent-web` 已经打开这些变量，endpoint 是 `http://host.docker.internal:4318`（容器里的 `127.0.0.1` 到不了 kind）。导出失败只打日志，这次回答仍然返回，只是 Jaeger 里没有它。

不设 `OTEL_TRACES_EXPORTER`、也不设 `OTEL_EXPORTER_OTLP_ENDPOINT` 时，本机 SDK 关闭。没有转发、又要看 span 时，设 `OTEL_TRACES_EXPORTER=console`，span 打到进程 stderr，没有瀑布图。

`OTEL_AI_CAPTURE_CONTENT=true` 才放宽查询和结果预览。属性里不会出现 API key、token，也不会出现 embedding 向量。

## 从一次回答打开整条链

1. 在 Web UI 提一个会走工具的问题。这一轮结束后，回答下方给出 Jaeger 地址 `http://127.0.0.1:16686/trace/<trace_id>`。CLI 在答案后面打印同一行 `Jaeger:` URL。
2. 一条 trace 就是这一次提问。`agent-chat` 的 `invoke_agent` 是根；主 agent 和 `delegate_source` 子 agent 的 `chat` / `execute_tool` 都在这条 trace 里，子 agent 挂在对应的 `execute_tool delegate_source` 下面。`gardener-ai-mcp` 或 `sci-ai-mcp` 是子进程里的 SERVER span。看不到 MCP service 时，先确认 agent 侧的 `execute_tool` 还在，再查 `traceparent` 有没有传过去。
3. 瀑布图就是这次回答：`invoke_agent` → `chat` → `execute_tool` → `retrieve` → `embeddings` / `qdrant.search`，以及 `peer.service` 为 tavily、duckduckgo、openai、anthropic 的外部 span。点开 span 看 Tags。
4. 回答变差时，用两次的 `trace_id` 各开一个标签，或按 tag `gen_ai.conversation.id=<thread_id>` 把同一会话列出来。对照这四组 tag：`chat` 的 `gen_ai.request.model` 与 token；`execute_tool` 的工具名和结果预览；`retrieve` 的 collection、结果数、top 来源 id/score/url；外部 span 的 `peer.service`、HTTP 状态、错误。

Jaeger 的 Service 依赖图只能看出 agent 调了哪个 MCP。定位单次质量变化以这条 trace 的 tag 为准。
