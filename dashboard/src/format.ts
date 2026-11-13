import type {
  DecisionKind,
  Round,
  RoundStatus,
  RunStatus,
  Status,
} from "./api";

const dollars = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const day = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

const clock = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
});

export function money(amount: number): string {
  return dollars.format(amount);
}

export function date(isoDate: string): string {
  return day.format(new Date(`${isoDate}T00:00:00Z`));
}

export function time(isoTime: string): string {
  return clock.format(new Date(isoTime));
}

export function label(code: string): string {
  const words = code.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export const statusLabels: Record<Status, string> = {
  waiting: "Waiting",
  in_progress: "In progress",
  waiting_for_sign_off: "Waiting for sign-off",
  decided: "Decided",
  left_for_handler: "Left for a handler",
};

export const runLabels: Record<RunStatus, string> = {
  running: "Running",
  finished: "Finished",
  stopped: "Stopped",
};

export const roundLabels: Record<RoundStatus, string> = {
  baseline: "Baseline evaluations",
  working: "Improver working",
  awaiting_configuration: "Configuration approval",
  evaluating: "Candidate evaluations",
  awaiting_recipe: "Recipe approval",
  finished: "Finished",
  stopped: "Stopped",
};

export function roundClosed(status: RoundStatus) {
  return status === "finished" || status === "stopped";
}
export function roundMeasurePlaceholder(status: RoundStatus) {
  return roundClosed(status) ? "Not evaluated" : "Pending";
}
export function roundVerdictLabel(
  round: Pick<Round, "verdict" | "status" | "candidate_version">,
) {
  if (round.verdict) return label(round.verdict);
  if (!roundClosed(round.status)) return "Pending";
  return round.candidate_version === null ? "No version tried" : "No verdict";
}

export const decisionLabels: Record<DecisionKind, string> = {
  approve: "Approved",
  decline: "Declined",
  refer: "Referred",
};

const addOnNames: Record<string, string> = {
  adventure_sports: "Adventure Sports",
  pre_existing_waiver: "Pre-Existing Condition Waiver",
  electronics_plus: "Electronics Plus",
};

export function addOnName(code: string): string {
  return addOnNames[code] ?? label(code);
}

export function modelCost(amount: number | null): string {
  return amount === null ? "Unavailable" : `$${amount.toFixed(6)}`;
}
export function seconds(amount: number): string {
  return `${amount.toFixed(1)} s`;
}

export function percent(amount: number): string {
  return `${(amount * 100).toFixed(1)}%`;
}
export const NOT_GRADED = "Not graded";

export function gradeScore(value: number | null): string {
  return value === null ? NOT_GRADED : `${value.toFixed(2)} / 3`;
}

export function judgeCheck(probability: number | null): string {
  if (probability === null) return NOT_GRADED;
  return probability >= 0.5 ? "Yes" : "No";
}
export function timestamp(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "medium",
  });
}
export function evaluationName(
  setName: string | null,
  tiny: boolean | null,
): string {
  return `${setName === "held_out" ? "Held-out" : "Evaluation"} · ${tiny ? "Tiny" : "Full"}`;
}
