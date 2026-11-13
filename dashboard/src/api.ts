export const statuses = [
  "waiting",
  "in_progress",
  "waiting_for_sign_off",
  "decided",
  "left_for_handler",
] as const;
export type Status = (typeof statuses)[number];
export type DecisionKind = "approve" | "decline" | "refer";
export type StepKind =
  "tool_call" | "tool_result" | "message" | "reading" | "error";

export interface ClaimSummary {
  claim_id: string;
  customer_name: string;
  claim_type: string;
  summary: string;
  amount_claimed: number;
  submitted_on: string;
  status: Status;
  decision: DecisionKind | null;
  payout: number | null;
}

export interface Item {
  description: string;
  category: string;
  amount: number;
}

export interface ClaimDocument {
  name: string;
  type: string;
  content: string;
}

export interface Decision {
  decision: DecisionKind;
  payout: number;
  section: string;
  reason: string;
  decided_at: string;
  signed_off_by: string | null;
  signed_off_at: string | null;
}

export interface Payment {
  amount: number;
  paid_at: string;
  signed_off_by: string | null;
  signed_off_at: string | null;
}

export interface Claim {
  claim_id: string;
  policy_id: string;
  customer_id: string;
  customer_name: string;
  claim_type: string;
  submitted_on: string;
  incident_date: string;
  incident_location: string;
  summary: string;
  story: string;
  items: Item[];
  amount_claimed: number;
  documents: ClaimDocument[];
  status: Status;
  thread_id: string | null;
  attempts: number;
  decision: Decision | null;
  payment: Payment | null;
  evaluation_id: string | null;
  source_id: string | null;
}

export interface Policy {
  policy_id: string;
  customer_id: string;
  plan: string;
  add_ons: string[];
  purchased_on: string;
  sold_through: string;
  trip: {
    destination: string;
    departs_on: string;
    returns_on: string;
    trip_cost: number;
  };
  travelers: string[];
  status: string;
}

export interface PreviousClaim {
  claim_id: string;
  filed_on: string;
  incident_date: string;
  claim_type: string;
  summary: string;
  outcome: string;
  amount_paid: number;
}

export interface Handler {
  handler_id: string;
  name: string;
  role: string;
  email: string;
  can_approve_over_limit: boolean;
}

export interface Step {
  step_id: number;
  thread_id: string;
  kind: StepKind;
  content: string;
  created_at: string;
}

export type RunStatus = "running" | "finished" | "stopped";
export interface RunMember {
  claim_id: string;
  customer_name: string;
  status: "selected" | "finished" | "skipped" | "in_progress";
  error: string | null;
  claim_status: Status;
  decision: DecisionKind | null;
  payout: number | null;
  reason: string | null;
  llm_cost: number | null;
  active_seconds: number;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number | null;
}
export interface Run {
  run_id: string;
  kind: "dispatch" | "evaluation";
  concurrency: number;
  model: string;
  reasoning_effort: string | null;
  set_name: "evaluation" | "held_out" | null;
  tiny: boolean | null;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  summary: string | null;
  members: RunMember[];
  llm_cost: number | null;
  active_seconds: number;
  seconds: number;
}
export interface Instructions {
  claim: string;
}

export type EvaluationSet = "evaluation" | "held_out";
export interface Scores {
  decision_accuracy: number;
  accuracy: number;
  cost_of_mistakes: number;
  straight_through: number;
  model_cost_per_claim: number | null;
  seconds_per_claim: number;
}
export interface Evaluation extends Run {
  scores: Scores;
}
export interface Comparison extends RunMember {
  source_id: string;
  handler_decision: DecisionKind;
  handler_payout: number;
  right_decision: boolean;
  right: boolean;
  mistake_cost: number;
}
export interface EvaluationDetail extends Evaluation {
  comparisons: Comparison[];
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

let accessToken: string | null = null;
const authFailures = new EventTarget();

export function onUnauthorized(callback: () => void) {
  authFailures.addEventListener("expired", callback);
  return () => {
    authFailures.removeEventListener("expired", callback);
  };
}

export function setAccessToken(token: string | null) {
  accessToken = token;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const requestToken = accessToken;
  const headers = new Headers(init?.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401 && requestToken === accessToken) {
    accessToken = null;
    authFailures.dispatchEvent(new Event("expired"));
  }
  if (!response.ok) {
    const body = (await response.json()) as { detail?: unknown };
    const detail =
      typeof body.detail === "string" ? body.detail : "The request failed";
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

function post<T>(path: string, body: object): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const claimPath = (claimId: string) =>
  `/api/claims/${encodeURIComponent(claimId)}`;

const evaluationsPath = "/api/evaluations";

export const api = {
  getCurrentHandler: () => request<Handler>("/api/me"),
  listClaims: () => request<ClaimSummary[]>("/api/claims"),
  getClaim: (claimId: string) => request<Claim>(claimPath(claimId)),
  listSteps: (claimId: string) =>
    request<Step[]>(`${claimPath(claimId)}/steps`),
  getPolicy: (policyId: string) =>
    request<Policy>(`/api/policies/${encodeURIComponent(policyId)}`),
  getClaimHistory: (customerId: string) =>
    request<PreviousClaim[]>(
      `/api/customers/${encodeURIComponent(customerId)}/claims`,
    ),
  getInstructions: () => request<Instructions>("/api/instructions"),
  listHandlers: () => request<Handler[]>("/api/handlers"),
  getLatestRun: () => request<Run | null>("/api/runs/latest"),
  getActiveRun: () => request<Run | null>("/api/runs/active"),
  startDispatch: (concurrency: number) =>
    post<{ status: string }>("/api/dispatches", { concurrency }),
  startEvaluation: (
    setName: EvaluationSet,
    tiny: boolean,
    concurrency: number,
  ) =>
    post<{ status: string }>(evaluationsPath, {
      set_name: setName,
      tiny,
      concurrency,
    }),
  listEvaluations: () => request<Evaluation[]>(evaluationsPath),
  getEvaluation: (runId: string) =>
    request<EvaluationDetail>(
      `${evaluationsPath}/${encodeURIComponent(runId)}`,
    ),
  signOff: (claimId: string) =>
    post<Claim>(`${claimPath(claimId)}/sign-off`, {}),
};
