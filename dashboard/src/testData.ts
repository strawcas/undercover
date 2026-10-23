import type { ClaimDetail, QueueRow } from "./api";

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
}));

export const decision = {
  outcome: "approve" as const,
  payout: "1250.00",
  calculation: "Eligible expenses capped at USD 1,250.",
  explanation: "The evidence supports a covered delay. The plan limit applies.",
  policy_sections: ["3", "10"],
  created_at: "2026-10-24T00:00:00Z",
};
