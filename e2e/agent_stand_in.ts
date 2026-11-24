// Stands in for AgentCore while the browser tests exercise the backend and dashboard.
import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { TEST_BACKEND } from "./addresses.ts";
import { agentHeaders, TEST_AGENT_KEY } from "./agent_credentials.ts";

const backend = process.env.UNDERCOVER_API ?? TEST_BACKEND;
const resumed: string[] = [];
const starts: unknown[] = [];
const busyReason = "A dispatch or evaluation is already running";
const accepted = { accepted: true };
let busy = false;

interface Invocation {
  operation: string;
  claim_id: string;
  round_id: string;
  proposal_id: string;
  handler: Record<string, unknown>;
  decision: string;
  reason: string | null;
  agents: number;
  set_name: string;
  tiny: boolean;
}

function reply(response: ServerResponse, status: number, body: unknown) {
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
}

async function forwardRound(response: ServerResponse, invocation: Invocation) {
  const { operation, round_id, proposal_id, handler, decision, reason } =
    invocation;
  if (busy && operation !== "stop_round") {
    reply(response, 200, { refused: busyReason });
    return;
  }

  const rounds = "/api/improver/rounds";
  let path = rounds;
  let body: object = handler;
  if (operation === "stop_round") {
    path = `${rounds}/${round_id}/finish`;
    body = { stopped: true, reason: "Stopped by Dana Whitfield" };
  } else if (operation === "decide_proposal") {
    path = `${rounds}/${round_id}/proposals/${proposal_id}/decision`;
    body = { handler, decision, reason };
  }
  const result = await fetch(backend + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...agentHeaders },
    body: JSON.stringify(body),
  });
  const value = (await result.json()) as { round_id?: string; detail?: string };
  if (result.status === 409) {
    reply(response, 200, { refused: value.detail });
  } else {
    reply(
      response,
      result.ok ? 200 : result.status,
      result.ok ? { ...accepted, round_id: value.round_id } : value,
    );
  }
}

async function invoke(request: IncomingMessage, response: ServerResponse) {
  let body = "";
  for await (const chunk of request) body += String(chunk);
  const input = JSON.parse(body) as Invocation;
  if (input.operation === "health") {
    reply(response, 200, { healthy: true });
  } else if (request.headers.authorization !== `Bearer ${TEST_AGENT_KEY}`) {
    reply(response, 401, { error: "A valid bearer key is required" });
  } else if (input.operation === "resume_claim") {
    resumed.push(input.claim_id);
    reply(response, 200, accepted);
  } else if (
    input.operation === "start_dispatch" ||
    input.operation === "start_evaluation"
  ) {
    if (busy) {
      reply(response, 200, { refused: busyReason });
      return;
    }
    busy = true;
    const selection =
      input.operation === "start_evaluation"
        ? { set_name: input.set_name, tiny: input.tiny }
        : {};
    starts.push({ concurrency: input.agents, ...selection });
    reply(response, 200, accepted);
  } else {
    await forwardRound(response, input);
  }
}

createServer((request, response) => {
  if (request.method === "POST" && request.url === "/invocations") {
    void invoke(request, response).catch(() => {
      reply(response, 503, { error: "The stand-in could not take the work" });
    });
  } else if (request.url === "/api/starts") {
    reply(response, 200, starts);
  } else if (request.method === "POST" && request.url === "/api/release") {
    busy = false;
    reply(response, 200, { status: "released" });
  } else if (request.url === "/api/resumed") {
    reply(response, 200, resumed);
  } else if (request.url === "/ping") {
    reply(response, 200, { status: busy ? "HealthyBusy" : "Healthy" });
  } else {
    reply(response, 404, { detail: "Not found" });
  }
}).listen(Number(process.env.PORT), "127.0.0.1");
