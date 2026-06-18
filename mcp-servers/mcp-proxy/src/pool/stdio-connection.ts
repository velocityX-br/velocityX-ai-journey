import { spawn, ChildProcess } from 'child_process';
import { ServerConfig } from '../types';
import { BackendRegistry } from '../registry/backend-registry';

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
}

const MAX_RESTARTS = 3;

export class StdioConnection {
  _healthy = true;
  private _child: ChildProcess | null = null;
  private _pendingRequests: Map<number, PendingRequest> = new Map();
  private _nextId = 1;
  private _crashCount = 0;
  private _lineBuffer = '';

  constructor(
    private readonly config: ServerConfig,
    private readonly registry: BackendRegistry,
  ) {}

  get isHealthy(): boolean {
    return this._healthy;
  }

  async connect(): Promise<void> {
    await this._spawn();
  }

  private async _spawn(): Promise<void> {
    const [cmd, ...args] = this.config.command!;
    const child = spawn(cmd, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    this._child = child;
    this._lineBuffer = '';

    await new Promise<void>((resolve, reject) => {
      child.once('spawn', () => resolve());
      child.once('error', (err) => reject(err));
    });

    // Stream JSON-RPC responses from stdout
    child.stdout!.on('data', (chunk: Buffer) => {
      this._lineBuffer += chunk.toString();
      const lines = this._lineBuffer.split('\n');
      // Keep the incomplete last segment in the buffer
      this._lineBuffer = lines.pop() ?? '';
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        this._handleLine(trimmed);
      }
    });

    child.once('exit', (code) => {
      this._handleExit(code);
    });
  }

  private _handleLine(line: string): void {
    let msg: { id?: number; result?: unknown; error?: { message?: string } };
    try {
      msg = JSON.parse(line) as typeof msg;
    } catch {
      // Not a JSON line — ignore (could be debug output)
      return;
    }

    if (msg.id === undefined) return;

    const pending = this._pendingRequests.get(msg.id);
    if (!pending) return;

    this._pendingRequests.delete(msg.id);

    if (msg.error) {
      pending.reject(new Error(msg.error.message ?? 'Backend error'));
    } else {
      pending.resolve(msg.result);
    }
  }

  private _handleExit(_code: number | null): void {
    // Reject all in-flight requests
    for (const [id, pending] of this._pendingRequests) {
      pending.reject(new Error('Backend process exited unexpectedly'));
      this._pendingRequests.delete(id);
    }

    this._crashCount++;

    if (this._crashCount > MAX_RESTARTS) {
      this._healthy = false;
      this.registry.markUnhealthy(this.config.name);
      return;
    }

    // Auto-restart
    this._spawn().catch(() => {
      this._healthy = false;
      this.registry.markUnhealthy(this.config.name);
    });
  }

  async request(method: string, params: unknown): Promise<unknown> {
    if (!this._healthy || !this._child) {
      throw new Error(`StdioConnection for "${this.config.name}" is not healthy`);
    }

    const id = this._nextId++;
    const message = JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n';

    return new Promise<unknown>((resolve, reject) => {
      this._pendingRequests.set(id, { resolve, reject });
      this._child!.stdin!.write(message);
    });
  }

  async disconnect(): Promise<void> {
    if (!this._child) return;

    // Prevent the exit handler from triggering restarts
    this._crashCount = MAX_RESTARTS + 1;

    const child = this._child;
    this._child = null;

    await new Promise<void>((resolve) => {
      child.once('exit', () => resolve());
      child.kill();
    });
  }
}
