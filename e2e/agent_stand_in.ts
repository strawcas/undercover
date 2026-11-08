// Stands in for the agent's service in the end-to-end tests: it accepts each claim
// the backend asks it to resume, and lists them at /api/resumed.
import { createServer, type ServerResponse } from "node:http";

const resumed: string[] = [];
const starts: unknown[] = [];

function reply(response: ServerResponse, status: number, body: unknown) {
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
}

createServer((request, response) => {
  const resume = /^\/api\/claims\/([^/]+)\/resume$/.exec(request.url ?? "");
  if (request.method === "POST" && resume) {
    resumed.push(decodeURIComponent(resume[1]));
    reply(response, 202, { status: "accepted" });
  } else if (request.method === "POST" && request.url === "/api/dispatches") {
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
  } else if (request.url === "/api/resumed") {
    reply(response, 200, resumed);
  } else if (request.url === "/api/health") {
    reply(response, 200, { status: "ok" });
  } else {
    reply(response, 404, { detail: "Not found" });
  }
}).listen(Number(process.env.PORT), "127.0.0.1");
