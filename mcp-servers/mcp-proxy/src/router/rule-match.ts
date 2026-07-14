import { BackendRegistry } from '../registry/backend-registry';
import { RoutingRule } from '../types';

// Re-export so callers can import RoutingRule from either location.
export type { RoutingRule };

/**
 * Layer C: config rule-based matching.
 *
 * Iterates rules in order and returns the serverName of the first rule that
 * matches.  A rule can match via:
 *   1. toolPattern — regex applied to toolName
 *   2. tags        — the server that owns the tool must have ALL the listed tags
 *   3. serverName  — the named server must be healthy or unknown
 *
 * If a matching rule does not have a serverName but matched via tags, the
 * first healthy/unknown server whose tag set is a superset of the rule's tags
 * AND that has the tool (or has no tool list — "expose-all") is returned.
 */
export function ruleMatch(
  toolName: string,
  registry: BackendRegistry,
  rules: RoutingRule[],
): string | null {
  for (const rule of rules) {
    const matched = _ruleMatches(toolName, registry, rule);
    if (matched !== null) return matched;
  }
  return null;
}

// ── Internal helpers ──────────────────────────────────────────────────────────

function _ruleMatches(
  toolName: string,
  registry: BackendRegistry,
  rule: RoutingRule,
): string | null {
  // --- toolPattern check ---
  if (rule.toolPattern !== undefined) {
    const regex = new RegExp(rule.toolPattern);
    if (!regex.test(toolName)) return null;

    // Pattern matched.  If a serverName is given, verify that server is usable.
    if (rule.serverName !== undefined) {
      return _serverUsable(rule.serverName, registry) ? rule.serverName : null;
    }
    // No explicit serverName — find any healthy server that owns this tool.
    return registry.getToolOwner(toolName);
  }

  // --- tags check ---
  if (rule.tags !== undefined && rule.tags.length > 0) {
    // Find a healthy/unknown server that has ALL the required tags and owns the tool.
    const candidate = _findServerByTags(toolName, registry, rule.tags);
    if (candidate === null) return null;

    // If the rule also names a server, verify consistency.
    if (rule.serverName !== undefined) {
      // Only accept if the rule's serverName is the candidate and it is usable.
      return rule.serverName === candidate ? candidate : null;
    }
    return candidate;
  }

  // --- serverName-only check ---
  if (rule.serverName !== undefined) {
    return _serverUsable(rule.serverName, registry) ? rule.serverName : null;
  }

  // Empty rule — no criteria — never matches.
  return null;
}

/** True when the named server exists and its health is 'healthy' or 'unknown'. */
function _serverUsable(serverName: string, registry: BackendRegistry): boolean {
  const server = registry.getServer(serverName);
  if (!server) return false;
  return server.health !== 'unhealthy';
}

/**
 * Return the first healthy/unknown server name that:
 *   - has ALL the required tags
 *   - either explicitly lists the tool OR has an empty tool list (expose-all)
 */
function _findServerByTags(
  toolName: string,
  registry: BackendRegistry,
  requiredTags: string[],
): string | null {
  for (const server of registry.getAllServers()) {
    if (server.health === 'unhealthy') continue;

    // Tag superset check
    const serverTags = new Set(server.config.tags);
    const allTagsPresent = requiredTags.every((t) => serverTags.has(t));
    if (!allTagsPresent) continue;

    // Tool ownership check:
    // If the server has an explicit tool list the tool must be in it.
    // If the list is empty the server is "expose-all" — we accept it.
    const toolList = server.config.tools;
    if (toolList && toolList.length > 0) {
      if (!toolList.includes(toolName)) continue;
    }

    return server.config.name;
  }
  return null;
}
