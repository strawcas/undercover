import { createServer } from "node:http";
import { setTimeout as later } from "node:timers/promises";
import { randomUUID } from "node:crypto";

interface Invocation {
  operation: "start" | "resume";
  conversation_id: string;
  turn_id: string;
  stretch_id: string;
  bearer: string;
}

const backend = process.env.UNDERCOVER_API ?? "";

async function play({
  operation,
  conversation_id,
  turn_id,
  stretch_id,
  bearer,
}: Invocation) {
  const base = `${backend}/api/copilot/conversations/${conversation_id}/turns/${turn_id}`;
  async function call(path: string, method: string, fields: object) {
    const response = await fetch(`${base}/${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${bearer}`,
      },
      body: JSON.stringify({ ...fields, stretch_id }),
    });
    if (!response.ok) throw new Error(`Copilot fixture rejected ${path}`);
    return response;
  }
  const work = (await (await call("take", "POST", {})).json()) as {
    confirmed: boolean | null;
    message: string;
    confirmation: { claim_id: string; revision: number } | null;
  };
  const outcome = {
    status: "done",
    answer: null,
    failure: null,
    confirmation: null,
    usage: { model: "offline", calls: 2, cost: 0.0003, seconds: 1.2 },
  };
  if (operation === "resume") {
    if (work.confirmed && work.confirmation) {
      const { claim_id, revision } = work.confirmation;
      const response = await fetch(
        `${backend}/api/claims/${claim_id}/sign-off`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${bearer}`,
            "If-Match": `"${String(revision)}"`,
            "X-UnderCover-Copilot-Receipt": `${conversation_id}/${turn_id}/${stretch_id}/${randomUUID()}`,
          },
        },
      );
      if (!response.ok)
        throw new Error("The fixture could not settle the payout");
    }
    await call("outcome", "PUT", {
      ...outcome,
      answer: work.confirmed
        ? "Approved **CLM-1021**.\n\n| Claim | Amount |\n| --- | --- |\n| CLM-1021 | $1,350.00 |"
        : "I haven’t signed off or paid this claim. What would you like me to do next?",
    });
    return;
  }

  const step = `steps/${randomUUID()}`;
  const lookup = {
    tool: "lookup_term",
    kind: "context",
    input: "Deductible",
    status: "working",
    result: null,
    reason: null,
    seconds: null,
  };
  await call(step, "PUT", lookup);
  await later(1300);
  await call(step, "PUT", {
    ...lookup,
    status: "done",
    seconds: 1.3,
    result: { source: { file: "glossary.yaml", owner: "Claims Operations" } },
  });
  const claimId = work.message.includes("Another") ? "CLM-1023" : "CLM-1021";
  const headers = { Authorization: `Bearer ${bearer}` };
  const claim = (await (
    await fetch(`${backend}/api/claims/${claimId}`, { headers })
  ).json()) as {
    customer_name: string;
    decision: { payout: number; reason: string };
    revision: number;
  };
  await call(`steps/${randomUUID()}`, "PUT", {
    tool: "prepare_sign_off",
    kind: "action",
    input: claimId,
    status: "done",
    result: null,
    reason: null,
    seconds: 0.1,
  });
  const absent = await fetch(`${backend}/api/claims/CLM-0000`, { headers });
  const refusal = (await absent.json()) as { detail: string };
  await call(`steps/${randomUUID()}`, "PUT", {
    tool: "get_claim",
    kind: "claim",
    input: "CLM-0000",
    status: "refused",
    result: null,
    reason: refusal.detail,
    seconds: 0.1,
  });
  await call("outcome", "PUT", {
    ...outcome,
    status: "waiting",
    confirmation: {
      claim_id: claimId,
      customer_name: claim.customer_name,
      amount: claim.decision.payout,
      reason: claim.decision.reason,
      revision: claim.revision,
    },
  });
}

createServer((request, response) => {
  void respond(request, response);
}).listen(Number(process.env.PORT), "127.0.0.1");

async function respond(
  request: import("node:http").IncomingMessage,
  response: import("node:http").ServerResponse,
) {
  if (request.url === "/ping") {
    response.end("ready");
    return;
  }
  let body = "";
  for await (const chunk of request) body += String(chunk);
  response.setHeader("Content-Type", "application/json");
  response.end(JSON.stringify({ accepted: true }));
  void play(JSON.parse(body) as Invocation).catch((error: unknown) => {
    console.error(
      error instanceof Error ? error.message : "Copilot stand-in failed",
    );
  });
}
