import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { JUDGE_STAND_IN } from "./addresses.ts";

let calls = 0;
async function respond(request: IncomingMessage, response: ServerResponse) {
  response.setHeader("Content-Type", "application/json");
  if (request.url === "/health") {
    response.end(JSON.stringify({ status: "ok" }));
    return;
  }
  if (request.url === "/calls") {
    response.end(JSON.stringify({ calls }));
    return;
  }
  if (request.method !== "POST" || request.url !== "/api/alpha/decisions") {
    response.writeHead(404).end();
    return;
  }
  let text = "";
  for await (const chunk of request) text += String(chunk);
  const body = JSON.parse(text) as { model: string };
  calls += 1;
  response.end(
    JSON.stringify({
      model: body.model,
      id: `judge-${String(calls)}`,
      usage: { input_tokens: 10, output_tokens: 2, cost: 0.0002 },
      answers: {
        grade: {
          type: "score",
          score: 1.6,
          probabilities: { "0": 0.4, "1": 0.05, "2": 0.1, "3": 0.45 },
        },
        same_grounds: { type: "noul", noul: 0.51 },
        same_section: { type: "noul", noul: 0.49 },
      },
    }),
  );
}
createServer((request, response) => {
  void respond(request, response);
}).listen(Number(new URL(JUDGE_STAND_IN).port), "127.0.0.1");
