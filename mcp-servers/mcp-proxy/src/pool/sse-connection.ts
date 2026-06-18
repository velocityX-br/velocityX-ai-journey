import { ServerConfig } from '../types';

export class SseConnection {
  private _healthy = true;
  private _nextId = 1;

  constructor(private readonly config: ServerConfig) {}

  get isHealthy(): boolean {
    return this._healthy;
  }

  // For HTTP/SSE backends no persistent setup is needed — connections are stateless HTTP POSTs.
  async connect(): Promise<void> {
    // No-op for stateless HTTP mode. Could establish an SSE stream here in the future.
  }

  async request(method: string, params: unknown): Promise<unknown> {
    if (!this.config.url) {
      throw new Error(`SseConnection for "${this.config.name}" has no URL configured`);
    }

    const id = this._nextId++;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };

    if (this.config.auth?.token_env) {
      const token = process.env[this.config.auth.token_env];
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
    }

    const body = JSON.stringify({ jsonrpc: '2.0', id, method, params });

    const response = await fetch(this.config.url, {
      method: 'POST',
      headers,
      body,
    });

    if (!response.ok) {
      throw new Error(`HTTP error ${response.status} from backend "${this.config.name}"`);
    }

    const json = (await response.json()) as { result?: unknown; error?: { message?: string } };

    if (json.error) {
      throw new Error(json.error.message ?? 'Backend error');
    }

    return json.result;
  }

  async disconnect(): Promise<void> {
    // Nothing to close for stateless HTTP mode.
  }
}
