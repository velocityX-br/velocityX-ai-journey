import { ProxyConfig, ServerConfig, ToolCallResult } from '../types';
import { BackendRegistry } from '../registry/backend-registry';
import { StdioConnection } from './stdio-connection';
import { SseConnection } from './sse-connection';

type Connection = StdioConnection | SseConnection;

export class ConnectionPool {
  private readonly connections: Map<string, Connection> = new Map();

  constructor(
    private readonly config: ProxyConfig,
    private readonly registry: BackendRegistry,
  ) {}

  /**
   * Get (or lazily create) a connection to the named server.
   * If the cached connection is unhealthy a fresh one is created.
   */
  async getConnection(serverName: string): Promise<Connection> {
    // Check registry first — if server is permanently unhealthy, don't try to reconnect
    const serverState = this.registry.getServer(serverName);
    if (!serverState) {
      throw new Error(`Server not found: ${serverName}`);
    }
    if (serverState.health === 'unhealthy') {
      throw new Error(`Server "${serverName}" is unhealthy and not available`);
    }

    const existing = this.connections.get(serverName);
    if (existing && existing.isHealthy) {
      return existing;
    }

    // Look up server config
    const serverConfig = this.config.servers.find((s: ServerConfig) => s.name === serverName);
    if (!serverConfig) {
      throw new Error(`ConnectionPool: unknown server "${serverName}"`);
    }

    const conn = this._createConnection(serverConfig);
    await conn.connect();
    this.connections.set(serverName, conn);
    return conn;
  }

  /**
   * Forward a tool call to the correct backend.
   * The tool call is expressed as an MCP `tools/call` JSON-RPC request.
   */
  async callTool(serverName: string, toolName: string, args: unknown): Promise<ToolCallResult> {
    const conn = await this.getConnection(serverName);
    return conn.request('tools/call', { name: toolName, arguments: args }) as Promise<ToolCallResult>;
  }

  /** Disconnect all active backend connections. */
  async disconnectAll(): Promise<void> {
    const disconnectPromises: Promise<void>[] = [];
    for (const conn of this.connections.values()) {
      disconnectPromises.push(conn.disconnect());
    }
    await Promise.all(disconnectPromises);
    this.connections.clear();
  }

  private _createConnection(serverConfig: ServerConfig): Connection {
    if (serverConfig.transport === 'stdio') {
      return new StdioConnection(serverConfig, this.registry);
    }
    return new SseConnection(serverConfig);
  }
}
