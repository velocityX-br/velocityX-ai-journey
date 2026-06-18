// Transport types
export type Transport = 'stdio' | 'sse';

// Server auth config
export interface ServerAuth {
  type: 'bearer' | 'api_key';
  token_env?: string;
}

// Server config (from config.yaml)
export interface ServerConfig {
  name: string;
  transport: Transport;
  command?: string[];      // stdio only
  url?: string;            // sse only
  auth?: ServerAuth;
  tools: string[];     // empty array means "expose all tools" (populated at connect time)
  tags: string[];
}

// Router config
export interface LLMPruneConfig {
  enabled: boolean;
  threshold: number;       // default 20
  model?: string;          // e.g. claude-haiku-4-5-20251001
  api_key_env?: string;
}

// Routing rule (used by Layer C / rule-match)
export interface RoutingRule {
  serverName?: string;   // route to a specific server
  tags?: string[];       // route if the tool's server has all these tags
  toolPattern?: string;  // route if tool name matches this regex
}

export interface RouterConfig {
  llm_prune: LLMPruneConfig;
  rules?: RoutingRule[];   // optional Layer C rules, evaluated in order
}

// Proxy config
export interface ProxyAuthConfig {
  type: 'api_key';
  keys_env: string;
}

export interface ProxyConfig {
  servers: ServerConfig[];
  router: RouterConfig;
  proxy: {
    mcp_port: number;      // default 3000
    http_port: number;     // default 3001
    auth?: ProxyAuthConfig;
  };
}

// Backend server runtime state
export type HealthStatus = 'healthy' | 'unhealthy' | 'unknown';

export interface BackendServer {
  config: ServerConfig;
  health: HealthStatus;
}

// Tool entry (runtime, from registry)
export interface ToolEntry {
  name: string;
  serverName: string;
  tags: string[];
  description?: string;
}

// Router result
export interface RouterResult {
  serverName: string;
  matchedBy: 'exact' | 'rule' | 'llm';
}

// Result returned by ConnectionPool.callTool — matches MCP tools/call response shape
export interface ToolCallResult {
  content?: Array<{ type: string; text?: string; [key: string]: unknown }>;
  isError?: boolean;
}
