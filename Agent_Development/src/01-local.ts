/**
 * STEP 1 — RUN AN AGENT LOCALLY
 * =============================
 * Goal: see the core ADK loop with no networking at all.
 *
 * Concepts introduced:
 *   - Runner        drives the agent: sends a message, streams back Events.
 *   - InMemoryRunner a Runner pre-wired with in-memory session/artifact/memory
 *                    services — perfect for local dev.
 *   - runEphemeral   convenience: runs a single turn without you having to
 *                    create a Session first (a throwaway session is used).
 *   - Event          each step the agent emits: a tool CALL, a tool RESULT,
 *                    and finally the model's TEXT answer.
 *
 * Run:  npm run local   (requires GEMINI_API_KEY in .env)
 */
import 'dotenv/config';
import { InMemoryRunner, getFunctionCalls, getFunctionResponses, isFinalResponse } from '@google/adk';
import type { Event } from '@google/adk';
import { buildWeatherAgent } from './agent.js';

function printEvent(event: Event): void {
  // 1. Did the model decide to call a tool this step?
  for (const call of getFunctionCalls(event)) {
    console.log(`  ↳ tool CALL   ${call.name}(${JSON.stringify(call.args ?? {})})`);
  }
  // 2. Did a tool return a result this step?
  for (const resp of getFunctionResponses(event)) {
    console.log(`  ↳ tool RESULT ${resp.name} => ${JSON.stringify(resp.response)}`);
  }
  // 3. Plain text the model produced this step.
  const text = event.content?.parts?.map((p) => p.text).filter(Boolean).join('');
  if (text) {
    const tag = isFinalResponse(event) ? 'FINAL' : 'text ';
    console.log(`  ↳ ${tag}       ${text}`);
  }
}

async function main(): Promise<void> {
  const agent = buildWeatherAgent();
  const runner = new InMemoryRunner({ agent });

  const question = "What's the weather in Berlin?";
  console.log(`\nUSER: ${question}\n`);

  // runEphemeral streams Events as the agent thinks -> calls tool -> answers.
  for await (const event of runner.runEphemeral({
    userId: 'u1',
    newMessage: { parts: [{ text: question }] },
  })) {
    printEvent(event);
  }

  console.log('\n[done] local run complete.\n');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
