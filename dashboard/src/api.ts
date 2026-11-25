export const statuses = [
  "waiting",
  "in_progress",
  "waiting_for_sign_off",
  "sent_back",
  "decided",
  "left_for_handler",
] as const;
export type Status = (typeof statuses)[number];
export type DecisionKind = "approve" | "decline" | "refer";
type TextStepKind =
  "tool_call" | "tool_result" | "message" | "reading" | "error" | "security";

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
  calculation: PayoutCalculation | null;
  decided_by: string;
  decided_at: string;
  manual_version: string;
  signed_off_by: string | null;
  signed_off_at: string | null;
  gateway_hold: string | null;
}

interface ClaimReview {
  note: string;
  handler: string;
  reviewed_at: string;
}

export interface ReturnedDecision extends ClaimReview {
  decision: Decision;
  approval_limit: number;
}

export interface PayoutCalculation {
  calculation: { step: string; amount: number }[];
  payout: number;
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
  sign_off_available: boolean;
  evaluation_id: string | null;
  source_id: string | null;
  returned_decisions?: ReturnedDecision[];
  review_active?: boolean;
  revision?: number;
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

export interface ContextSource {
  file: string;
  system: string;
  owner: string;
  reviewed: string;
}

interface StepRecord {
  step_id: number;
  thread_id: string;
  created_at: string;
}

type StepDetails =
  | { kind: TextStepKind; content: string }
  | { kind: "lookup"; content: { subject: string; sources: ContextSource[] } }
  | { kind: "memory_search"; content: { query: string; results: string[] } }
  | {
      kind: "memory_save";
      content: { messages: { role: "customer" | "agent"; text: string }[] };
    }
  | { kind: "refusal"; content: { source: string; reason: string } }
  | { kind: "claim_review"; content: ClaimReview };

export type Step = StepRecord & StepDetails;

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
  configuration_version: number;
  round_id: string | null;
  reasoning_effort: string | null;
  set_name: "evaluation" | "held_out" | null;
  tiny: boolean | null;
  status: RunStatus;
  stop_reason?: string | null;
  started_at: string;
  finished_at: string | null;
  summary: string | null;
  members: RunMember[];
  llm_cost: number | null;
  active_seconds: number;
  seconds: number;
}
export interface Configuration {
  version: number;
  parent_version: number | null;
  instructions: string;
  model: string;
  reasoning_effort: string;
  reason: string;
  proposed_by: string;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
  prompt: string;
}
export interface ConfigurationHistory extends Configuration {
  diff: string;
  held_out_scores: Scores | null;
}
export interface Objectives {
  accuracy: number;
  grade: number;
}
export interface Measures extends Objectives {
  cost_per_claim: number;
  seconds_per_claim: number;
}
export interface PlatformSettings {
  payout_approval_limit: number;
  objectives: Objectives;
  noise: Measures;
  tolerance: Measures;
  spending_limit: number;
  concurrency: number;
}
export interface AllowedModel {
  reasoning_efforts: string[];
  input_price: number;
  output_price: number;
}
export interface SettingsView {
  values: PlatformSettings;
  models_allowed: Record<string, AllowedModel>;
  changes: {
    id: number;
    handler: string;
    changed_at: string;
    before: PlatformSettings;
    after: PlatformSettings;
  }[];
}

export type EvaluationSet = "evaluation" | "held_out";
export interface Scores {
  grade: number | null;
  decision_accuracy: number;
  accuracy: number;
  cost_of_mistakes: number;
  straight_through: number;
  model_cost_per_claim: number | null;
  seconds_per_claim: number;
}
export interface Evaluation extends Run {
  scores: Scores;
  judge_cost: number | null;
}
export interface Comparison extends RunMember {
  source_id: string;
  handler_decision: DecisionKind;
  handler_payout: number;
  handler_reason: string;
  handler_reference: string;
  agent_section: string | null;
  grade: number | null;
  letter: "F" | "C" | "B" | "A" | null;
  same_grounds: number | null;
  same_section: number | null;
  right_decision: boolean;
  right: boolean;
  mistake_cost: number;
}
export interface EvaluationDetail extends Evaluation {
  comparisons: Comparison[];
}

export type ConfigurationProposal = Pick<
  Configuration,
  "instructions" | "model" | "reasoning_effort" | "reason"
