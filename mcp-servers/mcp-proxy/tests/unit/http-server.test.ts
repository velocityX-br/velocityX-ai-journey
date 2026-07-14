import request from 'supertest';
import { HttpServer } from '../../src/server/http-server';
import { BackendRegistry } from '../../src/registry/backend-registry';
import { Router } from '../../src/router/router';
import { ConnectionPool } from '../../src/pool/connection-pool';
import { ProxyConfig, ToolEntry } from '../../src/types';

// ── Shared helpers ────────────────────────────────────────────────────────────

function makeConfig(overrides: Partial<ProxyConfig['proxy']> = {}): ProxyConfig {
  return {
    servers: [
      {
        name: 'filesystem',
        transport: 'stdio',
        command: ['npx', 'server-filesystem'],
        tools: ['read_file', 'write_file'],
        tags: ['file'],
      },
    ],
    router: {
      llm_prune: { enabled: false, threshold: 20 },
    },
    proxy: {
      mcp_port: 3000,
      http_port: 0, // port 0 → OS assigns a random free port
      ...overrides,
    },
  };
}

const SAMPLE_TOOLS: ToolEntry[] = [
  { name: 'read_file', serverName: 'filesystem', tags: ['file'] },
  { name: 'write_file', serverName: 'filesystem', tags: ['file'] },
];

function makeRegistry(): jest.Mocked<BackendRegistry> {
  const config = makeConfig();
  const real = new BackendRegistry(config);
  return {
    getServer: jest.fn().mockImplementation(real.getServer.bind(real)),
    getAllServers: jest.fn().mockReturnValue(real.getAllServers()),
    getToolOwner: jest.fn().mockImplementation(real.getToolOwner.bind(real)),
    markUnhealthy: jest.fn(),
    markHealthy: jest.fn(),
    getHealthyTools: jest.fn().mockReturnValue(SAMPLE_TOOLS),
    getAllTools: jest.fn().mockReturnValue(SAMPLE_TOOLS),
  } as unknown as jest.Mocked<BackendRegistry>;
}

function makeRouter(registry: jest.Mocked<BackendRegistry>): jest.Mocked<Router> {
  return {
    routeToolCall: jest.fn().mockImplementation((toolName: string) => {
      if (toolName === 'read_file' || toolName === 'write_file') {
        return { serverName: 'filesystem', matchedBy: 'exact' };
      }
      return null;
    }),
    getToolsList: jest.fn().mockResolvedValue(SAMPLE_TOOLS),
  } as unknown as jest.Mocked<Router>;
}

