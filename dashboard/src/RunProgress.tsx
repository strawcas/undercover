import { type AgentRun, type Todo, label } from "./api";
import { useResource } from "./useResource";

const todoLabels: Record<Todo["status"], string> = {
  to_do: "To do",
  in_progress: "In progress",
  done: "Done",
  left_for_handler: "Left for a handler",
};
const handoffLabels = {
  referral: "Referred for judgment or more information",
  sign_off: "Payout needs handler sign-off",
  technical_failure: "Two failed assessments; no decision recorded",
  unpaid_approval: "Approved payout needs payment review",
};

function watchRun() {
  return true;
}

function RunDetail({
  run,
  onSelect,
}: Readonly<{ run: AgentRun; onSelect: (id: string) => void }>) {
  return (
    <>
      <div className="section-title">
        <h2>{run.status === "running" ? "Current run" : "Latest run"}</h2>
        <span>{label(run.status)}</span>
      </div>
      <p className="muted run-identity">Run {run.run_id}</p>
      <p>
        {run.scope.mode === "claim"
          ? `One claim: ${run.scope.claim_id}`
          : `Up to ${String(run.claim_limit)} distinct claims`}{" "}
        · {run.counts.worked} worked · {run.counts.assessment_attempts}{" "}
        assessment attempts
      </p>
      <p className="muted">
        {run.counts.simulated_payments} simulated payments ·{" "}
        {run.counts.failed_assessments} failed attempts ·{" "}
        {run.counts.unpaid_approvals} unpaid approvals
      </p>
      {run.summary ? (
        <div className="run-summary">
          <p>{run.summary.text}</p>
          {run.summary.detail && <p>{run.summary.detail}</p>}
          <p className="muted">
            Completion-time snapshot ·{" "}
            {run.finished_at && new Date(run.finished_at).toLocaleString()}.
            Later handler actions do not change these totals.
          </p>
        </div>
      ) : (
        <p className="muted" role="status">
          The agent is planning and working this queue.
        </p>
      )}
      {run.todos.length ? (
        <ol className="todo-list" aria-label="Run to-do list">
          {run.todos.map((item) => (
            <li key={item.id}>
              <button
                onClick={() => {
                  onSelect(item.claim_id);
                }}
                aria-label={`Review ${item.claim_id}`}
              >
                <span className="identifier">{item.claim_id}</span>
                <span>{item.text}</span>
              </button>
              <span className={`todo-status ${item.status}`}>
                {todoLabels[item.status]}
              </span>
              {item.handoff_reason && (
                <small>{handoffLabels[item.handoff_reason]}</small>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">
          {run.status === "running"
            ? "The agent has not added its first to-do yet."
            : "No claim to-dos were recorded."}
        </p>
      )}
    </>
  );
}

export function RunProgress({
  onSelect,
}: Readonly<{ onSelect: (id: string) => void }>) {
  const { data, error, refresh } = useResource<{ run: AgentRun | null }>(
    "/api/agent-runs/current",
    undefined,
    watchRun,
  );
  let content = <p role="status">Loading run progress…</p>;
  if (data) {
    content = (
      <>
        <h2>Queue run</h2>
        <p className="muted">
          No run yet. Start processing with uv run -m agent. Use --cap 5 to work
          up to five claims.
        </p>
      </>
    );
    if (data.run) content = <RunDetail run={data.run} onSelect={onSelect} />;
  }
  return (
    <section className="panel run-progress" aria-label="Queue run progress">
      {error && (
        <p role="alert">
          Unable to load run progress: {error}{" "}
          <button onClick={refresh}>Reload run</button>
        </p>
      )}
      {(data !== undefined || !error) && content}
    </section>
  );
}
