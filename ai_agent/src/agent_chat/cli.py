"""Typer CLI — the concrete, runnable entrypoint.

    agent-chat run --config sap_ops --query "How do I debug a shoot stuck reconciling?"

Builds the real graph (real LLM + real MCP tools) and streams the answer.
"""

from __future__ import annotations

import asyncio
import uuid

import typer
from ai_observability import current_trace_id, preview, span, trace_url
from rich.console import Console
from rich.panel import Panel

from agent_chat.config import load_config
from agent_chat.graph import build_graph
from agent_chat.memory import build_checkpointer
from agent_chat.observability import configure_observability
from agent_chat.otel_callback import OtelCallbackHandler

app = typer.Typer(
    add_completion=False,
    help="Config-driven ReAct chat runtime (LangGraph + MCP). A YAML file changes the prompt, model, and tools.",
)
console = Console()


@app.command()
def run(
    query: str = typer.Option(..., "--query", "-q", help="The question to ask the agent."),
    config: str = typer.Option("generic_react", "--config", "-c", help="Config name under configs/ (no .yaml)."),
    thread_id: str = typer.Option(None, "--thread-id", "-t", help="Conversation id for memory/resume."),
) -> None:
    """Run the agent for a single query and print the answer."""
    configure_observability()
    cfg = load_config(config)
    thread = thread_id or f"cli-{uuid.uuid4().hex[:8]}"

    console.print(
        Panel(
            f"[bold]{cfg.name}[/bold]\n"
            f"provider: {cfg.llm.provider} · model: {cfg.llm.model}\n"
            f"tools: {', '.join(cfg.mcp_servers) or '(none)'} · thread: {thread}",
            title="agent",
            border_style="cyan",
        )
    )

    answer, trace_id = asyncio.run(_run_once(cfg, query, thread))
    console.print(Panel(answer, title="answer", border_style="green"))
    if trace_id:
        url = trace_url(trace_id)
        console.print(f"Jaeger: {url}" if url else f"trace_id: {trace_id}")


@app.command()
def show(config: str = typer.Argument("generic_react", help="Config name to inspect.")) -> None:
    """Print the fully-resolved config (base + overrides + env)."""
    cfg = load_config(config)
    console.print_json(cfg.model_dump_json(indent=2))


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev)."),
) -> None:
    """Serve the Web UI (FastAPI + SSE) over the same graph as ``run``."""
    import uvicorn

    console.print(
        Panel(
            f"Web UI on [bold]http://{host}:{port}[/bold]\nAPI: GET /api/configs · POST /api/chat (SSE)",
            title="agent serve",
            border_style="cyan",
        )
    )
    uvicorn.run("agent_chat.api:app", host=host, port=port, reload=reload)


async def _run_once(cfg, query: str, thread: str) -> tuple[str, str]:
    """Compile the graph inside the checkpointer's lifetime and invoke it."""
    with build_checkpointer(cfg.memory) as saver:
        graph = await build_graph(cfg, checkpointer=saver)
        async with span(
            "invoke_agent",
            **{
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.conversation.id": thread,
                "gen_ai.request.model": cfg.llm.model,
                "ai.agent.config": cfg.name,
                "ai.query": preview(query),
            },
        ) as current:
            result = await graph.ainvoke(
                {"messages": [{"role": "user", "content": query}]},
                config={
                    "configurable": {"thread_id": thread},
                    "recursion_limit": cfg.recursion_limit,
                    "callbacks": [OtelCallbackHandler()],
                },
            )
            messages = result.get("messages", [])
            if not messages:
                answer = "(no response)"
            else:
                answer = getattr(messages[-1], "content", str(messages[-1]))
            if not isinstance(answer, str):
                answer = preview(answer)
            if current.is_recording():
                current.set_attribute("ai.answer", preview(answer))
            return answer, current_trace_id()


if __name__ == "__main__":  # pragma: no cover
    app()
