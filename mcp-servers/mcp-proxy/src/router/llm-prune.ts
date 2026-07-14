import Anthropic from '@anthropic-ai/sdk';
import { ToolEntry, LLMPruneConfig } from '../types';

/**
 * Layer B: LLM semantic pruning (optional).
 *
 * When enabled and the tool count exceeds the configured threshold, sends the
 * tool descriptions and a client-supplied context string to an Anthropic model
 * and asks it to return only the relevant tool names.
 *
 * Degrades gracefully: on any error (API, parse, network) it logs a warning
 * and returns the original full tool list.
 */
export async function llmPrune(
  tools: ToolEntry[],
  context: string,
  config: LLMPruneConfig,
): Promise<ToolEntry[]> {
  // Short-circuits ────────────────────────────────────────────────────────────
  if (!config.enabled) return tools;
  if (tools.length <= config.threshold) return tools;

  // Build Anthropic client ────────────────────────────────────────────────────
  const apiKey = config.api_key_env ? process.env[config.api_key_env] : undefined;
  const client = new Anthropic({ apiKey });

  // Build prompt ──────────────────────────────────────────────────────────────
  const toolDescriptions = tools
    .map((t) => `- ${t.name}: ${t.description ?? '(no description)'}`)
    .join('\n');

  const prompt =
    `You are a tool selector for an AI assistant.\n\n` +
    `The user wants to: ${context}\n\n` +
    `Available tools:\n${toolDescriptions}\n\n` +
    `Return a JSON array of tool names (strings) that are relevant to the user's goal. ` +
    `Return ONLY the JSON array, no other text. Example: ["tool_a", "tool_b"]`;

  // Call LLM ──────────────────────────────────────────────────────────────────
  try {
    const model = config.model ?? 'claude-haiku-4-5-20251001';
    const response = await client.messages.create({
      model,
      max_tokens: 512,
      messages: [{ role: 'user', content: prompt }],
    });

    // Extract text from the first content block
    const firstBlock = response.content[0];
    if (!firstBlock || firstBlock.type !== 'text') {
      throw new Error('Unexpected response format from LLM');
    }

    const rawText = firstBlock.text.trim();

    // Parse JSON array of tool names
    const selectedNames: unknown = JSON.parse(rawText);
    if (
      !Array.isArray(selectedNames) ||
      !selectedNames.every((n) => typeof n === 'string')
    ) {
      throw new Error('LLM response is not a string array');
    }

    const nameSet = new Set<string>(selectedNames as string[]);
    return tools.filter((t) => nameSet.has(t.name));
  } catch (err) {
    console.warn(
      '[llmPrune] LLM pruning failed, returning full tool list.',
      err instanceof Error ? err.message : err,
    );
    return tools;
  }
}
