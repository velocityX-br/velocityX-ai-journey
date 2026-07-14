/**
 * Unit tests for Connection Pool (Task 5).
 *
 * All child_process and HTTP calls are mocked — no real processes or network.
 */

import { EventEmitter } from 'events';
import { ProxyConfig, ServerConfig } from '../../src/types';
import { BackendRegistry } from '../../src/registry/backend-registry';

// ── helpers ───────────────────────────────────────────────────────────────────

function makeConfig(servers: Partial<ServerConfig>[]): ProxyConfig {
  const fullServers: ServerConfig[] = servers.map((s, i) => ({
    name: s.name ?? `server-${i}`,
    transport: s.transport ?? 'stdio',
    command: s.command,
    url: s.url,
    auth: s.auth,
    tools: s.tools ?? [],
    tags: s.tags ?? [],
  }));

  return {
    servers: fullServers,
    router: {
      llm_prune: { enabled: false, threshold: 20 },
    },
    proxy: { mcp_port: 3000, http_port: 3001 },
  };
}

// ── Mock child_process ─────────────────────────────────────────────────────────

// We define MockChildProcess before the jest.mock factory so the factory can
// reference it via a module-level variable.

class MockChildProcess extends EventEmitter {
  stdin: { write: jest.Mock; end: jest.Mock };
  stdout: EventEmitter;
  stderr: EventEmitter;
  killed = false;

  constructor() {
    super();
    this.stdin = { write: jest.fn(), end: jest.fn() };
    this.stdout = new EventEmitter();
    this.stderr = new EventEmitter();
  }

  kill(): void {
    this.killed = true;
    this.emit('exit', 0, null);
  }

  /** Simulate a line arriving on stdout. */
  emitStdoutLine(line: string): void {
    this.stdout.emit('data', Buffer.from(line + '\n'));
  }

  /** Simulate an unexpected crash. */
  crash(code = 1): void {
    this.emit('exit', code, null);
  }
}

let mockSpawnImpl: jest.Mock;

jest.mock('child_process', () => ({
  spawn: (...args: unknown[]) => mockSpawnImpl(...args),
}));

// ── Mock global fetch ──────────────────────────────────────────────────────────

let mockFetchImpl: jest.Mock;

global.fetch = (...args: unknown[]) => mockFetchImpl(...args as Parameters<typeof fetch>);

// ── Imports after mocks ────────────────────────────────────────────────────────

import { StdioConnection } from '../../src/pool/stdio-connection';
import { SseConnection } from '../../src/pool/sse-connection';
import { ConnectionPool } from '../../src/pool/connection-pool';

// ─────────────────────────────────────────────────────────────────────────────
// StdioConnection tests
// ─────────────────────────────────────────────────────────────────────────────

