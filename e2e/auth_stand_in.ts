import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { generateKeyPairSync, sign } from "node:crypto";
import { readFileSync } from "node:fs";
import { AUTH_STAND_IN } from "./addresses.ts";

const { privateKey, publicKey } = generateKeyPairSync("rsa", {
  modulusLength: 2048,
});
const key = {
  ...publicKey.export({ format: "jwk" }),
  kid: "offline",
  alg: "RS256",
  use: "sig",
};
const handlers = JSON.parse(readFileSync("data/handlers.json", "utf8")) as {
  handler_id: string;
  email: string;
}[];

function session(email: string) {
  const handler = handlers.find((item) => item.email === email);
  if (!handler) return null;
  const user = {
    id: handler.handler_id,
    email,
    aud: "authenticated",
    role: "authenticated",
    app_metadata: {},
    user_metadata: {},
    created_at: new Date(0).toISOString(),
  };
  const header = Buffer.from(
    JSON.stringify({ alg: "RS256", typ: "JWT", kid: key.kid }),
  ).toString("base64url");
  const payload = Buffer.from(
    JSON.stringify({
      sub: user.id,
      email,
      aud: user.aud,
      iss: `${AUTH_STAND_IN}/auth/v1`,
      exp: Math.floor(Date.now() / 1000) + 3600,
    }),
  ).toString("base64url");
  const unsigned = `${header}.${payload}`;
  const signature = sign(
    "RSA-SHA256",
    Buffer.from(unsigned),
    privateKey,
  ).toString("base64url");
  return {
    access_token: `${unsigned}.${signature}`,
    refresh_token: email,
    token_type: "bearer",
    expires_in: 3600,
    user,
  };
}

async function respond(request: IncomingMessage, response: ServerResponse) {
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  response.setHeader(
    "Access-Control-Allow-Headers",
    "authorization, apikey, content-type, x-client-info, x-supabase-api-version",
  );
  response.setHeader("Content-Type", "application/json");
  if (request.method === "OPTIONS") {
    response.writeHead(204).end();
    return;
  }
  const path = new URL(request.url ?? "/", AUTH_STAND_IN).pathname;
  if (path.endsWith("/jwks.json")) {
    response.end(JSON.stringify({ keys: [key] }));
    return;
  }
  if (path.endsWith("/logout")) {
    response.writeHead(204).end();
    return;
  }
  if (path.endsWith("/token")) {
    let text = "";
    for await (const chunk of request) text += String(chunk);
    const body = JSON.parse(text) as {
      email?: string;
      password?: string;
      refresh_token?: string;
    };
    const result =
      body.password === "offline-password" || body.refresh_token
        ? session(body.email ?? body.refresh_token ?? "")
        : null;
    response.writeHead(result ? 200 : 400).end(
      JSON.stringify(
        result ?? {
          error: "invalid_grant",
          error_description: "Invalid email or password",
        },
      ),
    );
    return;
  }
  response.writeHead(404).end(JSON.stringify({ error: "Not found" }));
}

createServer((request, response) => {
  void respond(request, response);
}).listen(Number(new URL(AUTH_STAND_IN).port), "127.0.0.1");
