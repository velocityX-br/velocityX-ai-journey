import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { loadConfig } from '../../src/config/loader';

// Helper to write a temp YAML file and return its path
function writeTempYaml(content: string): string {
  const tmpDir = os.tmpdir();
  const tmpFile = path.join(tmpDir, `config-loader-test-${Date.now()}-${Math.random().toString(36).slice(2)}.yaml`);
  fs.writeFileSync(tmpFile, content, 'utf8');
  return tmpFile;
}

// Cleanup helper
function removeTempFile(filePath: string): void {
  try {
    fs.unlinkSync(filePath);
  } catch {
    // ignore
  }
}

const FULL_CONFIG_YAML = `
servers:
  - name: filesystem
    transport: stdio
    command: ["npx", "@modelcontextprotocol/server-filesystem", "/tmp"]
    tools: ["read_file", "write_file", "list_directory"]
    tags: ["file", "storage"]

  - name: github
    transport: sse
    url: "https://mcp.github.com/sse"
    auth:
      type: bearer
      token_env: GITHUB_TOKEN
    tools: ["search_repos", "create_issue", "get_pr"]
    tags: ["code", "vcs"]

router:
  llm_prune:
    enabled: true
    threshold: 30
    model: "claude-haiku-4-5"
    api_key_env: ANTHROPIC_API_KEY

proxy:
  mcp_port: 4000
  http_port: 4001
  auth:
    type: api_key
    keys_env: PROXY_API_KEYS
`;

const MINIMAL_CONFIG_YAML = `
servers:
  - name: myserver
    transport: stdio
    command: ["node", "server.js"]

proxy: {}
`;