describe('StdioConnection', () => {
  let mockChild: MockChildProcess;
  let registry: BackendRegistry;
  let serverConfig: ServerConfig;

  beforeEach(() => {
    mockChild = new MockChildProcess();
    mockSpawnImpl = jest.fn().mockReturnValue(mockChild);

    const config = makeConfig([
      { name: 'fs', transport: 'stdio', command: ['npx', 'server-fs'], tools: ['read_file'] },
    ]);
    registry = new BackendRegistry(config);
    serverConfig = config.servers[0];
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it('spawns the child process on connect()', async () => {
    const conn = new StdioConnection(serverConfig, registry);
    const connectPromise = conn.connect();

    // Process emits 'spawn' to signal successful start
    mockChild.emit('spawn');
    await connectPromise;

    expect(mockSpawnImpl).toHaveBeenCalledWith(
      serverConfig.command![0],
      serverConfig.command!.slice(1),
      expect.objectContaining({ stdio: ['pipe', 'pipe', 'pipe'] }),
    );
    expect(conn.isHealthy).toBe(true);
  });

  it('sends a JSON-RPC request and resolves with the response', async () => {
    const conn = new StdioConnection(serverConfig, registry);
    const connectPromise = conn.connect();
    mockChild.emit('spawn');
    await connectPromise;

    const requestPromise = conn.request('tools/list', {});

    // Simulate the backend responding to id=1
    const written: string = mockChild.stdin.write.mock.calls[0][0] as string;
    const req = JSON.parse(written);
    mockChild.emitStdoutLine(
      JSON.stringify({ jsonrpc: '2.0', id: req.id, result: { tools: [] } }),
    );

    const result = await requestPromise;
    expect(result).toEqual({ tools: [] });
  });

  it('marks unhealthy after 3 restarts (4th crash)', async () => {
    const conn = new StdioConnection(serverConfig, registry);
    const connectPromise = conn.connect();
    mockChild.emit('spawn');
    await connectPromise;

    // Each crash triggers a reconnect attempt; we need a new mock child for each restart
    const children: MockChildProcess[] = [mockChild];
    for (let i = 1; i <= 3; i++) {
      const c = new MockChildProcess();
      children.push(c);
    }

    // After first crash, spawn returns child[1], etc.
    let spawnCallCount = 0;
    mockSpawnImpl = jest.fn().mockImplementation(() => {
      spawnCallCount++;
      const child = children[spawnCallCount] ?? children[children.length - 1];
      // Emit spawn so connect resolves quickly
      setImmediate(() => child.emit('spawn'));
      return child;
    });

    // Crash 1 → restart 1 (spawns children[1])
    mockChild.crash();
    await new Promise((r) => setImmediate(r));
    children[1].emit('spawn');
    await new Promise((r) => setImmediate(r));

    // Crash 2 → restart 2 (spawns children[2])
    children[1].crash();
    await new Promise((r) => setImmediate(r));
    children[2].emit('spawn');
    await new Promise((r) => setImmediate(r));

    // Crash 3 → restart 3 (spawns children[3])
    children[2].crash();
    await new Promise((r) => setImmediate(r));
    children[3].emit('spawn');
    await new Promise((r) => setImmediate(r));

    // Still healthy after 3 restarts
    expect(conn.isHealthy).toBe(true);

    // Crash 4 → no more restarts, marks unhealthy
    children[3].crash();
    await new Promise((r) => setImmediate(r));

    expect(conn.isHealthy).toBe(false);
    expect(registry.getServer('fs')!.health).toBe('unhealthy');
  }, 10_000);

  it('is healthy before any crash', async () => {
    const conn = new StdioConnection(serverConfig, registry);
    const connectPromise = conn.connect();
    mockChild.emit('spawn');
    await connectPromise;

    expect(conn.isHealthy).toBe(true);
  });

  it('disconnect() kills the child process', async () => {
    const conn = new StdioConnection(serverConfig, registry);
    const connectPromise = conn.connect();
    mockChild.emit('spawn');
    await connectPromise;

    await conn.disconnect();
    expect(mockChild.killed).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// SseConnection tests
// ─────────────────────────────────────────────────────────────────────────────

describe('SseConnection', () => {
  afterEach(() => {
    jest.clearAllMocks();
    delete process.env.MY_TOKEN;
  });

  it('POSTs a JSON-RPC request to the configured URL', async () => {
    const serverConfig: ServerConfig = {
      name: 'github',
      transport: 'sse',
      url: 'https://mcp.example.com/mcp',
      tools: ['search_repos'],
      tags: [],
    };

    mockFetchImpl = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ jsonrpc: '2.0', id: 1, result: { repos: [] } }),
    });

    const conn = new SseConnection(serverConfig);
    await conn.connect();

    const result = await conn.request('tools/list', {});

    expect(mockFetchImpl).toHaveBeenCalledWith(
      serverConfig.url,
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
        body: expect.stringContaining('"method":"tools/list"'),
      }),
    );
    expect(result).toEqual({ repos: [] });
  });

  it('includes Authorization header when auth is configured', async () => {
    process.env.MY_TOKEN = 'secret-token';

    const serverConfig: ServerConfig = {
      name: 'github',
      transport: 'sse',
      url: 'https://mcp.example.com/mcp',
      auth: { type: 'bearer', token_env: 'MY_TOKEN' },
      tools: [],
      tags: [],
    };

    mockFetchImpl = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ jsonrpc: '2.0', id: 1, result: {} }),
    });

    const conn = new SseConnection(serverConfig);
    await conn.connect();
    await conn.request('tools/list', {});

    const [, options] = mockFetchImpl.mock.calls[0] as [string, RequestInit];
    expect((options.headers as Record<string, string>)['Authorization']).toBe(
      'Bearer secret-token',
    );
  });

  it('throws with HTTP status code in message on 4xx/5xx', async () => {
    const serverConfig: ServerConfig = {
      name: 'github',
      transport: 'sse',
      url: 'https://mcp.example.com/mcp',
      tools: [],
      tags: [],
    };

    mockFetchImpl = jest.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({}),
    });

    const conn = new SseConnection(serverConfig);
    await conn.connect();

    await expect(conn.request('tools/list', {})).rejects.toThrow('403');
  });

  it('starts healthy and remains healthy after a successful request', async () => {
    const serverConfig: ServerConfig = {
      name: 'github',
      transport: 'sse',
      url: 'https://mcp.example.com/mcp',
      tools: [],
      tags: [],
    };

    mockFetchImpl = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ jsonrpc: '2.0', id: 1, result: {} }),
    });

    const conn = new SseConnection(serverConfig);
    expect(conn.isHealthy).toBe(true);
    await conn.connect();
    await conn.request('tools/list', {});
    expect(conn.isHealthy).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// ConnectionPool tests
