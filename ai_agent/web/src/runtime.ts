import type {
  ChatModelAdapter,
  ChatModelRunResult,
  ThreadAssistantMessagePart,
  ThreadHistoryAdapter,
  ThreadMessage,
} from "@assistant-ui/react";
import { ExportedMessageRepository } from "@assistant-ui/react";

import { readSse, type SseFrame } from "./sse";
import { getStore, upsertMessage, type StoredMessage, type StoredPart } from "./sessions";

type MutableTool = {
  type: "tool-call";
  toolCallId: string;
  toolName: string;
  args: { source?: string };
  argsText: string;
  result?: string;
  parentId?: string;
  timing: { startedAt: number; completedAt?: number };
};

type MutableReasoning = {
  type: "reasoning";
  text: string;
  parentId?: string;
  providerMetadata: { activity: { startedAt: number; completedAt?: number } };
};

type MutableText = {
  type: "text";
  text: string;
  parentId?: string;
};

type MutablePart = MutableTool | MutableReasoning | MutableText;

function toolArgs(name: string, input: unknown): { source?: string } {
  if (name === "delegate_source" && input && typeof input === "object" && "source" in input) {
    const source = (input as { source?: unknown }).source;
    if (typeof source === "string") return { source };
  }
  return {};
}

class TurnBuilder {
  parts: MutablePart[] = [];
  traceId = "";
  traceUrl = "";
  error = "";

  private closeReasoning(parentId: string | undefined, now: number) {
    for (let i = this.parts.length - 1; i >= 0; i--) {
      const part = this.parts[i];
      if (part?.type !== "reasoning" || (part.parentId ?? "") !== (parentId ?? "")) continue;
      if (part.providerMetadata.activity.completedAt === undefined) {
        part.providerMetadata.activity.completedAt = now;
      }
      return;
    }
  }

  apply(frame: SseFrame, now = Date.now()) {
    const parentId = frame.parent_id;
    if (frame.type === "reasoning" && frame.text) {
      const last = this.parts[this.parts.length - 1];
      if (
        last?.type === "reasoning" &&
        (last.parentId ?? "") === (parentId ?? "") &&
        last.providerMetadata.activity.completedAt === undefined
      ) {
        last.text += frame.text;
      } else {
        this.parts.push({
          type: "reasoning",
          text: frame.text,
          ...(parentId ? { parentId } : {}),
          providerMetadata: { activity: { startedAt: now } },
        });
      }
      return;
    }
    if (frame.type === "tool_start" && frame.id) {
      this.closeReasoning(parentId, now);
      this.parts.push({
        type: "tool-call",
        toolCallId: frame.id,
        toolName: frame.name || "tool",
        args: toolArgs(frame.name || "", frame.input),
        argsText: JSON.stringify(frame.input ?? {}, null, 2),
        ...(parentId ? { parentId } : {}),
        timing: { startedAt: now },
      });
      return;
    }
    if (frame.type === "tool_end" && frame.id) {
      const tool = this.parts.find(
        (part): part is MutableTool => part.type === "tool-call" && part.toolCallId === frame.id,
      );
      if (tool) {
        tool.result = frame.output ?? "";
        tool.timing.completedAt = now;
      }
      return;
    }
    if (frame.type === "token" && frame.text) {
      if (!parentId) this.closeReasoning(undefined, now);
      const tail = this.parts[this.parts.length - 1];
      if (tail?.type === "text" && (tail.parentId ?? "") === (parentId ?? "")) {
        tail.text += frame.text;
      } else {
        this.parts.push({
          type: "text",
          text: frame.text,
          ...(parentId ? { parentId } : {}),
        });
      }
      return;
    }
    if (frame.type === "done") {
      this.traceId = frame.trace_id ?? "";
      this.traceUrl = frame.trace_url ?? "";
      this.closeReasoning(undefined, now);
      return;
    }
    if (frame.type === "error") this.error = frame.message ?? "request failed";
  }

  result(final: boolean): ChatModelRunResult {
    const content = this.parts.map((part) => ({ ...part })) as ThreadAssistantMessagePart[];
    const custom: Record<string, unknown> = {};
    if (this.traceId) custom.traceId = this.traceId;
    if (this.traceUrl) custom.traceUrl = this.traceUrl;
    if (this.error) {
      return {
        content,
        status: { type: "incomplete", reason: "error", error: this.error },
        metadata: { custom },
      };
    }
    if (final && !content.some((part) => part.type === "text" && !part.parentId)) {
      content.push({ type: "text", text: "(no textual output)" });
    }
    return { content, metadata: { custom } };
  }
}

