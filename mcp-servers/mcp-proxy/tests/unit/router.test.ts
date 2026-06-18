import { BackendRegistry } from '../../src/registry/backend-registry';
import { ProxyConfig, ServerConfig, ToolEntry, LLMPruneConfig } from '../../src/types';
import { exactMatch } from '../../src/router/exact-match';
import { ruleMatch, RoutingRule } from '../../src/router/rule-match';
import { llmPrune } from '../../src/router/llm-prune';
import { Router } from '../../src/router/router';

// ── Helpers ──────────────────────────────────────────────────────────────────

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

function makeRegistry(servers: Partial<ServerConfig>[]): BackendRegistry {
  return new BackendRegistry(makeConfig(servers));
}

// ── 1 & 2. exactMatch ────────────────────────────────────────────────────────

describe('exactMatch', () => {
  it('returns the server name when the tool is found in the registry', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file', 'write_file'], tags: ['file'] },
      { name: 'github', tools: ['search_repos', 'create_issue'], tags: ['code'] },
    ]);

    expect(exactMatch('read_file', registry)).toBe('filesystem');
    expect(exactMatch('create_issue', registry)).toBe('github');
  });

  it('returns null when the tool is not found in the registry', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file'], tags: [] },
    ]);

    expect(exactMatch('no_such_tool', registry)).toBeNull();
  });

  it('returns null for a tool whose server is unhealthy', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file'], tags: [] },
    ]);
    registry.markUnhealthy('filesystem');

    expect(exactMatch('read_file', registry)).toBeNull();
  });
});

// ── 3-6. ruleMatch ───────────────────────────────────────────────────────────

describe('ruleMatch', () => {
  it('returns the correct server when toolPattern regex matches the tool name', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file', 'write_file'], tags: ['file'] },
      { name: 'github', tools: ['search_repos'], tags: ['code'] },
    ]);

    const rules: RoutingRule[] = [
      { toolPattern: '^read_', serverName: 'filesystem' },
    ];

    expect(ruleMatch('read_file', registry, rules)).toBe('filesystem');
  });

  it('returns null when toolPattern does not match', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file'], tags: ['file'] },
    ]);

    const rules: RoutingRule[] = [
      { toolPattern: '^write_', serverName: 'filesystem' },
    ];

    expect(ruleMatch('read_file', registry, rules)).toBeNull();
  });

  it('returns the correct server when tags all match', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file'], tags: ['file', 'storage'] },
      { name: 'github', tools: ['search_repos'], tags: ['code', 'vcs'] },
    ]);

    // Rule: route tools whose server has both 'code' and 'vcs' tags
    const rules: RoutingRule[] = [
      { tags: ['code', 'vcs'], serverName: 'github' },
    ];

    expect(ruleMatch('search_repos', registry, rules)).toBe('github');
  });

  it('returns null when only a subset of tags match', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file'], tags: ['file'] },
    ]);

    const rules: RoutingRule[] = [
      { tags: ['file', 'storage'], serverName: 'filesystem' },
    ];

    // filesystem has only ['file'], not ['file', 'storage']
    expect(ruleMatch('read_file', registry, rules)).toBeNull();
  });

  it('returns the server when serverName direct match is healthy/unknown', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file'], tags: ['file'] },
    ]);

    const rules: RoutingRule[] = [
      { serverName: 'filesystem' },
    ];

    expect(ruleMatch('anything', registry, rules)).toBe('filesystem');
  });

  it('returns null when serverName direct match is unhealthy', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file'], tags: ['file'] },
    ]);
    registry.markUnhealthy('filesystem');

    const rules: RoutingRule[] = [
      { serverName: 'filesystem' },
    ];

    expect(ruleMatch('read_file', registry, rules)).toBeNull();
  });

  it('returns null when no rules match', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file'], tags: ['file'] },
    ]);

    const rules: RoutingRule[] = [
      { toolPattern: '^write_', serverName: 'filesystem' },
    ];

    expect(ruleMatch('search_repos', registry, rules)).toBeNull();
  });

  it('returns null for an empty rules array', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file'], tags: [] },
    ]);

    expect(ruleMatch('read_file', registry, [])).toBeNull();
  });

  it('uses first matching rule when multiple rules could match', () => {
    const registry = makeRegistry([
      { name: 'filesystem', tools: ['read_file'], tags: ['file'] },
      { name: 'backup', tools: ['read_file_backup'], tags: ['file'] },
    ]);

    const rules: RoutingRule[] = [
      { toolPattern: 'read', serverName: 'filesystem' },
      { toolPattern: 'read', serverName: 'backup' },
    ];

    expect(ruleMatch('read_file', registry, rules)).toBe('filesystem');
  });
});

// ── 7-10. llmPrune ───────────────────────────────────────────────────────────

