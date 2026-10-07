import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  unstable_useComposerInput,
  useAuiState,
  useLocalRuntime,
} from "@assistant-ui/react";
import { useMemo, useState } from "react";

import { ActivityTimeline, Answer, TraceLink } from "./ActivityTimeline";
import { createChatModel, createHistory } from "./runtime";

export type SkillInfo = {
  name: string;
  description: string;
};

const SKILLS_COMMAND: SkillInfo = {
  name: "skills",
  description: "列出当前配置已加载的 skill",
};

function UserMessage() {
  const text = useAuiState((state) =>
    state.message.parts
      .filter((part) => part.type === "text")
      .map((part) => (part.type === "text" ? part.text : ""))
      .join(""),
  );
  return (
    <MessagePrimitive.Root className="user-row">
      <div className="user-bubble">{text}</div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  const error = useAuiState((state) => {
    const status = state.message.status;
    return status?.type === "incomplete" && "error" in status ? status.error : undefined;
  });
  return (
    <MessagePrimitive.Root className="assistant-row">
      <ActivityTimeline />
      <Answer />
      {error !== undefined ? <div className="err">{String(error)}</div> : null}
      <TraceLink />
    </MessagePrimitive.Root>
  );
}

function Composer({ skills }: { skills: SkillInfo[] }) {
  const running = useAuiState((state) => state.thread.isRunning);
  const { value, setText } = unstable_useComposerInput();
  const [picked, setPicked] = useState(0);
  const token = slashToken(value);
  const matches =
    token === null
      ? []
      : [SKILLS_COMMAND, ...skills].filter((skill) => skill.name.toLowerCase().startsWith(token.toLowerCase()));
  const active = matches.length ? Math.min(picked, matches.length - 1) : 0;

  return (
    <ComposerPrimitive.Root className="composer">
      {matches.length > 0 ? (
        <div className="slash-menu" role="listbox">
          {matches.map((skill, index) => (
            <button
              key={skill.name}
              type="button"
              role="option"
              aria-selected={index === active}
              className={index === active ? "slash-item active" : "slash-item"}
              onMouseDown={(event) => {
                event.preventDefault();
                setText(`/${skill.name} `);
                setPicked(0);
              }}
            >
              <span className="slash-name">/{skill.name}</span>
              <span className="slash-desc">{skill.description}</span>
            </button>
          ))}
        </div>
      ) : null}
      <ComposerPrimitive.Input
        placeholder="Ask, or type /skills to list skills…  (Enter to send)"
        rows={1}
        onKeyDown={(event) => {
          if (!matches.length) return;
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setPicked((index) => (index + 1) % matches.length);
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setPicked((index) => (index - 1 + matches.length) % matches.length);
          } else if (event.key === "Tab") {
            event.preventDefault();
            const skill = matches[active];
            if (skill) setText(`/${skill.name} `);
            setPicked(0);
          } else if (event.key === "Enter" && !event.shiftKey) {
            const exact = matches.some((skill) => skill.name.toLowerCase() === (token ?? "").toLowerCase());
            if (!exact) {
              event.preventDefault();
              const skill = matches[active];
              if (skill) setText(`/${skill.name} `);
              setPicked(0);
            }
          }
        }}
      />
      {running ? (
        <ComposerPrimitive.Cancel className="send">Stop</ComposerPrimitive.Cancel>
      ) : (
        <ComposerPrimitive.Send className="send">Send</ComposerPrimitive.Send>
      )}
    </ComposerPrimitive.Root>
  );
}

function slashToken(value: string) {
  if (!value.startsWith("/") || value.includes(" ") || value.includes("\n")) return null;
  return value.slice(1);
}

export function ChatPane({ threadId, skills }: { threadId: string; skills: SkillInfo[] }) {
  const chatModel = useMemo(() => createChatModel(threadId), [threadId]);
  const history = useMemo(() => createHistory(threadId), [threadId]);
  const runtime = useLocalRuntime(chatModel, { adapters: { history } });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="thread">
        <ThreadPrimitive.Viewport className="viewport">
          <ThreadPrimitive.Empty>
            <div className="empty">
              Ask a question, or type /skills. /sap-jira, /kb-capture, and /sap-authentication call those skills when this
              config has them loaded.
            </div>
          </ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
        </ThreadPrimitive.Viewport>
        <Composer skills={skills} />
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