function messageText(message: ThreadMessage) {
  return message.content
    .filter((part) => part.type === "text" && !("parentId" in part && part.parentId))
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("");
}

export function createChatModel(threadId: string): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const last = messages[messages.length - 1];
      if (!last) return;
      const session = getStore().sessions[threadId];
      const response = await fetch("/api/chat", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          config: session?.config || "generic_react",
          query: messageText(last),
          thread_id: threadId,
        }),
        signal: abortSignal,
      });
      if (!response.ok || !response.body) {
        yield {
          content: [],
          status: { type: "incomplete", reason: "error", error: `HTTP ${response.status}` },
        };
        return;
      }
      const turn = new TurnBuilder();
      for await (const frame of readSse(response.body)) {
        if (abortSignal.aborted) return;
        turn.apply(frame);
        yield turn.result(false);
      }
      yield turn.result(true);
    },
  };
}

function storedParts(message: ThreadMessage): StoredPart[] {
  if (message.role !== "assistant") return [];
  const parts: StoredPart[] = [];
  for (const part of message.content) {
    if (part.type === "reasoning") {
      const activity = part.providerMetadata?.activity;
      const startedAt = typeof activity?.startedAt === "number" ? activity.startedAt : undefined;
      if (startedAt === undefined) continue;
      parts.push({
        type: "reasoning",
        ...(part.parentId ? { parentId: part.parentId } : {}),
        startedAt,
        ...(typeof activity?.completedAt === "number" ? { completedAt: activity.completedAt } : {}),
      });
    } else if (part.type === "tool-call") {
      parts.push({
        type: "tool-call",
        toolCallId: part.toolCallId,
        toolName: part.toolName,
        ...(part.parentId ? { parentId: part.parentId } : {}),
        ...(typeof part.args.source === "string" ? { source: part.args.source } : {}),
        startedAt: part.timing?.startedAt ?? Date.now(),
        ...(part.timing?.completedAt !== undefined ? { completedAt: part.timing.completedAt } : {}),
      });
    }
  }
  return parts;
}

function toStored(message: ThreadMessage): StoredMessage {
  const custom = message.role === "assistant" ? message.metadata?.custom : undefined;
  return {
    id: message.id,
    role: message.role === "user" ? "user" : "assistant",
    text: messageText(message),
    parts: storedParts(message),
    ...(typeof custom?.traceId === "string" ? { traceId: custom.traceId } : {}),
    ...(typeof custom?.traceUrl === "string" ? { traceUrl: custom.traceUrl } : {}),
  };
}

function toLike(message: StoredMessage) {
  if (message.role === "user") {
    return { id: message.id, role: "user" as const, content: message.text };
  }
  const content: ThreadAssistantMessagePart[] = message.parts.map((part) => {
    if (part.type === "reasoning") {
      return {
        type: "reasoning" as const,
        text: "",
        ...(part.parentId ? { parentId: part.parentId } : {}),
        providerMetadata: {
          activity: {
            startedAt: part.startedAt,
            ...(part.completedAt !== undefined ? { completedAt: part.completedAt } : {}),
            retained: false,
          },
        },
      };
    }
    const args: Record<string, string> = {};
    if (part.source) args.source = part.source;
    return {
      type: "tool-call" as const,
      toolCallId: part.toolCallId,
      toolName: part.toolName,
      args,
      argsText: "{}",
      ...(part.parentId ? { parentId: part.parentId } : {}),
      timing: {
        startedAt: part.startedAt,
        ...(part.completedAt !== undefined ? { completedAt: part.completedAt } : {}),
      },
    };
  });
  if (message.text) content.push({ type: "text", text: message.text });
  return {
    id: message.id,
    role: "assistant" as const,
    content,
    ...(message.traceId || message.traceUrl
      ? { metadata: { custom: { traceId: message.traceId, traceUrl: message.traceUrl } } }
      : {}),
  };
}

export function createHistory(threadId: string): ThreadHistoryAdapter {
  return {
    async load() {
      const session = getStore().sessions[threadId];
      return ExportedMessageRepository.fromArray((session?.messages ?? []).map(toLike));
    },
    async append({ message }) {
      if (message.role !== "user" && message.role !== "assistant") return;
      upsertMessage(threadId, toStored(message));
    },
  };
}
