import type { DecisionKind, RoundStatus, RunStatus, Status } from "../api";
import {
  decisionLabels,
  roundLabels,
  runLabels,
  statusLabels,
} from "../format";

export function StatusChip({ status }: Readonly<{ status: Status }>) {
  return (
    <span className={`chip status-${status}`}>{statusLabels[status]}</span>
  );
}

export function DecisionChip({
  decision,
}: Readonly<{ decision: DecisionKind }>) {
  return (
    <span className={`chip decision-${decision}`}>
      {decisionLabels[decision]}
    </span>
  );
}

export function RunChip({ status }: Readonly<{ status: RunStatus }>) {
  return <span className={`chip run-${status}`}>{runLabels[status]}</span>;
}

export function RoundChip({ status }: Readonly<{ status: RoundStatus }>) {
  return <span className={`chip round-${status}`}>{roundLabels[status]}</span>;
}
