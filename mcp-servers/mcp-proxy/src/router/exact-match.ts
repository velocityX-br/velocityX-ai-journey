import { BackendRegistry } from '../registry/backend-registry';

/**
 * Layer A: exact tool name match.
 * Delegates directly to BackendRegistry.getToolOwner which already
 * handles the healthy/unknown filter.
 */
export function exactMatch(toolName: string, registry: BackendRegistry): string | null {
  return registry.getToolOwner(toolName);
}
