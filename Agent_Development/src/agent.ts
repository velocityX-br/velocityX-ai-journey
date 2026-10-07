/**
 * SHARED AGENT FACTORY
 * --------------------
 * An *agent* pairs a model with instructions and a set of tools. `LlmAgent`
 * (also exported as `Agent`) is the standard ADK agent that lets the model
 * reason and call tools in a loop.
 *
 * We define the agent ONCE here and reuse the exact same factory in all three
 * teaching scripts. That is the whole point of the PoC: the *same* agent is
 *   1. run locally      (01-local.ts)
 *   2. served over A2A   (02-a2a-server.ts)
 *   3. consumed over A2A (03-a2a-client.ts, via a RemoteA2AAgent)
 *
 * Model: passing the string 'gemini-2.5-flash' is enough — @google/adk (via
 * @google/genai) reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment.
 * You could instead pass `new Gemini({ model, apiKey })` for explicit control.
 */
import { LlmAgent } from '@google/adk';
import { weatherTool, timeTool } from './tools.js';

export function buildWeatherAgent(): LlmAgent {
  return new LlmAgent({
    name: 'weather_agent',
    model: 'gemini-2.5-flash',
    description: 'Answers weather questions using a weather tool.',
    instruction: [
      'You are a helpful weather assistant.',
      'When the user asks about the weather in a place, call the get_weather tool.',
      'If the user asks for the time, call the get_time tool.',
      'Answer concisely in one or two sentences.',
    ].join(' '),
    tools: [weatherTool, timeTool],
  });
}
