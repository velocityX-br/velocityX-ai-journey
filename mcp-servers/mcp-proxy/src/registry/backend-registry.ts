import { ProxyConfig, BackendServer, ToolEntry, HealthStatus } from '../types';

export class BackendRegistry {
  // Map from server name → runtime state
  private readonly servers: Map<string, BackendServer> = new Map();

  // Map from tool name → server name (built from config; only for servers with explicit tool lists)
  private readonly toolOwnerMap: Map<string, string> = new Map();

  constructor(config: ProxyConfig) {
    for (const serverConfig of config.servers) {
      const server: BackendServer = {
        config: serverConfig,
        health: 'unknown' satisfies HealthStatus,
      };
      this.servers.set(serverConfig.name, server);

      // Populate tool → server mapping from the config's tool list.
      // When tools is undefined/empty the config says "expose all"; at registry
      // level we have no tool names to index — they'll be discovered on connect.
      // First-writer-wins: only map a tool name to the first server that declares it.
      if (serverConfig.tools && serverConfig.tools.length > 0) {
        for (const toolName of serverConfig.tools) {
          if (!this.toolOwnerMap.has(toolName)) {
            this.toolOwnerMap.set(toolName, serverConfig.name);
          }
        }
      }
    }

    // Detect duplicate tool names across servers and warn about them.
    const seenTools = new Map<string, string>(); // toolName → first serverName
    for (const server of config.servers) {
      if (!server.tools || server.tools.length === 0) continue;
      for (const toolName of server.tools) {
        if (seenTools.has(toolName)) {
          const firstServer = seenTools.get(toolName)!;
          console.warn(
            `[BackendRegistry] Duplicate tool name "${toolName}" found in servers ` +
            `"${firstServer}" and "${server.name}". ` +
            `"${firstServer}" will be used for routing.`
          );
        } else {
          seenTools.set(toolName, server.name);
        }
      }
    }
  }

  /** Return the runtime state of a specific server, or undefined if unknown. */
  getServer(name: string): BackendServer | undefined {
    return this.servers.get(name);
  }

  /** Return all registered servers. */
  getAllServers(): BackendServer[] {
    return Array.from(this.servers.values());
  }

  /**
   * Find which server owns a given tool name.
   * Only searches servers whose health is 'healthy' or 'unknown' (not 'unhealthy').
   * Returns server name, or null if not found / server is unhealthy.
   */
  getToolOwner(toolName: string): string | null {
    const serverName = this.toolOwnerMap.get(toolName);
    if (serverName === undefined) return null;

    const server = this.servers.get(serverName);
    if (!server) return null;

    // Exclude tools whose server has been marked unhealthy
    if (server.health === 'unhealthy') return null;

    return serverName;
  }

  /** Mark a server as unhealthy (e.g. after connection failure). No-op for unknown names. */
  markUnhealthy(name: string): void {
    const server = this.servers.get(name);
    if (server) {
      server.health = 'unhealthy';
    }
  }

  /** Mark a server as healthy (e.g. after successful reconnect). No-op for unknown names. */
  markHealthy(name: string): void {
    const server = this.servers.get(name);
    if (server) {
      server.health = 'healthy';
    }
  }

  /**
   * Get all tools from healthy (or unknown-health) servers only.
   * Tools from servers marked 'unhealthy' are excluded.
   */
  getHealthyTools(): ToolEntry[] {
    return this._buildToolEntries((server) => server.health !== 'unhealthy');
  }

  /**
   * Get all tools from all servers regardless of health status.
   */
  getAllTools(): ToolEntry[] {
    return this._buildToolEntries(() => true);
  }

  // ── Internal helpers ───────────────────────────────────────────────────────

  private _buildToolEntries(filter: (server: BackendServer) => boolean): ToolEntry[] {
    const entries: ToolEntry[] = [];

    for (const server of this.servers.values()) {
      if (!filter(server)) continue;

      const tools = server.config.tools;
      if (!tools || tools.length === 0) {
        // "expose all" server — no known tool names at this level.
        continue;
      }

      for (const toolName of tools) {
        entries.push({
          name: toolName,
          serverName: server.config.name,
          tags: server.config.tags,
          // description is not available from config; may be set later by connection pool
        });
      }
    }

    return entries;
  }
}
