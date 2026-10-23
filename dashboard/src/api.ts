export type ClaimState =
  "waiting" | "in_progress" | "awaiting_sign_off" | "decided";
export type Outcome = "approve" | "decline" | "refer";
export type JsonValue =
  string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export interface QueueRow {
  claim_id: string;
  claim_type: string;
  summary: string;
  customer_name: string;
  amount_claimed: string;
  status: ClaimState;
  outcome: Outcome | null;
  payment_status: "paid" | "eligible" | "blocked" | "unavailable";
  run_active: boolean;
}

export interface Decision {
  outcome: Outcome;
  payout: string;
  calculation: string;
  explanation: string;
  policy_sections: string[];
  created_at: string;
}

export interface ClaimDetail {
  claim_id: string;
  policy_id: string;
  customer_id: string;
  claim_type: string;
  submitted_on: string;
  incident_date: string;
  incident_location: string;
  summary: string;
  story: string;
  amount_claimed: string;
  status: ClaimState;
  run_active: boolean;
  thread_id: string | null;
  items: { description: string; category: string; amount: string }[];
  documents: { name: string; type: string; content: string }[];
  policy: Record<string, JsonValue>;
  plan: Record<string, JsonValue>;
  customer: Record<string, JsonValue> & { name: string };
  add_ons: Record<string, JsonValue>[];
  decision: Decision | null;
  sign_off: {
    handler_id: string;
    handler_name: string;
    amount: string;
    created_at: string;
  } | null;
  payment: { amount: string; created_at: string; simulated: boolean } | null;
}

export interface ClaimEvent {
  id: number;
  thread_id: string;
  action: string;
  status: "started" | "completed" | "blocked" | "failed";
  detail: string;
  created_at: string;
}

export interface Handler {
  handler_id: string;
  name: string;
  can_approve_over_limit: boolean;
}

export const stateLabels: Record<ClaimState, string> = {
  waiting: "Waiting",
  in_progress: "In progress",
  awaiting_sign_off: "Waiting for sign-off",
  decided: "Decided",
};

export function money(value: string) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(value));
}

export function label(value: string) {
  return value
    .replaceAll("_", " ")
    .replace(/^./, (letter) => letter.toUpperCase());
}

export async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(path, options);
  const body: unknown = await response.json();
  if (!response.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? body.detail
        : null;
    throw new Error(
      typeof detail === "string"
        ? detail
        : `Request failed (${String(response.status)}).`,
    );
  }
  return body as T;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}
