import { useState } from "react";
import {
  type ClaimDetail,
  type ClaimEvent,
  type Handler,
  type EvidenceResponse,
  errorMessage,
  label,
  request,
} from "./api";
import { ClaimView } from "./ClaimDetail";
import { EvidencePanel } from "./EvidencePanel";
import { useResource } from "./useResource";

type Action = (operation: string, body?: Record<string, string>) => void;
interface Bundle {
  claim: ClaimDetail;
  events: ClaimEvent[];
  evidence: EvidenceResponse;
}

async function loadClaim(path: string): Promise<Bundle> {
  const [claim, events, evidence] = await Promise.all([
    request<ClaimDetail>(path),
    request<ClaimEvent[]>(`${path}/events`),
    request<EvidenceResponse>(`${path}/evidence`),
  ]);
  return { claim, events, evidence };
}

function isRunning(bundle: Bundle) {
  return bundle.claim.run_active || bundle.claim.status === "waiting";
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

function Waiting({
  claim,
  failed,
}: Readonly<{ claim: ClaimDetail; failed: boolean }>) {
  let heading = "Waiting for assessment";
  let detail =
    "Start an assessment with uv run -m agent, or use uv run -m agent --cap 5 for up to five claims.";
  if (claim.status === "handler_review") {
    heading = "Handler review required";
    detail =
      "Two assessments failed. No insurance decision or payout was recorded. A handler must review this claim; automatic processing has stopped.";
  } else if (failed) {
    heading = "Assessment will be retried";
    detail =
      "The last attempt failed. The queue can retry it once. If the run has ended, run uv run -m agent to pick it up.";
  }
  return (
    <section className="panel action-panel">
      <div>
        <h3>{heading}</h3>
        <p>{detail}</p>
      </div>
    </section>
  );
}

function Actions({
  claim,
  action,
  busy,
  failed,
}: Readonly<{
  claim: ClaimDetail;
  action: Action;
  busy: boolean;
  failed: boolean;
}>) {
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
  if (!claim.decision) return <Waiting claim={claim} failed={failed} />;
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
      <Actions
        claim={data.claim}
        action={action}
        busy={busy}
        failed={data.events.at(-1)?.status === "failed"}
      />
      <Activity events={data.events} />
      <EvidencePanel evidence={data.evidence} />
    </ClaimView>
  );
}
