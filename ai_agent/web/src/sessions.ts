const STORE_KEY = "agent-chat:sessions:v1";
const LEGACY_STORE_KEY = "agent-scaffold:sessions:v1";

export type StoredPart =
  | {
      type: "reasoning";
      parentId?: string;
      startedAt: number;
      completedAt?: number;
    }
  | {
      type: "tool-call";
      toolCallId: string;
      toolName: string;
      parentId?: string;
      source?: string;
      startedAt: number;
      completedAt?: number;
    };

export type StoredMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  parts: StoredPart[];
  traceId?: string;
  traceUrl?: string;
};

export type Session = {
  id: string;
  title: string;
  config: string;
  createdAt: number;
  updatedAt: number;
  messages: StoredMessage[];
};

type Store = {
  activeId: string | null;
  order: string[];
  sessions: Record<string, Session>;
};

const listeners = new Set<() => void>();

let store: Store = { activeId: null, order: [], sessions: {} };

function emit() {
  for (const listener of listeners) listener();
}

export function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getStore() {
  return store;
}

function persist() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(store));
  } catch (error) {
    if (error instanceof DOMException && error.name === "QuotaExceededError" && store.order.length > 1) {
      const oldest = store.order.pop();
      if (oldest) delete store.sessions[oldest];
      try {
        localStorage.setItem(STORE_KEY, JSON.stringify(store));
      } catch {
        /* give up */
      }
    }
  }
}

function normalizeMessage(message: StoredMessage & { md?: string }, index: number, sessionId: string): StoredMessage {
  return {
    id: message.id || `${sessionId}-${index}`,
    role: message.role === "user" ? "user" : "assistant",
    text: message.text || message.md || "",
    parts: Array.isArray(message.parts) ? message.parts : [],
    ...(message.traceId ? { traceId: message.traceId } : {}),
    ...(message.traceUrl ? { traceUrl: message.traceUrl } : {}),
  };
}

function readStoredRaw(): string | null {
  const current = localStorage.getItem(STORE_KEY);
  if (current) return current;
  const legacy = localStorage.getItem(LEGACY_STORE_KEY);
  if (!legacy) return null;
  try {
    localStorage.setItem(STORE_KEY, legacy);
  } catch {
    /* still use the legacy payload in memory */
  }
  return legacy;
}

export function loadStore() {
  try {
    const raw = readStoredRaw();
    if (!raw) return;
    const parsed = JSON.parse(raw) as Store;
    if (!parsed?.sessions || !Array.isArray(parsed.order)) return;
    for (const session of Object.values(parsed.sessions)) {
      session.messages = (session.messages ?? []).map((message, index) =>
        normalizeMessage(message as StoredMessage & { md?: string }, index, session.id),
      );
    }
    store = parsed;
  } catch {
    store = { activeId: null, order: [], sessions: {} };
  }
  emit();
}

export function newSessionId() {
  return `web-${Math.random().toString(36).slice(2, 10)}`;
}

export function createSession(config: string) {
  const now = Date.now();
  const session: Session = {
    id: newSessionId(),
    title: "",
    config,
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
  store.sessions[session.id] = session;
  store.order.unshift(session.id);
  store.activeId = session.id;
  persist();
  emit();
  return session;
}

export function setActive(id: string) {
  if (!store.sessions[id]) return;
  store.activeId = id;
  persist();
  emit();
}

export function setSessionConfig(id: string, config: string) {
  const session = store.sessions[id];
  if (!session) return;
  session.config = config;
  persist();
  emit();
}

export function renameSession(id: string, title: string) {
  const session = store.sessions[id];
  if (!session) return;
  session.title = title;
  persist();
  emit();
}

export function deleteSession(id: string) {
  delete store.sessions[id];
  store.order = store.order.filter((item) => item !== id);
  if (store.activeId === id) store.activeId = store.order[0] ?? null;
  persist();
  emit();
}

export function touchSession(id: string) {
  const session = store.sessions[id];
  if (!session) return;
  session.updatedAt = Date.now();
  const index = store.order.indexOf(id);
  if (index > 0) {
    store.order.splice(index, 1);
    store.order.unshift(id);
  }
  persist();
  emit();
}

export function upsertMessage(id: string, message: StoredMessage) {
  const session = store.sessions[id];
  if (!session) return;
  const index = session.messages.findIndex((item) => item.id === message.id);
  if (index >= 0) session.messages[index] = message;
  else session.messages.push(message);
  if (!session.title && message.role === "user" && message.text) {
    session.title = message.text.length > 60 ? `${message.text.slice(0, 60)}…` : message.text;
  }
  touchSession(id);
}
