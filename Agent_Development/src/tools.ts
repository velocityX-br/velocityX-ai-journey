/**
 * STEP 0 (shared): TOOLS
 * ----------------------
 * A *tool* is just a typed function the LLM is allowed to call. In @google/adk
 * a tool is a `FunctionTool`:
 *   - `name`        the identifier the model uses when it decides to call it
 *   - `description` natural-language hint the model reads to decide *when* to call it
 *   - `parameters`  a **zod** schema — the ADK converts this to the JSON Schema
 *                   that is shown to the model, and validates the model's args
 *                   before your `execute` runs
 *   - `execute`     your plain async function; its typed `input` is inferred
 *                   from the zod schema above
 *
 * Note: `FunctionTool`'s generic inference accepts a zod v3 or v4 object
 * schema. We use zod v4 (`zod/v4`), matching the zod version @google/adk
 * itself depends on, so the schema types line up and `execute`'s `input` is
 * fully typed (e.g. `input.city` is a string).
 */
import { FunctionTool } from '@google/adk';
import { z } from 'zod/v4';

// --- mock "backend" data so the PoC runs with zero external services --------
const MOCK_WEATHER: Record<string, { tempC: number; condition: string }> = {
  berlin: { tempC: 18, condition: 'Partly cloudy' },
  london: { tempC: 14, condition: 'Light rain' },
  'san francisco': { tempC: 17, condition: 'Foggy' },
  tokyo: { tempC: 24, condition: 'Clear' },
};

/**
 * get_weather(city) -> { city, tempC, condition }
 * Teaches: the model picks this tool when the user asks about weather; the
 * `city` argument is validated against the zod schema before `execute` runs.
 */
export const weatherTool = new FunctionTool({
  name: 'get_weather',
  description: 'Get the current weather for a given city.',
  parameters: z.object({
    city: z.string().describe('The city to get the weather for, e.g. "Berlin".'),
  }),
  execute: async ({ city }) => {
    const key = city.trim().toLowerCase();
    const hit = MOCK_WEATHER[key];
    if (!hit) {
      return { city, found: false, note: 'No mock data for this city.' };
    }
    return { city, found: true, tempC: hit.tempC, condition: hit.condition };
  },
});

/**
 * get_time() -> { iso, note }
 * A second, no-argument tool so you can see the model choose between tools.
 */
export const timeTool = new FunctionTool({
  name: 'get_time',
  description: 'Get the current server time as an ISO-8601 string.',
  parameters: z.object({}),
  execute: async () => {
    return { iso: new Date().toISOString(), note: 'server local clock' };
  },
});