>;
export interface RecipeProposal {
  recipe: string;
  reason: string;
}
export interface Recipe extends RecipeProposal {
  version: number;
  parent_version: number | null;
  proposed_by: string;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
  diff: string;
}
interface ProposalAudit {
  proposal_id: string;
  diff: string;
  created_at: string;
  decision: "approve" | "reject" | null;
  feedback: string | null;
  handler: string | null;
  decided_at: string | null;
  version: number | null;
}
export type Proposal = ProposalAudit &
  (
    | { kind: "configuration"; arguments: ConfigurationProposal }
    | { kind: "recipe"; arguments: RecipeProposal }
  );
export type RoundStatus =
  | "baseline"
  | "working"
  | "awaiting_configuration"
  | "evaluating"
  | "awaiting_recipe"
  | "finished"
  | "stopped";
export interface Round {
  round_id: string;
  thread_id: string;
  status: RoundStatus;
  stop_reason?: string | null;
  work_expired?: boolean;
  started_at: string;
  started_by: string;
  finished_at: string | null;
  base_version: number;
  candidate_version: number | null;
  recipe_version: number;
  settings: PlatformSettings;
  models_allowed: Record<string, AllowedModel>;
  improver_model: string;
  improver_effort: string;
  progress: number;
  before: Scores | null;
  after: Scores | null;
  verdict: "kept" | "rolled_back" | null;
  explanation: string | null;
  proposals: Proposal[];
  spending: {
    improver: number;
    claims: number;
    judge: number;
    total: number;
    remaining: number;
    unknown: boolean;
  };
}

export const HTTP_TOO_MANY_REQUESTS = 429;

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
  if (response.status === HTTP_TOO_MANY_REQUESTS) {
    throw new ApiError(
      response.status,
      "The app is busy. Try again in a moment.",
    );
  }
  if (!response.ok) {
    const body = (await response.json()) as { detail?: unknown };
    const detail =
      typeof body.detail === "string" ? body.detail : "The request failed";
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

function post<T>(
  path: string,
  body: object,
  headers: Record<string, string> = {},
): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

const claimPath = (claimId: string) =>
  `/api/claims/${encodeURIComponent(claimId)}`;

const reviewCondition = (revision: number) => ({
  "If-Match": `"${String(revision)}"`,
});

const evaluationsPath = "/api/evaluations";
const configurationsPath = "/api/configurations";
const settingsPath = "/api/settings";
const roundsPath = "/api/rounds";
const roundPath = (roundId: string) =>
  `${roundsPath}/${encodeURIComponent(roundId)}`;

export const api = {
  getCurrentHandler: () => request<Handler>("/api/me"),
  listRounds: () => request<Round[]>(roundsPath),
  listRecipes: () => request<Recipe[]>("/api/recipes"),
  startRound: () => post<{ status: string; round_id: string }>(roundsPath, {}),
  stopRound: (roundId: string) =>
    post<{ status: string }>(`${roundPath(roundId)}/stop`, {}),
  decideProposal: (
    roundId: string,
    proposalId: string,
    decision: "approve" | "reject",
    reason: string | null,
  ) =>
    post<{ status: string }>(
      `${roundPath(roundId)}/proposals/${encodeURIComponent(proposalId)}/decision`,
      { decision, reason },
    ),
  listClaims: () => request<ClaimSummary[]>("/api/claims"),
  getClaim: (claimId: string) => request<Claim>(claimPath(claimId)),
  listSteps: (claimId: string) =>
    request<Step[]>(`${claimPath(claimId)}/steps`),
  sendBack: (claimId: string, note: string, revision: number) =>
    post<Claim>(
      `${claimPath(claimId)}/send-back`,
      { note },
      reviewCondition(revision),
    ),
  getPolicy: (policyId: string) =>
    request<Policy>(`/api/policies/${encodeURIComponent(policyId)}`),
  getClaimHistory: (customerId: string) =>
    request<PreviousClaim[]>(
      `/api/customers/${encodeURIComponent(customerId)}/claims`,
    ),
  getCurrentConfiguration: () =>
    request<Configuration>(`${configurationsPath}/current`),
  listConfigurations: () => request<ConfigurationHistory[]>(configurationsPath),
  getConfiguration: (version: number) =>
    request<Configuration>(`${configurationsPath}/${String(version)}`),
  getSettings: () => request<SettingsView>(settingsPath),
  changeSettings: (values: PlatformSettings) =>
    request<SettingsView>(settingsPath, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    }),
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
  signOff: (claimId: string, revision = 0) =>
    post<Claim>(
      `${claimPath(claimId)}/sign-off`,
      {},
      reviewCondition(revision),
    ),
};