// ─────────────────────────────────────────────────────────────────────────────

describe('ConnectionPool', () => {
  let config: ProxyConfig;
  let registry: BackendRegistry;
  let stdioChild: MockChildProcess;

  beforeEach(() => {
    stdioChild = new MockChildProcess();
    mockSpawnImpl = jest.fn().mockImplementation(() => {
      setImmediate(() => stdioChild.emit('spawn'));
      return stdioChild;
    });

    mockFetchImpl = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text: 'ok' }] } }),
    });

    config = makeConfig([
      { name: 'fs', transport: 'stdio', command: ['npx', 'server-fs'], tools: ['read_file'] },
      { name: 'gh', transport: 'sse', url: 'https://mcp.example.com', tools: ['search_repos'] },
    ]);
    registry = new BackendRegistry(config);
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  // 1. lazy init: creates connection on first call
  it('creates a connection on first getConnection() call (lazy init)', async () => {
    const pool = new ConnectionPool(config, registry);
    const conn = await pool.getConnection('fs');
    expect(conn).toBeDefined();
    expect(mockSpawnImpl).toHaveBeenCalledTimes(1);
  });

  // 2. caches: same instance returned on second call
  it('returns the same cached connection on subsequent calls', async () => {
    const pool = new ConnectionPool(config, registry);
    const conn1 = await pool.getConnection('fs');
    const conn2 = await pool.getConnection('fs');
    expect(conn1).toBe(conn2);
    expect(mockSpawnImpl).toHaveBeenCalledTimes(1);
  });

  // 3. unhealthy connection: creates a new one
  it('creates a new connection when the cached one is unhealthy', async () => {
    const pool = new ConnectionPool(config, registry);
    const conn1 = await pool.getConnection('fs');

    // Force it unhealthy by draining all restart attempts
    stdioChild.crash();
    await new Promise((r) => setImmediate(r));
    // Crash more times to exhaust retries
    for (let i = 0; i < 3; i++) {
      const nextChild = new MockChildProcess();
      mockSpawnImpl.mockImplementationOnce(() => {
        setImmediate(() => nextChild.emit('spawn'));
        return nextChild;
      });
      // Trigger crash on the current child (simulate)
      stdioChild.crash();
      await new Promise((r) => setImmediate(r));
    }

    // Manually mark unhealthy for test purposes
    (conn1 as StdioConnection & { _healthy: boolean })['_healthy'] = false;

    const conn2 = await pool.getConnection('fs');
    expect(conn2).not.toBe(conn1);
  });

  // 4. callTool success
  it('callTool returns the tool result on success', async () => {
    const pool = new ConnectionPool(config, registry);

    // For stdio, we need to simulate the response
    const originalGetConn = pool.getConnection.bind(pool);
    jest.spyOn(pool, 'getConnection').mockImplementation(async (name) => {
      const conn = await originalGetConn(name);
      // Intercept the request to auto-respond
      const origRequest = conn.request.bind(conn);
      jest.spyOn(conn, 'request').mockResolvedValue({
        content: [{ type: 'text', text: 'file contents' }],
      });
      return conn;
    });

    const result = await pool.callTool('fs', 'read_file', { path: '/tmp/test.txt' });
    expect(result).toEqual({ content: [{ type: 'text', text: 'file contents' }] });
  });

  // 5. server not found
  it('callTool throws when the server is not found in config', async () => {
    const pool = new ConnectionPool(config, registry);
    await expect(pool.callTool('unknown-server', 'some_tool', {})).rejects.toThrow(
      /unknown-server/,
    );
  });

  // 5b. permanently unhealthy server throws
  it('getConnection throws when the server is permanently unhealthy in the registry', async () => {
    const pool = new ConnectionPool(config, registry);
    // Mark the server unhealthy in the registry (simulates exhausted retries)
    registry.markUnhealthy('fs');
    await expect(pool.getConnection('fs')).rejects.toThrow(
      /unhealthy/,
    );
  });

  // 6. disconnectAll disconnects all active connections
  it('disconnectAll() disconnects all active connections', async () => {
    const pool = new ConnectionPool(config, registry);
    const conn = await pool.getConnection('fs');
    const disconnectSpy = jest.spyOn(conn, 'disconnect').mockResolvedValue();

    await pool.disconnectAll();

    expect(disconnectSpy).toHaveBeenCalled();
  });
});
