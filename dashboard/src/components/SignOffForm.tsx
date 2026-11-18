import { useState } from "react";
import { api, type Claim } from "../api";
import { useAuth } from "../auth";

export function SignOffForm({
  claimId,
  onSignedOff,
  previouslySignedOff,
}: Readonly<{
  claimId: string;
  onSignedOff: (paid: Claim) => void;
  previouslySignedOff: boolean;
}>) {
  const { handler } = useAuth();
  const [error, setError] = useState<string>();
  const [sending, setSending] = useState(false);
  if (!handler?.can_approve_over_limit)
    return (
      <p className="message">
        You cannot sign off this payout. A senior handler is required.
      </p>
    );

  function submit() {
    setSending(true);
    api.signOff(claimId).then(onSignedOff, (reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "Sign-off failed");
      setSending(false);
    });
  }

  return (
    <div className="sign-off-form">
      {!previouslySignedOff && (
        <p>Sign off as {handler.name} to pay the approved amount.</p>
      )}
      <button type="button" disabled={sending} onClick={submit}>
        {previouslySignedOff ? "Record payment" : "Sign off"}
      </button>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
