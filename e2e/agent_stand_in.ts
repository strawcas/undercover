// Stands in for the agent's service in the end-to-end tests: it accepts each claim
// the backend asks it to resume, and lists them at /api/resumed.
import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { TEST_BACKEND } from "./addresses.ts";

const backend = process.env.UNDERCOVER_API ?? TEST_BACKEND;

const resumed: string[] = [];
const starts: unknown[] = [];
let busy = false;

function reply(response: ServerResponse, status: number, body: unknown) {
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
}

async function forwardRound(
  request: IncomingMessage,
  response: ServerResponse,
) {
  if (busy && !request.url?.endsWith("/stop")) {
    reply(response, 409, {
      detail: "A dispatch or evaluation is already running",
    });
    return;
  }
  let body = "";
  for await (const chunk of request) body += String(chunk);
  const path = (request.url ?? "").replace(
    "/api/rounds",
    "/api/improver/rounds",
  );
  const stopping = path.endsWith("/stop");
  const result = await fetch(
    `${backend}${stopping ? path.replace(/\/stop$/, "/finish") : path}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: stopping
        ? JSON.stringify({ stopped: true, reason: "Stopped by Dana Whitfield" })
        : body,
    },
  );
  const value = (await result.json()) as { round_id?: string };
  reply(
    response,
    result.ok ? 202 : result.status,
    result.ok ? { status: "accepted", round_id: value.round_id } : value,
  );
}

function isRound(request: IncomingMessage) {
  return request.method === "POST" && request.url?.startsWith("/api/rounds");
}

createServer((request, response) => {
  const resume = /^\/api\/claims\/([^/]+)\/resume$/.exec(request.url ?? "");
  if (isRound(request)) {
    void forwardRound(request, response).catch(() => {
      reply(response, 503, {
        detail: "Round stand-in could not reach the backend",
      });
    });
  } else if (request.method === "POST" && resume) {
    resumed.push(decodeURIComponent(resume[1]));
    reply(response, 202, { status: "accepted" });
  } else if (
    request.method === "POST" &&
    (request.url === "/api/dispatches" || request.url === "/api/evaluations")
  ) {
    if (busy) {
      reply(response, 409, {
        detail: "A dispatch or evaluation is already running",
      });
      return;
    }
    busy = true;
    let body = "";
    request.on("data", (chunk: Buffer) => {
      body += chunk.toString();
    });
    request.on("end", () => {
      starts.push(JSON.parse(body));
      reply(response, 202, { status: "accepted" });
    });
  } else if (request.url === "/api/starts") {
    reply(response, 200, starts);
  } else if (request.method === "POST" && request.url === "/api/release") {
    busy = false;
    reply(response, 200, { status: "released" });
  } else if (request.url === "/api/resumed") {
    reply(response, 200, resumed);
  } else if (request.url === "/api/health") {
    reply(response, 200, { status: "ok" });
  } else {
    reply(response, 404, { detail: "Not found" });
  }
}).listen(Number(process.env.PORT), "127.0.0.1");
