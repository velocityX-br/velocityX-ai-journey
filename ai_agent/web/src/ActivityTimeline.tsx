import { useAuiState } from "@assistant-ui/react";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Kind = "skill" | "mcp" | "web" | "subagent" | "tool" | "reasoning";

function kindOf(name: string): Kind {
  if (name === "delegate_source") return "subagent";
  if (name.startsWith("skill_")) return "skill";
  if (name.startsWith("mcp__")) return "mcp";
  if (name === "web_search") return "web";
  return "tool";
}

function labelOf(name: string, source?: string) {
  if (name === "delegate_source") return source || "sub-agent";
  if (name.startsWith("mcp__")) {
    const [, server, tool] = name.split("__");
    return server && tool ? `${server} · ${tool}` : name;
  }
  if (name.startsWith("skill_")) return name.slice("skill_".length);
  return name;
}

function formatMs(ms: number) {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function activityTimes(part: {
  providerMetadata?: { activity?: { startedAt?: unknown; completedAt?: unknown; retained?: unknown } };
}) {
  const activity = part.providerMetadata?.activity;
  const startedAt = typeof activity?.startedAt === "number" ? activity.startedAt : undefined;
  const completedAt = typeof activity?.completedAt === "number" ? activity.completedAt : undefined;
  const retained = activity?.retained !== false;
  return { startedAt, completedAt, retained };
}

export function ActivityTimeline() {
  const parts = useAuiState((state) => state.message.parts);
  const running = useAuiState((state) => state.message.status?.type === "running");
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!running) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  const top = parts.filter(
    (part) =>
      !("parentId" in part && part.parentId) && (part.type === "reasoning" || part.type === "tool-call"),
  );
  if (top.length === 0) return null;

  const started = top.reduce((min, part) => {
    const at =
      part.type === "tool-call"
        ? part.timing?.startedAt
        : part.type === "reasoning"
          ? activityTimes(part).startedAt
          : undefined;
    return at !== undefined && at < min ? at : min;
  }, Number.POSITIVE_INFINITY);
  const elapsed = Number.isFinite(started) ? formatMs((running ? now : lastEnd(parts, now)) - started) : "";
  const skills = top.filter((part) => part.type === "tool-call" && kindOf(part.toolName) === "skill").length;
  const subs = top.filter((part) => part.type === "tool-call" && kindOf(part.toolName) === "subagent").length;
  const counts = [
    skills ? `${skills} skill${skills > 1 ? "s" : ""}` : "",
    subs ? `${subs} sub-agent${subs > 1 ? "s" : ""}` : "",
  ].filter(Boolean);

  return (
    <details className="thinking">
      <summary>
        <b>Thinking</b>
        {elapsed ? <span className="time">{elapsed}</span> : null}
        {running ? <span className="pulse">working</span> : null}
        {counts.length ? <span className="count">{counts.join(" · ")}</span> : null}
      </summary>
      <div className="steps">
        {top.map((part, index) =>
          part.type === "reasoning" ? (
            <ReasoningBlock key={`r-${index}`} text={part.text} retained={activityTimes(part).retained} />
          ) : part.type === "tool-call" ? (
            <ToolBlock key={part.toolCallId} part={part} parts={parts} now={now} running={running} />
          ) : null,
        )}
      </div>
    </details>
  );
}

function lastEnd(
  parts: readonly { type: string; parentId?: string; timing?: { completedAt?: number }; providerMetadata?: { activity?: { completedAt?: unknown } } }[],
  now: number,
) {
  let end = 0;
  for (const part of parts) {
    if (part.parentId) continue;
    const at =
      part.type === "tool-call"
        ? part.timing?.completedAt
        : typeof part.providerMetadata?.activity?.completedAt === "number"
          ? part.providerMetadata.activity.completedAt
          : undefined;
    if (at && at > end) end = at;
  }
  return end || now;
}

function ReasoningBlock({ text, retained }: { text: string; retained: boolean }) {
  return (
    <details className="step reason">
      <summary>
        <span className="chip">Think</span>
        <span>Reasoning</span>
      </summary>
      {retained && text ? <pre>{text}</pre> : <p className="muted">Kept for this turn only.</p>}
    </details>
  );
}

