import * as path from 'path';
import { loadConfig } from './config/loader';
import { BackendRegistry } from './registry/backend-registry';
import { Router } from './router/router';
import { ConnectionPool } from './pool/connection-pool';
import { McpServer } from './server/mcp-server';
import { HttpServer } from './server/http-server';

async function main(): Promise<void> {
  const configPath = process.env['MCP_PROXY_CONFIG'] ?? path.resolve(process.cwd(), 'config.yaml');
  console.error(`[mcp-proxy] Loading config from: ${configPath}`);

  const config = loadConfig(configPath);
  console.error(`[mcp-proxy] Loaded ${config.servers.length} server(s)`);

  const registry = new BackendRegistry(config);
  const router = new Router(registry, config);
  const pool = new ConnectionPool(config, registry);

  const mcpServer = new McpServer(registry, router, pool, config);
  const httpServer = new HttpServer(registry, router, pool, config);

  // Graceful shutdown
  const shutdown = async () => {
    console.error('[mcp-proxy] Shutting down...');
    await Promise.all([
      mcpServer.stop(),
      httpServer.stop(),
      pool.disconnectAll(),
    ]);
    process.exit(0);
  };
  process.on('SIGINT', () => { void shutdown(); });
  process.on('SIGTERM', () => { void shutdown(); });

  await Promise.all([
    mcpServer.start(),
    httpServer.start(),
  ]);
}

main().catch((err) => {
  console.error('[mcp-proxy] Fatal error:', err);
  process.exit(1);
});
