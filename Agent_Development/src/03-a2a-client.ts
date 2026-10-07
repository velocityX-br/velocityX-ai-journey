/**
 * STEP 3 — CONSUME THE REMOTE AGENT OVER A2A (the two-agent handshake)
 * ====================================================================
 * Goal: from a SECOND process, talk to the agent served in step 2 — without
 * caring that it lives on another server. This is the heart of A2A dev.
 *
 * Concepts introduced:
 *   - RemoteA2AAgent   a local stand-in for a remote agent. You give it the
 *                      remote's AgentCard URL; it fetches the card, connects,
 *                      and forwards each turn over the A2A protocol.
 *   - The key insight: a `RemoteA2AAgent` IS a `BaseAgent`, so you drive it with
 *     the exact same Runner + runEphemeral loop as a local agent (step 1).
 *     Consuming a remote agent is therefore transparent.
 *
 * Prereq: run `npm run a2a:server` in another terminal first.
 * Run:    npm run a2a:client   (requires GEMINI_API_KEY — the REMOTE agent
 *                               calls Gemini on your behalf)
 */
import 'dotenv/config';
import {
  InMemoryRunner,
  RemoteA2AAgent,
  AGENT_CARD_PATH,
  getFunctionCalls,
  getFunctionResponses,
  isFinalResponse,
} from '@google/adk';
import type { Event } from '@google/adk';

const CARD_URL = `http://localhost:8080/${AGENT_CARD_PATH}`;

function printEvent(event: Event): void {
  for (const call of getFunctionCalls(event)) {
    console.log(`  ↳ tool CALL   ${call.name}(${JSON.stringify(call.args ?? {})})`);
  }
  for (const resp of getFunctionResponses(event)) {
    console.log(`  ↳ tool RESULT ${resp.name} => ${JSON.stringify(resp.response)}`);
  }
  const text = event.content?.parts?.map((p) => p.text).filter(Boolean).join('');
  if (text) {
    const tag = isFinalResponse(event) ? 'FINAL' : 'text ';
    console.log(`  ↳ ${tag}       ${text}`);
  }
}

async function main(): Promise<void> {
  // A local handle to the remote agent. Resolving the card is lazy (on first use).
  const remote = new RemoteA2AAgent({
    name: 'remote_weather',
    description: 'The weather agent, reached over A2A.',
    agentCard: CARD_URL,
  });

  // Drive the remote agent with the SAME runner loop used for a local agent.
  const runner = new InMemoryRunner({ agent: remote });

  const question = "What's the weather in Berlin?";
  console.log(`\n[client] connecting to A2A card: ${CARD_URL}`);
  console.log(`USER: ${question}\n`);

  for await (const event of runner.runEphemeral({
    userId: 'u1',
    newMessage: { parts: [{ text: question }] },
  })) {
    printEvent(event);
  }

  console.log('\n[done] answer produced via a remote A2A hop.\n');
}

main().catch((err) => {
  console.error('[client] failed. Is the server running (npm run a2a:server)?');
  console.error(err);
  process.exit(1);
});
