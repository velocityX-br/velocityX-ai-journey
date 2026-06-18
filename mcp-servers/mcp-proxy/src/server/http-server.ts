import express, { Application, Request, Response, NextFunction } from 'express';
import http from 'http';
import { BackendRegistry } from '../registry/backend-registry';
import { Router } from '../router/router';
import { ConnectionPool } from '../pool/connection-pool';
import { ProxyConfig } from '../types';

export class HttpServer {
  /** The Express application — exposed so tests can pass it to supertest directly. */
  public readonly app: Application;

  private readonly registry: BackendRegistry;
  private readonly router: Router;
  private readonly pool: ConnectionPool;
  private readonly config: ProxyConfig;
  private httpServer: http.Server | null = null;

  constructor(
    registry: BackendRegistry,
    router: Router,
    pool: ConnectionPool,
    config: ProxyConfig,
  ) {
    this.registry = registry;
    this.router = router;
    this.pool = pool;
    this.config = config;

    this.app = express();
    this.app.use(express.json());

    this._registerRoutes();
  }

  // ── Lifecycle ───────────────────────────────────────────────────────────────

  /** Bind the HTTP server to `config.proxy.http_port`. */
  start(): Promise<void> {
    return new Promise((resolve, reject) => {
      const port = this.config.proxy.http_port;
      this.httpServer = this.app.listen(port, () => resolve());
      this.httpServer.once('error', reject);
    });
  }

  /** Gracefully close all open connections. */
  stop(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (!this.httpServer) {
        resolve();
        return;
      }
      this.httpServer.close((err) => {
        if (err) reject(err);
        else resolve();
      });
    });
  }

  // ── Route registration ──────────────────────────────────────────────────────

  private _registerRoutes(): void {
    // Health endpoint — always public (no auth required)
    this.app.get('/health', (_req: Request, res: Response) => {
      res.json({ status: 'ok', timestamp: new Date().toISOString() });
    });

    // Auth middleware applied to all routes AFTER /health
    if (this.config.proxy.auth) {
      const authConfig = this.config.proxy.auth;
      this.app.use((req: Request, res: Response, next: NextFunction) => {
        const rawKeys = process.env[authConfig.keys_env] ?? '';
        const validKeys = rawKeys
          .split(',')
          .map((k) => k.trim())
          .filter((k) => k.length > 0);

        const authHeader = req.headers['authorization'] ?? '';
        const match = authHeader.match(/^Bearer\s+(.+)$/i);
        const token = match ? match[1] : null;

        if (!token || !validKeys.includes(token)) {
          res.status(401).json({ error: 'Unauthorized' });
          return;
        }

        next();
      });
    }

    // GET /tools — list tools with optional LLM pruning via ?context=
    this.app.get('/tools', async (req: Request, res: Response) => {
      try {
        const context =
          typeof req.query['context'] === 'string' ? req.query['context'] : undefined;
        const tools = await this.router.getToolsList(context);
        res.json({ tools });
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : String(err);
        res.status(500).json({ error: message });
      }
    });

    // POST /tools/:name/call — invoke a specific tool
    this.app.post('/tools/:name/call', async (req: Request, res: Response) => {
      const toolName = req.params['name']!;
      try {
        const routeResult = this.router.routeToolCall(toolName);
        if (routeResult === null) {
          res.status(404).json({ error: `Tool not found: ${toolName}` });
          return;
        }
        const result = await this.pool.callTool(routeResult.serverName, toolName, req.body ?? {});
        res.json({ result });
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : String(err);
        res.status(500).json({ error: message });
      }
    });

    // GET /servers — list backend servers with health and tool count
    this.app.get('/servers', (_req: Request, res: Response) => {
      const servers = this.registry.getAllServers().map((s) => ({
        name: s.config.name,
        transport: s.config.transport,
        health: s.health,
        toolCount: s.config.tools ? s.config.tools.length : 0,
      }));
      res.json({ servers });
    });

    // POST /admin/reload — stub: returns 200 immediately
    this.app.post('/admin/reload', (_req: Request, res: Response) => {
      res.json({ message: 'Config reloaded' });
    });

    // Catch-all 404
    this.app.use((_req: Request, res: Response) => {
      res.status(404).json({ error: 'Not found' });
    });
  }
}
