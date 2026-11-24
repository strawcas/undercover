// Stand-in for AgentCore in end-to-end tests. Work is already committed by the app.
import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { TEST_BACKEND } from "./addresses.ts";
import { agentHeaders, TEST_AGENT_KEY } from "./agent_credentials.ts";

const backend = process.env.UNDERCOVER_API ?? TEST_BACKEND;
const resumed: string[] = [];
const acceptedRuns = new Set<string>();
const starts: unknown[] = [];
const accepted = { accepted: true };

interface Invocation {
  operation: string;
  claim_id: string;
  run_id: string;
  round_id: string;
  proposal_id: string;
  reason: string;
}

function reply(response: ServerResponse, status: number, body: unknown) {
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
}

async function input(request: IncomingMessage) {
  let body = "";
  for await (const chunk of request) body += String(chunk);
  return JSON.parse(body) as Invocation;
}

async function appPost(path: string, body?: object) {
  return fetch(backend + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...agentHeaders },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function acceptWork(response: ServerResponse, request: Invocation) {
  if (request.operation === "resume_claim") {
    resumed.push(request.claim_id);
    reply(response, 200, accepted);
    return;
  }
  const run = request.operation === "work_run";
  const result = run
    ? await appPost("/api/runs/" + request.run_id + "/take")
    : await appPost("/api/improver/rounds/" + request.round_id + "/take", {
        proposal_id:
          request.operation === "carry_round_on" ? request.proposal_id : null,
      });
  if (!result.ok) {
    const value = (await result.json()) as { detail: string };
    reply(response, 200, { refused: value.detail });
    return;
  }
  if (run) {
    const value = (await result.json()) as {
      kind: string;
      concurrency: number;
      set_name: string;
      tiny: boolean;
    };
    starts.push(
      value.kind === "dispatch"
        ? { concurrency: value.concurrency }
        : {
            concurrency: value.concurrency,
            set_name: value.set_name,
            tiny: value.tiny,
          },
    );
    acceptedRuns.add(request.run_id);
  }
  reply(response, 200, accepted);
}

async function invoke(request: IncomingMessage, response: ServerResponse) {
  const body = await input(request);
  if (body.operation === "health") {
    reply(response, 200, { healthy: true });
  } else if (request.headers.authorization !== "Bearer " + TEST_AGENT_KEY) {
    reply(response, 401, { error: "A valid bearer key is required" });
  } else {
    await acceptWork(response, body);
  }
}

async function release(request: IncomingMessage, response: ServerResponse) {
  const body = await input(request);
  if (!acceptedRuns.delete(body.run_id)) {
    reply(response, 409, { detail: "The stand-in did not accept this run" });
    return;
  }
  const path = "/api/runs/" + body.run_id;
  const result = body.reason
    ? await appPost(path + "/stop", { reason: body.reason })
    : await appPost(path + "/finish");
  reply(response, result.status, await result.json());
}

createServer((request, response) => {
  if (request.method === "POST" && request.url === "/invocations") {
    void invoke(request, response).catch(() => {
      reply(response, 503, { error: "The stand-in could not take the work" });
    });
  } else if (request.method === "POST" && request.url === "/api/release") {
    void release(request, response).catch(() => {
      reply(response, 503, { error: "The stand-in could not finish the run" });
    });
  } else if (request.url === "/api/starts") {
    reply(response, 200, starts);
  } else if (request.url === "/api/resumed") {
    reply(response, 200, resumed);
  } else if (request.url === "/ping") {
    reply(response, 200, {
      status: acceptedRuns.size ? "HealthyBusy" : "Healthy",
    });
  } else {
    reply(response, 404, { detail: "Not found" });
  }
}).listen(Number(process.env.PORT), "127.0.0.1");
