import { useState, type SubmitEvent } from "react";
import { api, type Claim } from "../api";
import { useAuth } from "../auth";

const NOTE_LIMIT = 1000;

export function ClaimReviewForm({
  claimId,
  revision,
  onSignedOff,
  previouslySignedOff,
  canSendBack,
}: Readonly<{
  claimId: string;
  revision: number;
  onSignedOff: (paid: Claim) => void;
  previouslySignedOff: boolean;
  canSendBack: boolean;
}>) {
  const { handler } = useAuth();
  const [error, setError] = useState<string>();
  const [sending, setSending] = useState(false);
  const [writing, setWriting] = useState(false);
  const [note, setNote] = useState("");
  if (!handler?.can_approve_over_limit)
    return (
      <p className="message">
        You cannot sign off this payout. A senior handler is required.
      </p>
    );

  function submit(action: () => Promise<Claim>, fallback: string) {
    setError(undefined);
    setSending(true);
    void action()
      .then(onSignedOff, (reason: unknown) => {
        setError(reason instanceof Error ? reason.message : fallback);
      })
      .finally(() => {
        setSending(false);
      });
  }

  function sendBack(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    submit(
      () => api.sendBack(claimId, note.trim(), revision),
      "Send-back failed",
    );
  }

  return (
    <div className="sign-off-form">
      {!previouslySignedOff && (
        <p>Sign off as {handler.name} to pay the approved amount.</p>
      )}
      {writing ? (
        <form onSubmit={sendBack}>
          <label htmlFor="claim-review-note">
            What should the agent look at again?
          </label>
          <textarea
            id="claim-review-note"
            required
            maxLength={NOTE_LIMIT}
            rows={4}
            value={note}
            onChange={(event) => {
              setNote(event.target.value);
            }}
          />
          <div className="actions">
            <button type="submit" disabled={sending || !note.trim()}>
              Send back
            </button>
            <button
              className="secondary"
              type="button"
              disabled={sending}
              onClick={() => {
                setWriting(false);
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="actions">
          <button
            type="button"
            disabled={sending}
            onClick={() => {
              submit(() => api.signOff(claimId, revision), "Sign-off failed");
            }}
          >
            {previouslySignedOff ? "Record payment" : "Sign off"}
          </button>
          {canSendBack && (
            <button
              className="secondary"
              type="button"
              disabled={sending}
              onClick={() => {
                setWriting(true);
              }}
            >
              Send back
            </button>
          )}
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
