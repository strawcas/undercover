import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

export function SignOffForm({
  claimId,
  onSignedOff,
}: Readonly<{ claimId: string; onSignedOff: () => void }>) {
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
      <p>Sign off as {handler.name}.</p>
      <button type="button" disabled={sending} onClick={submit}>
        Sign off
      </button>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
