import * as fs from 'fs';
import * as yaml from 'js-yaml';
import { z } from 'zod';
import type { ProxyConfig } from '../types';

// Zod schema definitions

const ServerAuthSchema = z.object({
  type: z.enum(['bearer', 'api_key']),
  token_env: z.string().optional(),
});

const ServerConfigSchema = z.object({
  name: z.string(),
  transport: z.enum(['stdio', 'sse']),
  command: z.array(z.string()).optional(),
  url: z.string().optional(),
  auth: ServerAuthSchema.optional(),
  tools: z.array(z.string()).default([]),
  tags: z.array(z.string()).default([]),
}).superRefine((data, ctx) => {
  if (data.transport === 'stdio' && (!data.command || data.command.length === 0)) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['command'],
      message: 'command is required when transport is "stdio"',
    });
  }
  if (data.transport === 'sse' && !data.url) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['url'],
      message: 'url is required when transport is "sse"',
    });
  }
});

const LLMPruneConfigSchema = z.object({
  enabled: z.boolean().default(false),
  threshold: z.number().default(20),
  model: z.string().optional(),
  api_key_env: z.string().optional(),
});

const RoutingRuleSchema = z.object({
  serverName: z.string().optional(),
  tags: z.array(z.string()).optional(),
  toolPattern: z.string().optional(),
});

const RouterConfigSchema = z.object({
  llm_prune: LLMPruneConfigSchema.default({}),
  rules: z.array(RoutingRuleSchema).optional(),
});

const ProxyAuthConfigSchema = z.object({
  type: z.literal('api_key'),
  keys_env: z.string(),
});

const ProxySettingsSchema = z.object({
  mcp_port: z.number().default(3000),
  http_port: z.number().default(3001),
  auth: ProxyAuthConfigSchema.optional(),
});

const ProxyConfigSchema = z.object({
  servers: z.array(ServerConfigSchema),
  router: RouterConfigSchema.default({}),
  proxy: ProxySettingsSchema,
});

export function loadConfig(filePath: string): ProxyConfig {
  // 1. Check file existence
  if (!fs.existsSync(filePath)) {
    throw new Error(`Config file not found: ${filePath}`);
  }

  // 2. Read and parse YAML
  const raw = fs.readFileSync(filePath, 'utf8');
  let parsed: unknown;
  try {
    parsed = yaml.load(raw);
  } catch (err) {
    const yamlError = err instanceof Error ? err.message : String(err);
    throw new Error(`Invalid YAML in config file: ${filePath}\n${yamlError}`);
  }

  // 3. Validate with zod
  const result = ProxyConfigSchema.safeParse(parsed);
  if (!result.success) {
    const formatted = result.error.errors
      .map((e) => `  ${e.path.join('.')}: ${e.message}`)
      .join('\n');
    throw new Error(`Config validation error:\n${formatted}`);
  }

  // 4. Return typed config (zod output already has defaults applied)
  const config: ProxyConfig = result.data;
  return config;
}
