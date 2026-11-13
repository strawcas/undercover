import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { App } from "./App";
import { AuthContext, type AuthState } from "./auth";
import type {
  Claim,
  ClaimSummary,
  ConfigurationHistory,
  SettingsView,
  EvaluationDetail,
  Policy,
  PreviousClaim,
} from "./api";

// Stands in for the backend: answers "METHOD /path" with the JSON given, or
// with the status and detail given as an error, and a 404 for anything else.
export type Routes = Record<string, unknown>;

export function mockDialogs() {
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: {
      configurable: true,
      writable: true,
      value: vi.fn(function (this: HTMLDialogElement) {
        this.setAttribute("open", "");
      }),
    },
    close: {
      configurable: true,
      writable: true,
      value: vi.fn(function (this: HTMLDialogElement) {
        this.removeAttribute("open");
      }),
    },
  });
}

export class Refusal {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {}
}

export function stubApi(routes: Routes) {
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${path}`;
    const answer =
      key === "GET /api/runs/active" ? (routes[key] ?? null) : routes[key];
    if (answer === undefined)
      return Promise.resolve(
        Response.json({ detail: "Not found" }, { status: 404 }),
      );
    if (answer instanceof Refusal)
      return Promise.resolve(
        Response.json({ detail: answer.detail }, { status: answer.status }),
      );
    return Promise.resolve(Response.json(answer));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function renderAt(path: string, auth: AuthState = signedIn) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthContext.Provider value={auth}>
        <App />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

export const signedIn: AuthState = {
  handler: {
    handler_id: "HND-01",
    name: "Dana Whitfield",
    email: "dana.whitfield@undercover.example",
    role: "Senior Claims Handler",
    can_approve_over_limit: true,
  },
  loading: false,
  error: null,
  signIn: vi.fn().mockResolvedValue(undefined),
  signOut: vi.fn().mockResolvedValue(undefined),
};

// Finds the panel headed by the given heading, to look inside it.
export async function panel(heading: string) {
  return within(await screen.findByRole("region", { name: heading }));
}

export const claim: Claim = {
  claim_id: "CLM-1002",
  policy_id: "POL-2002",
  customer_id: "CUS-3002",
  customer_name: "Tom Becker",
  claim_type: "baggage",
  submitted_on: "2026-07-20",
  incident_date: "2026-06-21",
  incident_location: "Frankfurt, Germany",
  summary: "Suitcase lost on a connecting flight",
  story: "My suitcase never arrived in Prague.",
  items: [
    { description: "Hard-shell suitcase", category: "luggage", amount: 280 },
    { description: "Wool suit", category: "clothing", amount: 870 },
  ],
  amount_claimed: 1150,
  documents: [
    {
      name: "Lost baggage report",
      type: "carrier_report",
      content: "One checked bag not delivered.",
    },
  ],
  status: "waiting",
  thread_id: null,
  attempts: 0,
  decision: null,
  payment: null,
  evaluation_id: null,
  source_id: null,
};

export const summary: ClaimSummary = {
  claim_id: "CLM-1002",
  customer_name: "Tom Becker",
  claim_type: "baggage",
  summary: "Suitcase lost on a connecting flight",
  amount_claimed: 1150,
  submitted_on: "2026-07-20",
  status: "waiting",
  decision: null,
  payout: null,
};

export const policy: Policy = {
  policy_id: "POL-2002",
  customer_id: "CUS-3002",
  plan: "premium",
  add_ons: ["electronics_plus"],
  purchased_on: "2026-05-02",
  sold_through: "UnderCover website",
  trip: {
    destination: "Prague, Czech Republic",
    departs_on: "2026-06-20",
    returns_on: "2026-06-30",
    trip_cost: 2400,
  },
  travelers: ["Tom Becker"],
  status: "active",
};

export const history: PreviousClaim[] = [
  {
    claim_id: "CLM-0412",
    filed_on: "2024-08-19",
    incident_date: "2024-08-09",
    claim_type: "baggage_delay",
    summary: "Checked bag arrived 30 hours late in Lisbon",
    outcome: "approved",
    amount_paid: 140,
  },
];

const comparisonFixtures: EvaluationDetail["comparisons"] = [
  {
    claim_id: "EVAL-1",
    customer_name: "Maya Patel",
    source_id: "CLM-1001",
    status: "finished",
    error: null,
    claim_status: "decided",
    decision: "decline",
    payout: 0,
    reason: "The policy excludes this event.",
    llm_cost: 0.001,
    active_seconds: 20,
    started_at: "2026-11-09T01:00:00Z",
    finished_at: "2026-11-09T01:00:20Z",
    elapsed_seconds: 20,
    handler_decision: "approve",
    handler_payout: 420,
    handler_reason: "The delay is covered.",
    handler_reference: "Section 3",
    agent_section: "Section 3",
    grade: 1.6,
    letter: "A",
    same_grounds: 0.51,
    same_section: 0.49,
    right_decision: false,
    right: false,
    mistake_cost: 1500,
  },
  {
    claim_id: "EVAL-2",
    customer_name: "Tom Becker",
    source_id: "CLM-1002",
    status: "selected",
    error: "Interrupted",
    claim_status: "waiting",
    decision: null,
    payout: null,
    reason: null,
    llm_cost: null,
    active_seconds: 0,
    started_at: null,
    finished_at: null,
    elapsed_seconds: null,
    handler_decision: "refer",
    handler_payout: 0,
    handler_reason: "A handler must review the documents.",
    handler_reference: "Section 5",
    agent_section: null,
    grade: 0,
    letter: "F",
    same_grounds: 0,
    same_section: 0,
    right_decision: true,
    right: true,
    mistake_cost: 0,
  },
];

export const evaluation: EvaluationDetail = {
  run_id: "eval-1",
  kind: "evaluation",
  concurrency: 5,
  model: "openai/gpt-6-luna",
  configuration_version: 1,
  round_id: null,
  reasoning_effort: null,
  set_name: "held_out",
  tiny: true,
  status: "finished",
  started_at: "2026-11-09T01:00:00Z",
  finished_at: "2026-11-09T01:00:30Z",
  summary: "Worked 2 claims",
  llm_cost: 0.001,
  active_seconds: 20,
  seconds: 30,
  members: comparisonFixtures,
  scores: {
    grade: 0.8,
    decision_accuracy: 0.5,
    accuracy: 0.5,
    cost_of_mistakes: 1500,
    straight_through: 0.5,
    model_cost_per_claim: 0.0005,
    seconds_per_claim: 10,
  },
  comparisons: comparisonFixtures,
  judge_cost: 0.0002,
};

export const configuration: ConfigurationHistory = {
  version: 1,
  parent_version: null,
  instructions: "# Working a claim",
  model: "openai/gpt-6-luna",
  reasoning_effort: "low",
  reason: "Initial version",
  proposed_by: "UnderCover",
  approved_by: null,
  approved_at: null,
  created_at: "2026-01-01T00:00:00Z",
  prompt: "# Working a claim\n\n## Contents of the claims manual",
  diff: "+# Working a claim",
  held_out_scores: null,
};

export const settings: SettingsView = {
  values: {
    payout_approval_limit: 1000,
    objectives: { accuracy: 0.6, grade: 1 },
    noise: {
      accuracy: 0.1,
      grade: 0.15,
      cost_per_claim: 0.15,
      seconds_per_claim: 0.15,
    },
    tolerance: {
      accuracy: 0.1,
      grade: 0.15,
      cost_per_claim: 1,
      seconds_per_claim: 0.25,
    },
    spending_limit: 0.5,
    concurrency: 25,
  },
  models_allowed: {
    "openai/gpt-6-luna": {
      reasoning_efforts: ["none", "low", "medium"],
      input_price: 0.1,
      output_price: 0.5,
    },
  },
  changes: [],
};
