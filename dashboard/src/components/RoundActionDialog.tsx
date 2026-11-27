import type { RoundAction, useRoundActions } from "../useRoundActions";
import { ConfirmDialog } from "./ConfirmDialog";

function dialogLabels(action: RoundAction | null) {
  if (action?.kind === "start")
    return { title: "Start an improvement round?", confirm: "Start round" };
  if (action?.kind === "stop")
    return { title: "Stop this round?", confirm: "Stop round" };
  if (action?.kind === "decision" && action.decision === "reject")
    return { title: "Reject this proposal?", confirm: "Reject proposal" };
  return { title: "Approve this proposal?", confirm: "Approve proposal" };
}

function ActionContent({ action }: Readonly<{ action: RoundAction | null }>) {
  if (!action) return null;
  if (action.kind === "start")
    return (
      <p>
        The round captures the current configuration, recipe and settings, and
        may spend up to the round limit shown in Settings.
      </p>
    );
  if (action.kind === "stop")
    return (
      <p>
        Further work will be refused. Any committed verdict stays in place, and
        work already in hand will finish draining.
      </p>
    );
  if (action.decision === "reject")
    return (
      <>
        <p>{action.proposal.arguments.reason}</p>
        <p className="narrative">Your feedback: {action.reason}</p>
      </>
    );
  return (
    <>
      <p>{action.proposal.arguments.reason}</p>
      <p>
        {action.proposal.kind === "configuration"
          ? "This approves testing the candidate; the backend decides whether to keep it."
          : "This makes the recipe available to future rounds."}
      </p>
    </>
  );
}

export function RoundActionDialog({
  actions,
}: Readonly<{ actions: ReturnType<typeof useRoundActions> }>) {
  const labels = dialogLabels(actions.action);
  return (
    <ConfirmDialog
      open={actions.action !== null}
      title={labels.title}
      confirmLabel={labels.confirm}
      busy={actions.busy}
      onCancel={actions.cancel}
      onConfirm={() => {
        void actions.confirm();
      }}
    >
      <ActionContent action={actions.action} />
      {actions.error && (
        <p className="form-error" role="alert">
          {actions.error}
        </p>
      )}
    </ConfirmDialog>
  );
}
