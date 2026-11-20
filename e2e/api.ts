import { type APIRequestContext, expect } from "@playwright/test";
import { AGENT_STAND_IN } from "./addresses";
import { agentHeaders } from "./agent_credentials";

// Stands in for the agent by sending to the API through the dashboard's proxy.
async function send(
  request: APIRequestContext,
  method: "POST" | "PUT",
  path: string,
  data: object,
) {
  const response = await request.fetch(path, {
    method,
    data,
    headers: agentHeaders,
  });
  await expect(response).toBeOK();
  return response;
}

export const post = (request: APIRequestContext, path: string, data: object) =>
  send(request, "POST", path, data);

export const put = (request: APIRequestContext, path: string, data: object) =>
  send(request, "PUT", path, data);

export const get = (request: APIRequestContext, path: string) =>
  request.get(path, { headers: agentHeaders });

interface TestDecision {
  decision: "approve" | "decline" | "refer";
  payout: number;
  section: string;
  reason: string;
}

async function medicalPayout(
  request: APIRequestContext,
  claimId: string,
  amount: number,
  threadId: string | null,
) {
  const response = await post(request, `/api/claims/${claimId}/payout`, {
    benefit: "emergency_medical",
    thread_id: threadId,
    paid_by_others: 0,
    lines: [
      {
        description: "Medical expense",
        kind: "expense",
        amount,
        quantity: 1,
        nights: 0,
        hours: 0,
      },
    ],
  });
  const result = (await response.json()) as { payout: number };
  return result.payout;
}

export async function decide(
  request: APIRequestContext,
  claimId: string,
  decision: TestDecision,
) {
  const response = await get(request, `/api/claims/${claimId}`);
  const claim = (await response.json()) as { thread_id: string };
  if (decision.decision === "approve") {
    const probe = Math.max(decision.payout, 100);
    const deductible =
      probe - (await medicalPayout(request, claimId, probe, null));
    await medicalPayout(
      request,
      claimId,
      decision.payout + deductible,
      claim.thread_id,
    );
  }
  return post(request, `/api/claims/${claimId}/decision`, {
    ...decision,
    thread_id: claim.thread_id,
    decided_by: "UnderCover Agent (configuration 1)",
  });
}

// The stand-in for the agent's service, which Playwright starts on its own port.
export { AGENT_STAND_IN } from "./addresses";

export async function resumed(request: APIRequestContext): Promise<string[]> {
  const response = await request.get(`${AGENT_STAND_IN}/api/resumed`);
  await expect(response).toBeOK();
  return (await response.json()) as string[];
}