function ToolBlock({
  part,
  parts,
  now,
  running,
}: {
  part: {
    toolCallId: string;
    toolName: string;
    args: { source?: unknown };
    argsText: string;
    result?: unknown;
    timing?: { startedAt: number; completedAt?: number };
  };
  parts: readonly {
    type: string;
    parentId?: string;
    text?: string;
    toolCallId?: string;
    toolName?: string;
    argsText?: string;
    result?: unknown;
    timing?: { startedAt: number; completedAt?: number };
    providerMetadata?: { activity?: { retained?: unknown } };
  }[];
  now: number;
  running: boolean;
}) {
  const kind = kindOf(part.toolName);
  const source = typeof part.args.source === "string" ? part.args.source : undefined;
  const done = part.timing?.completedAt !== undefined;
  const elapsed = part.timing
    ? formatMs((part.timing.completedAt ?? (running ? now : part.timing.startedAt)) - part.timing.startedAt)
    : "";
  const children = parts.filter((item) => item.parentId === part.toolCallId);
  const kept = part.argsText !== "{}";

  return (
    <details className={`step ${kind}`}>
      <summary>
        <span className={`chip ${kind}`}>{chipLabel(kind)}</span>
        <span>{labelOf(part.toolName, source)}</span>
        {elapsed ? <span className="time">{elapsed}</span> : null}
        <span className="state">{done ? "done" : "running"}</span>
      </summary>
      <div className="body">
        {kind === "subagent"
          ? children.map((child) =>
              child.type === "reasoning" ? (
                <ReasoningBlock
                  key={`${part.toolCallId}-r`}
                  text={child.text ?? ""}
                  retained={child.providerMetadata?.activity?.retained !== false}
                />
              ) : child.type === "text" ? (
                <pre key={`${part.toolCallId}-d`} className="draft">
                  {child.text}
                </pre>
              ) : child.type === "tool-call" && child.toolCallId && child.toolName ? (
                <ToolBlock
                  key={child.toolCallId}
                  part={{
                    toolCallId: child.toolCallId,
                    toolName: child.toolName,
                    args: {},
                    argsText: child.argsText ?? "{}",
                    result: child.result,
                    timing: child.timing,
                  }}
                  parts={parts}
                  now={now}
                  running={running}
                />
              ) : null,
            )
          : null}
        {kept ? (
          <>
            <div className="label">input</div>
            <pre>{part.argsText}</pre>
          </>
        ) : (
          <p className="muted">Input and output stay on the live turn.</p>
        )}
        {part.result !== undefined ? (
          <>
            <div className="label">{kind === "subagent" ? "summary" : "output"}</div>
            <pre>{typeof part.result === "string" ? part.result : JSON.stringify(part.result, null, 2)}</pre>
          </>
        ) : null}
      </div>
    </details>
  );
}

function chipLabel(kind: Kind) {
  if (kind === "subagent") return "Sub-agent";
  if (kind === "skill") return "Skill";
  if (kind === "mcp") return "MCP";
  if (kind === "web") return "Web";
  return "Tool";
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.left = "-9999px";
    document.body.appendChild(area);
    area.select();
    try {
      return document.execCommand("copy");
    } finally {
      document.body.removeChild(area);
    }
  }
}

export function Answer() {
  const text = useAuiState((state) =>
    state.message.parts
      .filter((part) => part.type === "text" && !part.parentId)
      .map((part) => (part.type === "text" ? part.text : ""))
      .join(""),
  );
  const running = useAuiState((state) => state.message.status?.type === "running");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return undefined;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  if (!text) return null;
  return (
    <div className="answer-wrap">
      <div className="answer md">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </div>
      {!running ? (
        <div className="answer-actions">
          <button
            type="button"
            className="copy-md"
            onClick={async () => {
              if (await copyText(text)) setCopied(true);
            }}
          >
            {copied ? "Copied" : "Copy Markdown"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function TraceLink() {
  const traceId = useAuiState((state) => state.message.metadata?.custom?.traceId);
  const traceUrl = useAuiState((state) => state.message.metadata?.custom?.traceUrl);
  const href = typeof traceUrl === "string" ? traceUrl : "";
  if (!href && (typeof traceId !== "string" || !traceId)) return null;
  return (
    <div className="trace">
      {href ? (
        <>
          Jaeger{" "}
          <a href={href} target="_blank" rel="noreferrer">
            {href}
          </a>
        </>
      ) : (
        <span>trace {String(traceId)}</span>
      )}
    </div>
  );
}
