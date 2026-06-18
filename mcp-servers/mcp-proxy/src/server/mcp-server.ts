import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
  ListResourcesRequestSchema,
  ReadResourceRequestSchema,
  McpError,
  ErrorCode,
} from '@modelcontextprotocol/sdk/types.js';
import type { Transport } from '@modelcontextprotocol/sdk/shared/transport.js';

import { BackendRegistry } from '../registry/backend-registry';
import { Router } from '../router/router';
import { ConnectionPool } from '../pool/connection-pool';
import { ProxyConfig, ToolCallResult } from '../types';

export class McpServer {
  private readonly server: Server;
  private transport: Transport | null = null;

  constructor(
    private readonly registry: BackendRegistry,
    private readonly router: Router,
    private readonly pool: ConnectionPool,
    private readonly config: ProxyConfig,
  ) {
    this.server = new Server(
      { name: 'mcp-proxy', version: '0.1.0' },
      { capabilities: { tools: {}, resources: {} } },
    );

    this._registerHandlers();
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  /**
   * Start the MCP server using stdio transport (primary for local Claude Code use).
   */
  async start(): Promise<void> {
    const transport = new StdioServerTransport();
    await this.connectTransport(transport);
  }

  /**
   * Attach an already-constructed transport.  Used in tests (InMemoryTransport)
   * and can also be used by callers who want to supply a custom transport.
   */
  async connectTransport(transport: Transport): Promise<void> {
    this.transport = transport;
    await this.server.connect(transport);
  }

  /** Stop gracefully. */
  async stop(): Promise<void> {
    await this.server.close();
    this.transport = null;
  }

  // ── Handler registration ────────────────────────────────────────────────────

  private _registerHandlers(): void {
    // tools/list
    this.server.setRequestHandler(ListToolsRequestSchema, async (_request) => {
      try {
        // Pass undefined context — no LLM pruning by context for v1
        const tools = await this.router.getToolsList(undefined);

        return {
          tools: tools.map((t) => ({
            name: t.name,
            description: t.description ?? `Tool ${t.name} provided by ${t.serverName}`,
            inputSchema: { type: 'object' as const },
          })),
        };
      } catch (err) {
        throw new McpError(
          ErrorCode.InternalError,
          `Failed to list tools: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    });

    // tools/call
    this.server.setRequestHandler(CallToolRequestSchema, async (request) => {
      const { name: toolName, arguments: args } = request.params;

      try {
        const routeResult = this.router.routeToolCall(toolName);

        if (routeResult === null) {
          // Build list of known tools for the hint
          const available = await this.router.getToolsList(undefined);
          const names = available.map((t) => t.name).join(', ') || '(none)';
          throw new McpError(
            ErrorCode.MethodNotFound,
            `Tool not found: ${toolName}. Available tools: ${names}`,
          );
        }

        const result: ToolCallResult = await this.pool.callTool(routeResult.serverName, toolName, args ?? {});

        return {
          content: result.content ?? [],
          isError: result.isError ?? false,
        };
      } catch (err) {
        // Re-throw McpError instances as-is (they carry the correct error code)
        if (err instanceof McpError) {
          throw err;
        }

        throw new McpError(
          ErrorCode.InternalError,
          `Tool execution failed: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    });

    // resources/list — v1 stub: empty list
    this.server.setRequestHandler(ListResourcesRequestSchema, async (_request) => {
      return { resources: [] };
    });

    // resources/read — v1 stub: not found
    this.server.setRequestHandler(ReadResourceRequestSchema, async (_request) => {
      throw new McpError(
        ErrorCode.MethodNotFound,
        'Resource reading is not supported in this version',
      );
    });
  }
}