function makePool(): jest.Mocked<ConnectionPool> {
  return {
    callTool: jest.fn().mockResolvedValue({ content: [{ type: 'text', text: 'ok' }] }),
  } as unknown as jest.Mocked<ConnectionPool>;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('HttpServer', () => {
  let server: HttpServer;
  let registry: jest.Mocked<BackendRegistry>;
  let router: jest.Mocked<Router>;
  let pool: jest.Mocked<ConnectionPool>;

  beforeEach(async () => {
    registry = makeRegistry();
    router = makeRouter(registry);
    pool = makePool();
    const config = makeConfig();
    server = new HttpServer(registry, router, pool, config);
    await server.start();
  });

  afterEach(async () => {
    await server.stop();
  });

  // ── 1. GET /health ──────────────────────────────────────────────────────────
  it('GET /health returns 200 with status ok', async () => {
    const res = await request(server.app).get('/health');
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
    expect(typeof res.body.timestamp).toBe('string');
  });

  // ── 2. GET /tools ───────────────────────────────────────────────────────────
  it('GET /tools returns 200 with tools array', async () => {
    const res = await request(server.app).get('/tools');
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('tools');
    expect(Array.isArray(res.body.tools)).toBe(true);
    expect(res.body.tools).toEqual(SAMPLE_TOOLS);
  });

  // ── 3. GET /tools?context=foo calls router with context ────────────────────
  it('GET /tools?context=foo calls router.getToolsList with context', async () => {
    const res = await request(server.app).get('/tools?context=someprompt');
    expect(res.status).toBe(200);
    expect(router.getToolsList).toHaveBeenCalledWith('someprompt');
  });

  // ── 4. POST /tools/:name/call → 200 with result ───────────────────────────
  it('POST /tools/:name/call returns 200 with result', async () => {
    const args = { path: '/tmp/foo.txt' };
    const expectedResult = { content: [{ type: 'text', text: 'ok' }] };

    const res = await request(server.app)
      .post('/tools/read_file/call')
      .send({ arguments: args });

    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('result');
    expect(res.body.result).toEqual(expectedResult);
    expect(pool.callTool).toHaveBeenCalledWith('filesystem', 'read_file', { arguments: args });
  });

  // ── 5. POST /tools/:name/call → 404 when tool not found ───────────────────
  it('POST /tools/:name/call returns 404 when tool not found', async () => {
    const res = await request(server.app)
      .post('/tools/no_such_tool/call')
      .send({ arguments: {} });

    expect(res.status).toBe(404);
    expect(res.body).toHaveProperty('error');
    expect(res.body.error).toContain('no_such_tool');
  });

  // ── 5b. POST /tools/:name/call → 500 when routeToolCall throws synchronously
  it('POST /tools/:name/call returns 500 when routeToolCall throws synchronously', async () => {
    router.routeToolCall.mockImplementationOnce(() => {
      throw new Error('router internal failure');
    });

    const res = await request(server.app)
      .post('/tools/read_file/call')
      .send({ arguments: {} });

    expect(res.status).toBe(500);
    expect(res.body).toHaveProperty('error');
    expect(res.body.error).toContain('router internal failure');
  });

  // ── 6. GET /servers ─────────────────────────────────────────────────────────
  it('GET /servers returns 200 with server list', async () => {
    const res = await request(server.app).get('/servers');
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('servers');
    expect(Array.isArray(res.body.servers)).toBe(true);

    const srv = res.body.servers[0];
    expect(srv).toHaveProperty('name');
    expect(srv).toHaveProperty('transport');
    expect(srv).toHaveProperty('health');
    expect(srv).toHaveProperty('toolCount');
  });

  // ── 7. POST /admin/reload → 200 stub ──────────────────────────────────────
  it('POST /admin/reload returns 200 with reload message', async () => {
    const res = await request(server.app).post('/admin/reload');
    expect(res.status).toBe(200);
    expect(res.body.message).toBe('Config reloaded');
  });
});

// ── Auth tests ────────────────────────────────────────────────────────────────

describe('HttpServer with auth', () => {
  const VALID_KEY = 'secret-key-abc';
  const KEYS_ENV = 'TEST_PROXY_KEYS';

  let server: HttpServer;
  let registry: jest.Mocked<BackendRegistry>;
  let router: jest.Mocked<Router>;
  let pool: jest.Mocked<ConnectionPool>;

  beforeEach(async () => {
    process.env[KEYS_ENV] = `${VALID_KEY},another-key`;

    registry = makeRegistry();
    router = makeRouter(registry);
    pool = makePool();

    const config = makeConfig({
      auth: { type: 'api_key', keys_env: KEYS_ENV },
    });
    server = new HttpServer(registry, router, pool, config);
    await server.start();
  });

  afterEach(async () => {
    delete process.env[KEYS_ENV];
    await server.stop();
  });

  // ── 7 (auth). GET /tools without token → 401 ──────────────────────────────
  it('GET /tools without auth token returns 401', async () => {
    const res = await request(server.app).get('/tools');
    expect(res.status).toBe(401);
    expect(res.body.error).toBe('Unauthorized');
  });

  // ── 8 (auth). GET /tools with valid token → 200 ───────────────────────────
  it('GET /tools with valid Bearer token returns 200', async () => {
    const res = await request(server.app)
      .get('/tools')
      .set('Authorization', `Bearer ${VALID_KEY}`);

    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('tools');
  });

  // ── 9 (auth). GET /health without token → 200 (bypasses auth) ─────────────
  it('GET /health without auth token returns 200 (health bypasses auth)', async () => {
    const res = await request(server.app).get('/health');
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
  });

  // ── invalid key returns 401 ────────────────────────────────────────────────
  it('GET /tools with invalid Bearer token returns 401', async () => {
    const res = await request(server.app)
      .get('/tools')
      .set('Authorization', 'Bearer wrong-key');

    expect(res.status).toBe(401);
    expect(res.body.error).toBe('Unauthorized');
  });
});
