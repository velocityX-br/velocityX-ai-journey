export type SseFrame = {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  input?: unknown;
  output?: string;
  parent_id?: string;
  message?: string;
  trace_id?: string;
  trace_url?: string;
};

export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const frames = buf.split("\n\n");
      buf = frames.pop() ?? "";
      for (const frame of frames) {
        const line = frame.split("\n").find((item) => item.startsWith("data:"));
        if (!line) continue;
        yield JSON.parse(line.slice(5).trim()) as SseFrame;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
