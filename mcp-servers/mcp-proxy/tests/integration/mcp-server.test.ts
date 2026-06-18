import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { McpError, ErrorCode } from '@modelcontextprotocol/sdk/types.js';

import { McpServer } from '../../src/server/mcp-server';
import { BackendRegistry } from '../../src/registry/backend-registry';
import { Router } from '../../src/router/router';
import { ConnectionPool } from '../../src/pool/connection-pool';
import { ProxyConfig, ToolEntry, RouterResult } from '../../src/types';

// ── helpers ──────────────────────────────────────────────────────────────────

function makeConfig(): ProxyConfig {
  return {
    servers: [
      {
        name: 'filesystem',
        transport: 'stdio',
        command: ['node', 'fs-server.js'],
        tools: ['read_file', 'write_file'],
        tags: ['file'],
      },
      {
        name: 'github',
        transport: 'sse',
        url: 'https://mcp.github.com/sse',
        tools: ['search_repos'],
        tags: ['code'],
      },
    ],
    router: {
      llm_prune: { enabled: false, threshold: 20 },
    },
    proxy: { mcp_port: 3000, http_port: 3001 },
  };
}

/** Build a connected Client ↔ McpServer pair using in-memory transport. */
async function buildPair(
  mockRouter: Partial<Router>,
  mockPool: Partial<ConnectionPool>,
  config: ProxyConfig = makeConfig(),
): Promise<{ client: Client; mcpServer: McpServer }> {
  const registry = new BackendRegistry(config);

  const mcpServer = new McpServer(
    registry,
    mockRouter as Router,
    mockPool as ConnectionPool,
    config,
  );

  const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
  await mcpServer.connectTransport(serverTransport);

  const client = new Client({ name: 'test-client', version: '0.0.1' });
  await client.connect(clientTransport);

  return { client, mcpServer };
}

// ── 1. tools/list — healthy tools ────────────────────────────────────────────

describe('McpServer tools/list', () => {
  it('returns tools from router.getToolsList()', async () => {
    const tools: ToolEntry[] = [
      { name: 'read_file', serverName: 'filesystem', tags: ['file'] },
      { name: 'write_file', serverName: 'filesystem', tags: ['file'] },
    ];

    const mockRouter = {
      getToolsList: jest.fn().mockResolvedValue(tools),
      routeToolCall: jest.fn(),
    };
    const mockPool = { callTool: jest.fn() };

    const { client, mcpServer } = await buildPair(mockRouter, mockPool);
    try {
      const result = await client.listTools();
      expect(result.tools).toHaveLength(2);
      expect(result.tools.map((t) => t.name)).toEqual(['read_file', 'write_file']);
    } finally {
      await mcpServer.stop();
    }
  });

  // ── 2. tools/list — empty list ──────────────────────────────────────────────

  it('returns empty list when no healthy tools', async () => {
    const mockRouter = {
      getToolsList: jest.fn().mockResolvedValue([]),
      routeToolCall: jest.fn(),
    };
    const mockPool = { callTool: jest.fn() };

    const { client, mcpServer } = await buildPair(mockRouter, mockPool);
    try {
      const result = await client.listTools();
      expect(result.tools).toHaveLength(0);
    } finally {
      await mcpServer.stop();
    }
  });
});

// ── 3. tools/call — success ───────────────────────────────────────────────────

describe('McpServer tools/call', () => {
  it('routes to the correct backend and returns result', async () => {
    const routeResult: RouterResult = { serverName: 'filesystem', matchedBy: 'exact' };
    const toolOutput = { content: [{ type: 'text', text: 'file contents' }] };

    const mockRouter = {
      getToolsList: jest.fn().mockResolvedValue([
        { name: 'read_file', serverName: 'filesystem', tags: ['file'] },
      ]),
      routeToolCall: jest.fn().mockReturnValue(routeResult),
    };
    const mockPool = { callTool: jest.fn().mockResolvedValue(toolOutput) };

    const { client, mcpServer } = await buildPair(mockRouter, mockPool);
    try {
      const result = await client.callTool({ name: 'read_file', arguments: { path: '/tmp/a' } });
      expect(mockPool.callTool).toHaveBeenCalledWith('filesystem', 'read_file', { path: '/tmp/a' });
      expect(result.content).toEqual([{ type: 'text', text: 'file contents' }]);
    } finally {
      await mcpServer.stop();
    }
  });

  // ── 4. tools/call — tool not found ───────────────────────────────────────────

  it('returns MCP error MethodNotFound when tool not found', async () => {
    const mockRouter = {
      getToolsList: jest.fn().mockResolvedValue([
        { name: 'read_file', serverName: 'filesystem', tags: ['file'] },
      ]),
      routeToolCall: jest.fn().mockReturnValue(null),
    };
    const mockPool = { callTool: jest.fn() };

    const { client, mcpServer } = await buildPair(mockRouter, mockPool);
    try {
      await expect(
        client.callTool({ name: 'unknown_tool', arguments: {} }),
      ).rejects.toThrow(McpError);

      await expect(
        client.callTool({ name: 'unknown_tool', arguments: {} }),
      ).rejects.toMatchObject({ code: ErrorCode.MethodNotFound });
    } finally {
      await mcpServer.stop();
    }
  });

  // ── 5. tools/call — backend throws ───────────────────────────────────────────

  it('returns MCP InternalError when backend throws', async () => {
    const routeResult: RouterResult = { serverName: 'filesystem', matchedBy: 'exact' };

    const mockRouter = {
      getToolsList: jest.fn().mockResolvedValue([
        { name: 'read_file', serverName: 'filesystem', tags: ['file'] },
      ]),
      routeToolCall: jest.fn().mockReturnValue(routeResult),
    };
    const mockPool = {
      callTool: jest.fn().mockRejectedValue(new Error('backend connection failed')),
    };

    const { client, mcpServer } = await buildPair(mockRouter, mockPool);
    try {
      await expect(
        client.callTool({ name: 'read_file', arguments: {} }),
      ).rejects.toMatchObject({ code: ErrorCode.InternalError });
    } finally {
      await mcpServer.stop();
    }
  });
});

// ── 6. resources/list — empty list ───────────────────────────────────────────

describe('McpServer resources/list', () => {
  it('returns empty resources list', async () => {
    const mockRouter = {
      getToolsList: jest.fn().mockResolvedValue([]),
      routeToolCall: jest.fn(),
    };
    const mockPool = { callTool: jest.fn() };

    const { client, mcpServer } = await buildPair(mockRouter, mockPool);
    try {
      const result = await client.listResources();
      expect(result.resources).toHaveLength(0);
    } finally {
      await mcpServer.stop();
    }
  });
});

// ── 7. resources/read — MethodNotFound ───────────────────────────────────────

describe('McpServer resources/read', () => {
  it('resources/read throws MethodNotFound', async () => {
    const mockRouter = {
      getToolsList: jest.fn().mockResolvedValue([]),
      routeToolCall: jest.fn(),
    };
    const mockPool = { callTool: jest.fn() };

    const { client, mcpServer } = await buildPair(mockRouter, mockPool);
    try {
      await expect(
        client.readResource({ uri: 'file:///test.txt' })
      ).rejects.toMatchObject({
        code: ErrorCode.MethodNotFound,
      });
    } finally {
      await mcpServer.stop();
    }
  });
});
