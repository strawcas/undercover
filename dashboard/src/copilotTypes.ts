export interface CopilotConversation {
  conversation_id: string;
  handler_id: string;
  title: string | null;
  updated_at: string;
}

export interface CopilotStep {
  step_id: string;
  tool: string;
  kind: "claim" | "customer" | "context" | "work" | "memory" | "action";
  status: "working" | "done" | "refused" | "failed";
  input: string;
  result: unknown;
  reason: string | null;
  started_at: string;
  seconds: number | null;
}

export interface CopilotTurn {
  turn_id: string;
  status: "working" | "waiting" | "done" | "failed" | "stopped";
  message: string;
  answer: string | null;
  failure: string | null;
  confirmation: {
    claim_id: string;
    customer_name: string;
    amount: number;
    reason: string;
  } | null;
  usage: {
    model: string;
    calls: number;
    cost: number | null;
    seconds: number;
  } | null;
  steps: CopilotStep[];
}

export interface CopilotDetail extends CopilotConversation {
  turns: CopilotTurn[];
}