describe('llmPrune', () => {
  const makeTools = (names: string[]): ToolEntry[] =>
    names.map((name, i) => ({
      name,
      serverName: `server-${i}`,
      tags: [],
      description: `Description for ${name}`,
    }));

  it('returns tools unchanged when disabled', async () => {
    const tools = makeTools(['tool1', 'tool2', 'tool3']);
    const config: LLMPruneConfig = { enabled: false, threshold: 2 };

    const result = await llmPrune(tools, 'some context', config);
    expect(result).toBe(tools); // same reference — no copy
  });

  it('returns tools unchanged when at or below threshold', async () => {
    const tools = makeTools(['tool1', 'tool2']);
    const config: LLMPruneConfig = { enabled: true, threshold: 2 };

    const result = await llmPrune(tools, 'some context', config);
    expect(result).toBe(tools);
  });

  it('returns the full tool list when the LLM call fails (graceful degradation)', async () => {
    const mockCtor = jest.fn().mockImplementation(() => ({
      messages: {
        create: jest.fn().mockRejectedValue(new Error('Network error')),
      },
    }));
    jest.doMock('@anthropic-ai/sdk', () => ({
      __esModule: true,
      default: mockCtor,
      Anthropic: mockCtor,
    }));
    jest.resetModules();

    const { llmPrune: freshLlmPrune } = await import('../../src/router/llm-prune');

    const tools = makeTools(['tool1', 'tool2', 'tool3', 'tool4', 'tool5']);
    const config: LLMPruneConfig = {
      enabled: true,
      threshold: 2,
      model: 'claude-haiku-4-5-20251001',
      api_key_env: 'ANTHROPIC_API_KEY',
    };

    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const result = await freshLlmPrune(tools, 'do file operations', config);

    expect(result).toEqual(tools);
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
    jest.resetModules();
  });

  it('returns pruned subset when LLM call succeeds', async () => {
    const mockCreate = jest.fn().mockResolvedValue({
      content: [{ type: 'text', text: '["tool1", "tool3"]' }],
    });
    const mockCtor = jest.fn().mockImplementation(() => ({
      messages: { create: mockCreate },
    }));

    jest.doMock('@anthropic-ai/sdk', () => ({
      __esModule: true,
      default: mockCtor,
      Anthropic: mockCtor,
    }));

    // Re-require the module to pick up the mock
    jest.resetModules();
    const { llmPrune: freshLlmPrune } = await import('../../src/router/llm-prune');

    const tools = makeTools(['tool1', 'tool2', 'tool3', 'tool4', 'tool5']);
    const config: LLMPruneConfig = {
      enabled: true,
      threshold: 2,
      model: 'claude-haiku-4-5-20251001',
      api_key_env: 'ANTHROPIC_API_KEY',
    };

    const result = await freshLlmPrune(tools, 'do file operations', config);

    expect(result.map((t) => t.name)).toEqual(['tool1', 'tool3']);
    jest.resetModules();
  });
});

// ── 11-14. Router ─────────────────────────────────────────────────────────────

describe('Router', () => {
  it('exact match takes priority over rule match', () => {
    const config = makeConfig([
      { name: 'filesystem', tools: ['read_file', 'write_file'], tags: ['file'] },
      { name: 'other', tools: ['other_tool'], tags: ['other'] },
    ]);

    const registry = new BackendRegistry(config);
    const router = new Router(registry, config);

    const result = router.routeToolCall('read_file');
    expect(result).not.toBeNull();
    expect(result!.serverName).toBe('filesystem');
    expect(result!.matchedBy).toBe('exact');
  });

  it('falls through to rule match when exact match fails', () => {
    const config = makeConfig([
      { name: 'filesystem', tools: ['read_file'], tags: ['file'] },
    ]);
    // Inject a routing rule that should catch 'write_file' (not in exact map)
    const proxyConfig: ProxyConfig = {
      ...config,
      router: {
        ...config.router,
        rules: [{ toolPattern: '^write_', serverName: 'filesystem' }],
      },
    };

    const registry = new BackendRegistry(proxyConfig);
    const router = new Router(registry, proxyConfig);

    const result = router.routeToolCall('write_file');
    expect(result).not.toBeNull();
    expect(result!.serverName).toBe('filesystem');
    expect(result!.matchedBy).toBe('rule');
  });

  it('returns null when neither exact nor rule match succeeds', () => {
    const config = makeConfig([
      { name: 'filesystem', tools: ['read_file'], tags: ['file'] },
    ]);

    const registry = new BackendRegistry(config);
    const router = new Router(registry, config);

    const result = router.routeToolCall('totally_unknown_tool');
    expect(result).toBeNull();
  });

  it('getToolsList returns healthy tools and applies LLM pruning when configured', async () => {
    const tools5 = ['t1', 'read_file', 't3', 't4', 't5'];
    const config = makeConfig([
      { name: 'filesystem', tools: tools5, tags: ['file'] },
    ]);

    // Enable pruning with threshold=3 so 5 tools triggers it
    const pruningConfig: ProxyConfig = {
      ...config,
      router: {
        llm_prune: {
          enabled: true,
          threshold: 3,
          model: 'claude-haiku-4-5-20251001',
          api_key_env: 'ANTHROPIC_API_KEY',
        },
      },
    };

    const mockCreate = jest.fn().mockResolvedValue({
      content: [{ type: 'text', text: '["read_file", "t3"]' }],
    });
    const mockCtor = jest.fn().mockImplementation(() => ({
      messages: { create: mockCreate },
    }));
    jest.doMock('@anthropic-ai/sdk', () => ({
      __esModule: true,
      default: mockCtor,
      Anthropic: mockCtor,
    }));
    jest.resetModules();

    const { Router: FreshRouter } = await import('../../src/router/router');
    const { BackendRegistry: FreshRegistry } = await import('../../src/registry/backend-registry');

    const registry = new FreshRegistry(pruningConfig);
    const router = new FreshRouter(registry, pruningConfig);

    const result = await router.getToolsList('read and process a file');
    expect(result.map((t) => t.name)).toEqual(['read_file', 't3']);

    jest.resetModules();
  });

  it('getToolsList returns full healthy tools when LLM pruning is disabled', async () => {
    const config = makeConfig([
      { name: 'filesystem', tools: ['read_file', 'write_file'], tags: ['file'] },
    ]);

    const registry = new BackendRegistry(config);
    const router = new Router(registry, config);

    const result = await router.getToolsList();
    expect(result.map((t) => t.name).sort()).toEqual(['read_file', 'write_file'].sort());
  });
});
