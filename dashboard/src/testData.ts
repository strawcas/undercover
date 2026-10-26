import type { AgentRun, ClaimDetail, EvidenceResponse, QueueRow } from "./api";

export const claim: ClaimDetail = {
  claim_id: "CLM-1001",
  policy_id: "POL-2001",
  customer_id: "CUS-3001",
  claim_type: "trip_delay",
  submitted_on: "2026-06-24",
  incident_date: "2026-06-18",
  incident_location: "Rome, Italy",
  summary: "Flight home canceled by the airline",
  story:
    "My flight was canceled because of a crew problem. I stayed in a hotel.",
  amount_claimed: "420.00",
  status: "waiting",
  run_active: false,
  thread_id: null,
  run_id: null,
  failed_attempts: 0,
  review_reason: null,
  items: [
    {
      description: "Airport hotel",
      category: "accommodation",
      amount: "420.00",
    },
  ],
  documents: [
    {
      name: "Carrier notice",
      type: "carrier_report",
      content: "Crew shortage confirmed.",
    },
  ],
  policy: {
    policy_id: "POL-2001",
    plan: "premium",
    add_ons: [],
    trip: { destination: "Rome", trip_cost: "3100.00" },
  },
  plan: { name: "Premium", trip_delay_max: "800.00", deductible: "50.00" },
  customer: {
    name: "Maya Patel",
    email: "maya@example.com",
    verified: true,
    contact: null,
    previous_claims: [
      {
        claim_id: "CLM-0412",
        summary: "Previous delay",
        amount_paid: "140.00",
      },
    ],
  },
  add_ons: [{ name: "Electronics Plus", effect: "Higher limit" }],
  decision: null,
  sign_off: null,
  payment: null,
};

export const rows: QueueRow[] = [
  "waiting",
  "in_progress",
  "awaiting_sign_off",
  "decided",
  "handler_review",
].map((status, index) => ({
  claim_id: `CLM-${String(1001 + index)}`,
  claim_type: "trip_delay",
  summary: claim.summary,
  customer_name: `Customer ${String(index + 1)}`,
  amount_claimed: "420.00",
  status: status as QueueRow["status"],
  outcome: index === 3 ? "approve" : null,
  payment_status: index === 3 ? "paid" : "unavailable",
  run_active: index === 1,
  failed_attempts: index === 4 ? 2 : 0,
  review_reason: index === 4 ? "two_failed_assessments" : null,
}));

export const decision = {
  thread_id: "11111111-1111-4111-8111-111111111111",
  outcome: "approve" as const,
  payout: "1250.00",
  calculation: "Eligible expenses capped at USD 1,250.",
  explanation: "The evidence supports a covered delay. The plan limit applies.",
  policy_sections: ["3", "10"],
  manual_sections: ["6"],
  policy_wording_version: "2026.1",
  claims_manual_version: "2026.2",
  created_at: "2026-10-24T00:00:00Z",
};

export const evidence: EvidenceResponse = {
  claim_id: claim.claim_id,
  reads: [
    {
      evidence_id: "read-1",
      run_id: "22222222-2222-4222-8222-222222222222",
      claim_id: claim.claim_id,
      thread_id: decision.thread_id,
      source: "claims_manual",
      version: "2026.2",
      section_id: "6",
      title: "Calculating the payout",
      text: "Full audited section text, including its final sentence.",
      read_at: decision.created_at,
    },
  ],
};

export const run: AgentRun = {
  run_id: "22222222-2222-4222-8222-222222222222",
  scope: { mode: "limited", max_claims: 5 },
  status: "running",
  claim_limit: 5,
  snapshot_claim_ids: rows.map((row) => row.claim_id),
  admitted_claim_ids: [],
  capacity_remaining: 5,
  started_at: decision.created_at,
  finished_at: null,
  todos: [],
  attempts: [],
  counts: {
    worked: 0,
    assessment_attempts: 0,
    failed_assessments: 0,
    approved: 0,
    declined: 0,
    referred: 0,
    simulated_payments: 0,
    awaiting_sign_off: 0,
    handler_review: 0,
    unpaid_approvals: 0,
    waiting_remaining: 5,
  },
  summary: null,
};
