export type ClaimState =
  | "waiting"
  | "in_progress"
  | "awaiting_sign_off"
  | "decided"
  | "handler_review";
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
  failed_attempts: number;
  review_reason: "two_failed_assessments" | "payment_failed" | null;
}

export interface Decision {
  thread_id: string;
  outcome: Outcome;
  payout: string;
  calculation: string;
  explanation: string;
  policy_sections: string[];
  manual_sections: string[];
  policy_wording_version: string | null;
  claims_manual_version: string | null;
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
  run_id: string | null;
  failed_attempts: number;
  review_reason: QueueRow["review_reason"];
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
  claim_id: string;
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
  handler_review: "Handler review",
};

export interface Counts {
  worked: number;
  assessment_attempts: number;
  failed_assessments: number;
  approved: number;
  declined: number;
  referred: number;
  simulated_payments: number;
  awaiting_sign_off: number;
  handler_review: number;
  unpaid_approvals: number;
  waiting_remaining: number;
}

export interface Todo {
  id: string;
  claim_id: string;
  text: string;
  status: "to_do" | "in_progress" | "done" | "left_for_handler";
  handoff_reason:
    "referral" | "sign_off" | "technical_failure" | "unpaid_approval" | null;
}

export interface Attempt {
  run_id: string;
  claim_id: string;
  thread_id: string;
  attempt_number: number;
  status: "active" | "completed" | "failed";
  started_at: string;
  deadline_at: string;
  finished_at: string | null;
  error: string | null;
  claim_status: ClaimState;
  failed_attempts: number;
  outcome: Outcome | null;
  payout: string | null;
  payment_status: QueueRow["payment_status"];
}

export interface AgentRun {
  run_id: string;
  scope:
    | { mode: "all" }
    | { mode: "limited"; max_claims: number }
    | { mode: "claim"; claim_id: string };
  status: "running" | "completed" | "failed" | "interrupted";
  claim_limit: number;
  snapshot_claim_ids: string[];
  admitted_claim_ids: string[];
  capacity_remaining: number;
  started_at: string;
  finished_at: string | null;
  todos: Todo[];
  attempts: Attempt[];
  counts: Counts;
  summary: { text: string; detail: string; counts: Counts } | null;
}

export interface Evidence {
  evidence_id: string;
  run_id: string;
  claim_id: string;
  thread_id: string;
  source: "policy_wording" | "claims_manual";
  version: string;
  section_id: string;
  title: string;
  text: string;
  read_at: string;
}

export interface EvidenceResponse {
  claim_id: string;
  reads: Evidence[];
}

export interface InstructionsResponse {
  text: string;
  queue_text: string;
}

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