describe('loadConfig', () => {
  describe('valid config with all fields', () => {
    let tmpFile: string;

    beforeAll(() => {
      tmpFile = writeTempYaml(FULL_CONFIG_YAML);
    });

    afterAll(() => {
      removeTempFile(tmpFile);
    });

    it('parses servers correctly', () => {
      const config = loadConfig(tmpFile);
      expect(config.servers).toHaveLength(2);
      expect(config.servers[0].name).toBe('filesystem');
      expect(config.servers[0].transport).toBe('stdio');
      expect(config.servers[0].command).toEqual(['npx', '@modelcontextprotocol/server-filesystem', '/tmp']);
      expect(config.servers[0].tools).toEqual(['read_file', 'write_file', 'list_directory']);
      expect(config.servers[0].tags).toEqual(['file', 'storage']);
    });

    it('parses SSE server with auth', () => {
      const config = loadConfig(tmpFile);
      const github = config.servers[1];
      expect(github.name).toBe('github');
      expect(github.transport).toBe('sse');
      expect(github.url).toBe('https://mcp.github.com/sse');
      expect(github.auth).toEqual({ type: 'bearer', token_env: 'GITHUB_TOKEN' });
    });

    it('parses router config', () => {
      const config = loadConfig(tmpFile);
      expect(config.router.llm_prune.enabled).toBe(true);
      expect(config.router.llm_prune.threshold).toBe(30);
      expect(config.router.llm_prune.model).toBe('claude-haiku-4-5');
      expect(config.router.llm_prune.api_key_env).toBe('ANTHROPIC_API_KEY');
    });

    it('rules are undefined when not specified in config', () => {
      const config = loadConfig(tmpFile);
      expect(config.router.rules).toBeUndefined();
    });

    it('parses proxy config', () => {
      const config = loadConfig(tmpFile);
      expect(config.proxy.mcp_port).toBe(4000);
      expect(config.proxy.http_port).toBe(4001);
      expect(config.proxy.auth).toEqual({ type: 'api_key', keys_env: 'PROXY_API_KEYS' });
    });
  });

  describe('valid config with minimal fields — defaults applied', () => {
    let tmpFile: string;

    beforeAll(() => {
      tmpFile = writeTempYaml(MINIMAL_CONFIG_YAML);
    });

    afterAll(() => {
      removeTempFile(tmpFile);
    });

    it('applies default threshold of 20', () => {
      const config = loadConfig(tmpFile);
      expect(config.router.llm_prune.threshold).toBe(20);
    });

    it('applies default router with llm_prune disabled when router is omitted', () => {
      const config = loadConfig(tmpFile);
      expect(config.router.llm_prune.enabled).toBe(false);
      expect(config.router.llm_prune.model).toBeUndefined();
      expect(config.router.llm_prune.api_key_env).toBeUndefined();
    });

    it('applies default tools of [] when not specified', () => {
      const config = loadConfig(tmpFile);
      expect(config.servers[0].tools).toEqual([]);
    });

    it('applies default mcp_port of 3000', () => {
      const config = loadConfig(tmpFile);
      expect(config.proxy.mcp_port).toBe(3000);
    });

    it('applies default http_port of 3001', () => {
      const config = loadConfig(tmpFile);
      expect(config.proxy.http_port).toBe(3001);
    });

    it('applies default tags of [] when not specified', () => {
      const config = loadConfig(tmpFile);
      expect(config.servers[0].tags).toEqual([]);
    });

    it('does not include proxy.auth when not specified', () => {
      const config = loadConfig(tmpFile);
      expect(config.proxy.auth).toBeUndefined();
    });
  });

  describe('router.rules loaded from config', () => {
    const RULES_CONFIG_YAML = `
servers:
  - name: github
    transport: sse
    url: "https://mcp.github.com/sse"
    tags: ["code", "vcs"]

router:
  rules:
    - toolPattern: "^create_.*"
      serverName: github
    - tags: ["file"]
      serverName: filesystem

proxy: {}
`;
    let tmpFile: string;

    beforeAll(() => {
      tmpFile = writeTempYaml(RULES_CONFIG_YAML);
    });

    afterAll(() => {
      removeTempFile(tmpFile);
    });

    it('loads router.rules and preserves all rule fields', () => {
      const config = loadConfig(tmpFile);
      expect(config.router.rules).toHaveLength(2);
      expect(config.router.rules![0]).toEqual({ toolPattern: '^create_.*', serverName: 'github' });
      expect(config.router.rules![1]).toEqual({ tags: ['file'], serverName: 'filesystem' });
    });
  });

  describe('error handling', () => {
    it('throws with correct message when file does not exist', () => {
      const missingPath = '/tmp/this-file-does-not-exist-ever-12345.yaml';
      expect(() => loadConfig(missingPath)).toThrow(
        `Config file not found: ${missingPath}`
      );
    });

    it('throws with correct message on invalid YAML', () => {
      const badYaml = `
servers:
  - name: bad
    transport: [unclosed
`;
      const tmpFile = writeTempYaml(badYaml);
      try {
        expect(() => loadConfig(tmpFile)).toThrow(
          new RegExp(`^Invalid YAML in config file: ${tmpFile.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`)
        );
      } finally {
        removeTempFile(tmpFile);
      }
    });

    it('throws with validation error message when required field is missing', () => {
      const invalidYaml = `
servers:
  - transport: stdio
    command: ["node", "server.js"]

router:
  llm_prune:
    enabled: false
    model: "claude-haiku-4-5"
    api_key_env: ANTHROPIC_API_KEY

proxy: {}
`;
      const tmpFile = writeTempYaml(invalidYaml);
      try {
        expect(() => loadConfig(tmpFile)).toThrow(/^Config validation error:/);
      } finally {
        removeTempFile(tmpFile);
      }
    });

    it('throws validation error for invalid transport value', () => {
      const invalidYaml = `
servers:
  - name: myserver
    transport: websocket
    command: ["node", "server.js"]

router:
  llm_prune:
    enabled: false
    model: "claude-haiku-4-5"
    api_key_env: ANTHROPIC_API_KEY

proxy: {}
`;
      const tmpFile = writeTempYaml(invalidYaml);
      try {
        expect(() => loadConfig(tmpFile)).toThrow(/^Config validation error:/);
      } finally {
        removeTempFile(tmpFile);
      }
    });

    it('throws validation error when transport is stdio and command is missing', () => {
      const invalidYaml = `
servers:
  - name: myserver
    transport: stdio

proxy: {}
`;
      const tmpFile = writeTempYaml(invalidYaml);
      try {
        expect(() => loadConfig(tmpFile)).toThrow(/command is required when transport is "stdio"/);
      } finally {
        removeTempFile(tmpFile);
      }
    });

    it('throws validation error when transport is stdio and command is empty array', () => {
      const invalidYaml = `
servers:
  - name: myserver
    transport: stdio
    command: []

proxy: {}
`;
      const tmpFile = writeTempYaml(invalidYaml);
      try {
        expect(() => loadConfig(tmpFile)).toThrow(/command is required when transport is "stdio"/);
      } finally {
        removeTempFile(tmpFile);
      }
    });

    it('throws validation error when transport is sse and url is missing', () => {
      const invalidYaml = `
servers:
  - name: myserver
    transport: sse

proxy: {}
`;
      const tmpFile = writeTempYaml(invalidYaml);
      try {
        expect(() => loadConfig(tmpFile)).toThrow(/url is required when transport is "sse"/);
      } finally {
        removeTempFile(tmpFile);
      }
    });
  });
});
