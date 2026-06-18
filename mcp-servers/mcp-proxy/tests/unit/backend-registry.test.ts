import { BackendRegistry } from '../../src/registry/backend-registry';
import { ProxyConfig, ServerConfig } from '../../src/types';

// Helper to build a minimal ProxyConfig
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
      llm_prune: {
        enabled: false,
        threshold: 20,
        model: 'claude-haiku-4-5-20251001',
        api_key_env: 'ANTHROPIC_API_KEY',
      },
    },
    proxy: {
      mcp_port: 3000,
      http_port: 3001,
    },
  };
}

describe('BackendRegistry', () => {
  // ─── 1. Constructor ────────────────────────────────────────────────────────
  describe('constructor', () => {
    it('registers all servers with unknown health status', () => {
      const config = makeConfig([
        { name: 'server-a', tools: ['tool1', 'tool2'], tags: ['cat-a'] },
        { name: 'server-b', tools: ['tool3'], tags: ['cat-b'] },
      ]);

      const registry = new BackendRegistry(config);
      const all = registry.getAllServers();

      expect(all).toHaveLength(2);
      for (const server of all) {
        expect(server.health).toBe('unknown');
      }
    });

    it('stores the original ServerConfig on each BackendServer', () => {
      const config = makeConfig([
        { name: 'my-server', transport: 'sse', url: 'http://localhost:9000', tools: ['t1'], tags: ['x'] },
      ]);

      const registry = new BackendRegistry(config);
      const server = registry.getServer('my-server');

      expect(server).toBeDefined();
      expect(server!.config.url).toBe('http://localhost:9000');
      expect(server!.config.transport).toBe('sse');
    });
  });

  // ─── 2. getServer ──────────────────────────────────────────────────────────
  describe('getServer', () => {
    it('returns the correct BackendServer for a known name', () => {
      const config = makeConfig([{ name: 'alpha', tools: ['tool-alpha'], tags: [] }]);
      const registry = new BackendRegistry(config);

      const server = registry.getServer('alpha');
      expect(server).toBeDefined();
      expect(server!.config.name).toBe('alpha');
    });

    it('returns undefined for an unknown name', () => {
      const config = makeConfig([{ name: 'alpha', tools: ['tool-alpha'], tags: [] }]);
      const registry = new BackendRegistry(config);

      expect(registry.getServer('nonexistent')).toBeUndefined();
    });
  });

  // ─── 3. getToolOwner — found ───────────────────────────────────────────────
  describe('getToolOwner', () => {
    it('returns the correct server name for a known tool', () => {
      const config = makeConfig([
        { name: 'server-a', tools: ['tool1', 'tool2'], tags: [] },
        { name: 'server-b', tools: ['tool3'], tags: [] },
      ]);
      const registry = new BackendRegistry(config);

      expect(registry.getToolOwner('tool1')).toBe('server-a');
      expect(registry.getToolOwner('tool3')).toBe('server-b');
    });

    // ─── 4. getToolOwner — not found ────────────────────────────────────────
    it('returns null for an unknown tool', () => {
      const config = makeConfig([{ name: 'server-a', tools: ['tool1'], tags: [] }]);
      const registry = new BackendRegistry(config);

      expect(registry.getToolOwner('no-such-tool')).toBeNull();
    });

    // ─── 5. getToolOwner — unhealthy server ─────────────────────────────────
    it('returns null for a tool whose server is unhealthy', () => {
      const config = makeConfig([
        { name: 'server-a', tools: ['tool1'], tags: [] },
      ]);
      const registry = new BackendRegistry(config);

      registry.markUnhealthy('server-a');
      expect(registry.getToolOwner('tool1')).toBeNull();
    });
  });

  // ─── 6. markUnhealthy + getHealthyTools ───────────────────────────────────
  describe('markUnhealthy', () => {
    it('excludes an unhealthy server\'s tools from getHealthyTools', () => {
      const config = makeConfig([
        { name: 'server-a', tools: ['tool1', 'tool2'], tags: ['a'] },
        { name: 'server-b', tools: ['tool3'], tags: ['b'] },
      ]);
      const registry = new BackendRegistry(config);

      registry.markUnhealthy('server-a');

      const healthy = registry.getHealthyTools();
      const names = healthy.map((t) => t.name);

      expect(names).not.toContain('tool1');
      expect(names).not.toContain('tool2');
      expect(names).toContain('tool3');
    });

    it('sets server health to unhealthy', () => {
      const config = makeConfig([{ name: 'server-a', tools: ['t1'], tags: [] }]);
      const registry = new BackendRegistry(config);

      registry.markUnhealthy('server-a');

      expect(registry.getServer('server-a')!.health).toBe('unhealthy');
    });

    it('is a no-op for unknown server names (does not throw)', () => {
      const config = makeConfig([{ name: 'server-a', tools: ['t1'], tags: [] }]);
      const registry = new BackendRegistry(config);

      expect(() => registry.markUnhealthy('ghost')).not.toThrow();
    });
  });

  // ─── 7. markHealthy after markUnhealthy ───────────────────────────────────
  describe('markHealthy', () => {
    it('restores tools to getHealthyTools after re-marking healthy', () => {
      const config = makeConfig([
        { name: 'server-a', tools: ['tool1'], tags: ['a'] },
      ]);
      const registry = new BackendRegistry(config);

      registry.markUnhealthy('server-a');
      expect(registry.getHealthyTools().map((t) => t.name)).not.toContain('tool1');

      registry.markHealthy('server-a');
      expect(registry.getHealthyTools().map((t) => t.name)).toContain('tool1');
    });

    it('sets server health to healthy', () => {
      const config = makeConfig([{ name: 'server-a', tools: ['t1'], tags: [] }]);
      const registry = new BackendRegistry(config);

      registry.markUnhealthy('server-a');
      registry.markHealthy('server-a');

      expect(registry.getServer('server-a')!.health).toBe('healthy');
    });

    it('is a no-op for unknown server names (does not throw)', () => {
      const config = makeConfig([{ name: 'server-a', tools: ['t1'], tags: [] }]);
      const registry = new BackendRegistry(config);

      expect(() => registry.markHealthy('ghost')).not.toThrow();
    });
  });

  // ─── 8. getAllTools ────────────────────────────────────────────────────────
  describe('getAllTools', () => {
    it('returns tools from all servers including unhealthy ones', () => {
      const config = makeConfig([
        { name: 'server-a', tools: ['tool1', 'tool2'], tags: ['a'] },
        { name: 'server-b', tools: ['tool3'], tags: ['b'] },
      ]);
      const registry = new BackendRegistry(config);

      registry.markUnhealthy('server-a');

      const all = registry.getAllTools();
      const names = all.map((t) => t.name);

      expect(names).toContain('tool1');
      expect(names).toContain('tool2');
      expect(names).toContain('tool3');
    });

    it('populates serverName and tags on each ToolEntry', () => {
      const config = makeConfig([
        { name: 'server-a', tools: ['tool1'], tags: ['tag-x', 'tag-y'] },
      ]);
      const registry = new BackendRegistry(config);

      const entries = registry.getAllTools();
      expect(entries).toHaveLength(1);
      expect(entries[0].serverName).toBe('server-a');
      expect(entries[0].tags).toEqual(['tag-x', 'tag-y']);
    });
  });

  // ─── 9. Server with no tools (tools: undefined) ────────────────────────────
  describe('server with no tools listed', () => {
    it('handles servers with tools: undefined gracefully (no crash)', () => {
      const config = makeConfig([
        { name: 'no-tools-server', tags: ['misc'] },
        // tools is intentionally omitted
      ]);

      expect(() => new BackendRegistry(config)).not.toThrow();
    });

    it('server with no tools contributes zero ToolEntries to getAllTools', () => {
      const config = makeConfig([{ name: 'no-tools-server', tags: [] }]);
      const registry = new BackendRegistry(config);

      expect(registry.getAllTools()).toHaveLength(0);
    });

    it('server with no tools contributes zero ToolEntries to getHealthyTools', () => {
      const config = makeConfig([{ name: 'no-tools-server', tags: [] }]);
      const registry = new BackendRegistry(config);

      expect(registry.getHealthyTools()).toHaveLength(0);
    });

    it('getToolOwner returns null for any tool name when server has no tools listed', () => {
      const config = makeConfig([{ name: 'no-tools-server', tags: [] }]);
      const registry = new BackendRegistry(config);

      expect(registry.getToolOwner('anything')).toBeNull();
    });
  });

  // ─── 10. Duplicate tool name collision ────────────────────────────────────
  describe('duplicate tool names across servers', () => {
    it('first-server wins when two servers declare the same tool name', () => {
      const config = makeConfig([
        { name: 'server-a', tools: ['shared_tool', 'tool-a'], tags: [] },
        { name: 'server-b', tools: ['shared_tool', 'tool-b'], tags: [] },
      ]);
      const registry = new BackendRegistry(config);

      expect(registry.getToolOwner('shared_tool')).toBe('server-a');
      expect(registry.getToolOwner('tool-a')).toBe('server-a');
      expect(registry.getToolOwner('tool-b')).toBe('server-b');
    });

    it('emits a console.warn for each duplicated tool name', () => {
      const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
      const config = makeConfig([
        { name: 'server-a', tools: ['shared_tool'], tags: [] },
        { name: 'server-b', tools: ['shared_tool'], tags: [] },
      ]);
      new BackendRegistry(config);

      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining('"shared_tool"')
      );
      warnSpy.mockRestore();
    });
  });

  // ─── ToolEntry shape ───────────────────────────────────────────────────────
  describe('ToolEntry shape', () => {
    it('description is undefined when not set (not in config)', () => {
      const config = makeConfig([{ name: 'server-a', tools: ['tool1'], tags: [] }]);
      const registry = new BackendRegistry(config);

      const entry = registry.getAllTools().find((t) => t.name === 'tool1');
      expect(entry).toBeDefined();
      expect(entry!.description).toBeUndefined();
    });
  });
});
