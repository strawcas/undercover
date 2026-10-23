import { useEffect, useState } from "react";
import {
  type ClaimDetail,
  type ClaimEvent,
  type Handler,
  errorMessage,
  label,
  request,
} from "./api";
import { ClaimView } from "./ClaimDetail";
import { useResource } from "./useResource";

type Action = (operation: string, body?: Record<string, string>) => void;
interface Bundle {
  claim: ClaimDetail;
  events: ClaimEvent[];
}

async function loadClaim(path: string): Promise<Bundle> {
  const [claim, events] = await Promise.all([
    request<ClaimDetail>(path),
    request<ClaimEvent[]>(`${path}/events`),
  ]);
  return { claim, events };
}

function isRunning(bundle: Bundle) {
  return bundle.claim.run_active;
}
function noChange() {
  return;
}

function SignOffControls({
  action,
  busy,
}: Readonly<{ action: Action; busy: boolean }>) {
  const [handlerId, setHandlerId] = useState("");
  const { data, error, refresh } = useResource<Handler[]>("/api/handlers");
  return (
    <form
      className="sign-off-form"
      onSubmit={(event) => {
        event.preventDefault();
        action("sign-off", { handler_id: handlerId });
      }}
    >
      <label htmlFor="handler">Reviewing handler</label>
      <div className="flex flex-wrap gap-3">
        <select
          id="handler"
          required
          value={handlerId}
          disabled={busy}
          onChange={(event) => {
            setHandlerId(event.target.value);
          }}
        >
          <option value="">Select a handler</option>
          {data
            ?.filter((handler) => handler.can_approve_over_limit)
            .map((handler) => (
              <option key={handler.handler_id} value={handler.handler_id}>
                {handler.name}
              </option>
            ))}
        </select>
        <button className="primary-button" disabled={busy || !handlerId}>
          Sign off payout
        </button>
      </div>
      {error && (
        <p role="alert">
          {error}{" "}
          <button type="button" onClick={refresh}>
            Reload handlers
          </button>
        </p>
      )}
    </form>
  );
}

function Actions({
  claim,
  action,
  busy,
}: Readonly<{ claim: ClaimDetail; action: Action; busy: boolean }>) {
  if (claim.run_active)
    return (
      <section className="panel action-panel">
        <span className="activity-dot" />
        <div>
          <h3>Agent is reviewing this claim</h3>
          <p>Follow its work in the activity below.</p>
        </div>
      </section>
    );
  if (!claim.decision)
    return (
      <section className="panel action-panel">
        <div>
          <h3>Start a claim assessment</h3>
          <p>The agent reads the evidence and applies the policy wording.</p>
        </div>
        <button
          className="primary-button"
          disabled={busy}
          onClick={() => {
            action("runs");
          }}
        >
          Run agent
        </button>
      </section>
    );
  if (claim.status === "awaiting_sign_off")
    return (
      <section className="panel">
        <h3>Handler sign-off required</h3>
        <p className="muted">
          Review the decision and payout before signing off.
        </p>
        <SignOffControls action={action} busy={busy} />
      </section>
    );
  if (claim.decision.outcome === "approve" && !claim.payment)
    return (
      <section className="panel action-panel">
        <div>
          <h3>Ready for simulated payment</h3>
          <p>The approved payout is eligible for payment.</p>
        </div>
        <button
          className="primary-button"
          disabled={busy}
          onClick={() => {
            action("pay", { amount: claim.decision?.payout ?? "0" });
          }}
        >
          Record simulated payment
        </button>
      </section>
    );
  return null;
}

function Activity({ events }: Readonly<{ events: ClaimEvent[] }>) {
  return (
    <section className="panel activity" aria-label="Agent activity">
      <div className="section-title">
        <h3>Claim activity</h3>
        <span>
          {events.length} {events.length === 1 ? "event" : "events"}
        </span>
      </div>
      {events.length === 0 ? (
        <p className="muted">Activity will appear when the agent starts.</p>
      ) : (
        <ol aria-label="Claim activity">
          {events.map((event) => (
            <li key={event.id}>
              <span className={`event-marker ${event.status}`} />
              <div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <strong>{event.action}</strong>
                  <time dateTime={event.created_at}>
                    {new Date(event.created_at).toLocaleTimeString()}
                  </time>
                </div>
                <span className={`event-status ${event.status}`}>
                  {label(event.status)}
                </span>
                <p>{event.detail}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function ClaimReader({
  claimId,
  onChange = noChange,
}: Readonly<{ claimId: string; onChange?: () => void }>) {
  const path = `/api/claims/${claimId}`;
  const { data, error, refresh } = useResource(path, loadClaim, isRunning);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  useEffect(() => {
    if (data) onChange();
  }, [data, onChange]);

  async function perform(operation: string, body?: Record<string, string>) {
    if (busy) return;
    setBusy(true);
    setActionError("");
    try {
      await request(`${path}/${operation}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
    } catch (failure: unknown) {
      setActionError(errorMessage(failure));
    } finally {
      refresh();
      onChange();
      setBusy(false);
    }
  }
  const action: Action = (operation, body) => {
    void perform(operation, body);
  };
  if (!data)
    return (
      <div className="panel">
        {error ? (
          <div role="alert">
            <h2>Unable to load claim</h2>
            <p>{error}</p>
            <button onClick={refresh}>Try again</button>
          </div>
        ) : (
          <p role="status">Loading claim…</p>
        )}
      </div>
    );
  return (
    <ClaimView claim={data.claim}>
      {(error ?? actionError) && (
        <div className="panel error-panel" role="alert">
          <p>{error ?? actionError}</p>
          <button
            onClick={() => {
              setActionError("");
              refresh();
            }}
          >
            Refresh claim
          </button>
        </div>
      )}
      <Actions claim={data.claim} action={action} busy={busy} />
      <Activity events={data.events} />
    </ClaimView>
  );
}
