import { useEffect, useState, useSyncExternalStore } from "react";

import { ChatPane, type SkillInfo } from "./ChatPane";
import {
  createSession,
  deleteSession,
  getStore,
  loadStore,
  renameSession,
  setActive,
  setSessionConfig,
  subscribe,
} from "./sessions";

type ConfigItem = {
  name: string;
  display_name: string;
  provider: string;
  model: string;
  tools: string[];
  skills: SkillInfo[];
};

function relTime(ts: number) {
  const delta = Math.max(0, Date.now() - ts);
  const minute = 60000;
  const hour = 3600000;
  const day = 86400000;
  if (delta < minute) return "just now";
  if (delta < hour) return `${Math.floor(delta / minute)}m ago`;
  if (delta < day) return `${Math.floor(delta / hour)}h ago`;
  return `${Math.floor(delta / day)}d ago`;
}

export function App() {
  const snapshot = useSyncExternalStore(subscribe, getStore);
  const [configs, setConfigs] = useState<ConfigItem[]>([]);
  const [open, setOpen] = useState(false);
  const active = snapshot.activeId ? snapshot.sessions[snapshot.activeId] : undefined;

  useEffect(() => {
    loadStore();
    if (!getStore().activeId) createSession("");
    // BFF login gate: check the session first. On 401 the server (when auth is
    // enabled) has no logged-in user, so bounce to the server-side OIDC login.
    // When auth is disabled /auth/me isn't mounted (404) — treat that as open.
    //
    // Guard against an OIDC state-clobber loop: /auth/login stores a CSRF
    // `state` in the session, so firing it more than once overwrites the
    // pending value and makes the eventual callback fail with mismatching_state.
    // We only ever redirect ONCE per page life (sessionStorage flag), and never
    // while sitting on the callback path (the callback is a server redirect).
    const loadConfigs = () =>
      fetch("/api/configs", { credentials: "include" })
        .then((response) => response.json())
        .then((items: ConfigItem[]) => {
          setConfigs(items);
          const current = getStore();
          const session = current.activeId ? current.sessions[current.activeId] : undefined;
          if (session && !session.config && items[0]) setSessionConfig(session.id, items[0].name);
        })
        .catch(() => setConfigs([]));

    void fetch("/auth/me", { credentials: "include" })
      .then((response) => {
        if (response.status === 401) {
          // Don't loop: if we already sent the browser to IAS in this tab, wait.
          if (sessionStorage.getItem("auth-redirecting") === "1") return;
          sessionStorage.setItem("auth-redirecting", "1");
          window.location.href = "/auth/login";
          return;
        }
        // Logged in (or auth disabled). Clear the one-shot guard and load.
        sessionStorage.removeItem("auth-redirecting");
        void loadConfigs();
      })
      .catch(() => setConfigs([]));
  }, []);

  return (
    <div className={open ? "app sidebar-open" : "app"}>
      <aside className="sidebar">
        <div className="side-top">
          <button
            type="button"
            className="new-chat"
            onClick={() => {
              createSession(active?.config || configs[0]?.name || "");
              setOpen(false);
            }}
          >
            + New chat
          </button>
        </div>
        <div className="sessions">
          {snapshot.order.map((id) => {
            const session = snapshot.sessions[id];
            if (!session) return null;
            return (
              <SessionRow
                key={id}
                title={session.title || "New chat"}
                time={relTime(session.updatedAt)}
                active={id === snapshot.activeId}
                onOpen={() => {
                  setActive(id);
                  setOpen(false);
                }}
                onRename={(title) => renameSession(id, title)}
                onDelete={() => {
                  deleteSession(id);
                  if (!getStore().activeId) createSession(configs[0]?.name || "");
                }}
              />
            );
          })}
        </div>
      </aside>
      <main>
        <header>
          <button type="button" className="hamburger" onClick={() => setOpen((value) => !value)}>
            ☰
          </button>
          <h1>agent-chat</h1>
          <label>
            config{" "}
            <select
              value={active?.config || ""}
              onChange={(event) => {
                if (active) setSessionConfig(active.id, event.target.value);
              }}
            >
              {configs.map((config) => (
                <option key={config.name} value={config.name}>
                  {config.display_name} ({config.provider}/{config.model}
                  {config.tools.length ? ` · ${config.tools.join(",")}` : ""}
                  {config.skills.length ? ` · ${config.skills.length} skills` : ""})
                </option>
              ))}
            </select>
          </label>
          <span className="meta">{active ? `${active.config || "no config"} · thread ${active.id}` : "no configs"}</span>
        </header>
        {active ? (
          <ChatPane
            key={active.id}
            threadId={active.id}
            skills={configs.find((config) => config.name === active.config)?.skills ?? []}
          />
        ) : null}
      </main>
    </div>
  );
}

function SessionRow({
  title,
  time,
  active,
  onOpen,
  onRename,
  onDelete,
}: {
  title: string;
  time: string;
  active: boolean;
  onOpen: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  return (
    <div className={active ? "session active" : "session"} onClick={onOpen}>
      <div className="info">
        {editing ? (
          <input
            className="rename"
            value={draft}
            autoFocus
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => {
              onRename(draft.trim());
              setEditing(false);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                onRename(draft.trim());
                setEditing(false);
              }
              if (event.key === "Escape") setEditing(false);
            }}
          />
        ) : (
          <>
            <div className="title">{title}</div>
            <div className="when">{time}</div>
          </>
        )}
      </div>
      <div className="acts">
        <button
          type="button"
          title="Rename"
          onClick={(event) => {
            event.stopPropagation();
            setDraft(title);
            setEditing(true);
          }}
        >
          ✎
        </button>
        <button
          type="button"
          title="Delete"
          onClick={(event) => {
            event.stopPropagation();
            onDelete();
          }}
        >
          🗑
        </button>
      </div>
    </div>
  );
}
