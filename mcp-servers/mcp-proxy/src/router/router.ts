import { BackendRegistry } from '../registry/backend-registry';
import { ProxyConfig, RouterResult, ToolEntry } from '../types';
import { exactMatch } from './exact-match';
import { ruleMatch } from './rule-match';
import { llmPrune } from './llm-prune';

/**
 * Orchestrates all three router layers in priority order:
 *   Layer A (exact match) → Layer C (rule match) → Layer B (LLM prune, tools/list only)
 */
export class Router {
  private readonly registry: BackendRegistry;
  private readonly config: ProxyConfig;

  constructor(registry: BackendRegistry, config: ProxyConfig) {
    this.registry = registry;
    this.config = config;
  }

  /**
   * Route a tools/call request to the correct backend server.
   *
   * Returns a RouterResult describing which server handles the tool and which
   * layer matched it, or null if no server can be found.
   */
  routeToolCall(toolName: string): RouterResult | null {
    // Layer A — exact match
    const exactServer = exactMatch(toolName, this.registry);
    if (exactServer !== null) {
      return { serverName: exactServer, matchedBy: 'exact' };
    }

    // Layer C — rule match
    const rules = this.config.router.rules ?? [];
    const ruleServer = ruleMatch(toolName, this.registry, rules);
    if (ruleServer !== null) {
      return { serverName: ruleServer, matchedBy: 'rule' };
    }

    return null;
  }

  /**
   * Return the list of tools to expose for a tools/list response.
   *
   * Applies Layer B (LLM pruning) when:
   *   - pruning is enabled in config
   *   - the total tool count exceeds the threshold
   *   - a context string is provided by the caller
   *
   * Falls back to the full healthy tool list on LLM failure.
   */
  async getToolsList(context?: string): Promise<ToolEntry[]> {
    const tools = this.registry.getHealthyTools();

    const pruneConfig = this.config.router.llm_prune;
    if (
      pruneConfig.enabled &&
      tools.length > pruneConfig.threshold &&
      context !== undefined
    ) {
      return llmPrune(tools, context, pruneConfig);
    }

    return tools;
  }
}
