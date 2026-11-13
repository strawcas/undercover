import { useState } from "react";
import { api, type Proposal } from "./api";

export interface ProposalChoice {
  roundId: string;
  proposal: Proposal;
  decision: "approve" | "reject";
  reason: string | null;
}
export type RoundAction =
  | { kind: "start" }
  | { kind: "stop"; roundId: string }
  | ({ kind: "decision" } & ProposalChoice);

export function useRoundActions(
  reload: () => void,
  onStarted: (identity: string) => void,
) {
  const [action, setAction] = useState<RoundAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const [accepted, setAccepted] = useState<string | null>(null);
  function choose(next: RoundAction) {
    setError("");
    setAnnouncement("");
    setAction(next);
  }
  function cancel() {
    setAction(null);
    setError("");
  }
  async function confirm() {
    if (!action || busy) return;
    setBusy(true);
    setError("");
    try {
      if (action.kind === "start") {
        const result = await api.startRound();
        onStarted(result.round_id);
        setAccepted(result.round_id);
        setAnnouncement("Round accepted. Progress will appear here shortly.");
      } else if (action.kind === "stop") {
        await api.stopRound(action.roundId);
        setAnnouncement(
          "Round stopped. Work already in hand will drain before another run can start.",
        );
      } else {
        await api.decideProposal(
          action.roundId,
          action.proposal.proposal_id,
          action.decision,
          action.reason,
        );
        setAnnouncement(
          action.decision === "approve"
            ? "Approval recorded. The improver will continue."
            : "Rejection and feedback recorded. The improver will reconsider.",
        );
      }
      setAction(null);
      reload();
    } catch (failure: unknown) {
      setError(
        failure instanceof Error
          ? failure.message
          : "The action failed. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }
  return {
    action,
    busy,
    error,
    announcement,
    accepted,
    choose,
    cancel,
    confirm,
  };
}
