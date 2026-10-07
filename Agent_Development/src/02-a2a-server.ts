/**
 * STEP 2 — SERVE THE AGENT OVER A2A
 * =================================
 * Goal: take the SAME agent from step 1 and expose it on the network using the
 * Agent2Agent (A2A) protocol, so *another* agent can call it.
 *
 * Concepts introduced:
 *   - toA2a(agent, opts)  wraps any ADK agent in an Express app that speaks A2A
 *                         (JSON-RPC + REST). It returns a Promise<Express.App>.
 *   - AgentCard           the agent's public "capability card" — a JSON document
 *                         served at the well-known URL. Clients fetch it to learn
 *                         the agent's name, description, skills and RPC endpoint.
 *   - AGENT_CARD_PATH     = ".well-known/agent-card.json" (the standard location).
 *
 * SECURITY — READ THIS:
 *   A2A is the production inter-agent surface: ANY caller that can reach this
 *   port can invoke the agent and its tools. `toA2a` therefore FAILS CLOSED —
 *   it throws unless you either pass an `authentication` UserBuilder (bearer /
 *   OIDC) OR explicitly set `allowUnauthenticated: true`.
 *
 *   Below we set `allowUnauthenticated: true` because this is a LOCAL, trusted
 *   PoC bound to localhost. A real deployment MUST instead pass
 *   `authentication: <UserBuilder>` (see `bearerTokenUserBuilder` from
 *   '@a2a-js/sdk/server/express') and remove this flag.
 *
 * Run:  npm run a2a:server   (no API key needed just to serve the card;
 *                             a key IS needed once a client actually invokes it)
 */
import 'dotenv/config';
import { toA2a, AGENT_CARD_PATH } from '@google/adk';
import { buildWeatherAgent } from './agent.js';

const HOST = 'localhost';
const PORT = 8080;

async function main(): Promise<void> {
  const agent = buildWeatherAgent();

  const app = await toA2a(agent, {
    host: HOST,
    port: PORT,
    // ⚠️ LOCAL DEV ONLY. Production must use `authentication: <UserBuilder>`
    // instead of this flag. See the security note in this file's header.
    allowUnauthenticated: true,
  });

  app.listen(PORT, () => {
    const cardUrl = `http://${HOST}:${PORT}/${AGENT_CARD_PATH}`;
    console.log(`\n[a2a] weather_agent is being served over A2A.`);
    console.log(`[a2a] AgentCard:  ${cardUrl}`);
    console.log(`[a2a] Try:        curl ${cardUrl}`);
    console.log(`[a2a] Then in another terminal:  npm run a2a:client\n`);
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
