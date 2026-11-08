import { Link } from "react-router";
import { api, type Run } from "../api";
import { modelCost, seconds, time } from "../format";
import { claimPage } from "../paths";
import { REFRESH_MS, useLoad } from "../useLoad";
import { RunChip, StatusChip } from "./Chips";
import { Message } from "./Message";
import { RunStartForm } from "./RunStartForm";

export function RunPanel() {
  const { data: run, error } = useLoad(api.getLatestRun, REFRESH_MS);

  return (
    <section className="panel run" aria-labelledby="run-title">
      <div className="run-heading">
        <h2 id="run-title">Current run</h2>
        {run && <RunChip status={run.status} />}
      </div>
      <RunStartForm name="Dispatch" start={api.startDispatch} />
      {run === undefined && <Message error={error} />}
      {run === null && <p className="muted">No run yet.</p>}
      {run && <RunDetails run={run} />}
    </section>
  );
}

function RunDetails({ run }: Readonly<{ run: Run }>) {
  return (
    <>
      <p className="muted small">
        Started at {time(run.started_at)}, {run.concurrency} agents at once
        {run.finished_at && `, finished at ${time(run.finished_at)}`}
      </p>
      <p>
        Model cost: {modelCost(run.llm_cost)} · Active claim time:{" "}
        {seconds(run.active_seconds)} · Elapsed: {seconds(run.seconds)}
      </p>
      <h3>Claims in this dispatch</h3>
      {run.members.length === 0 ? (
        <p className="muted">No claims selected.</p>
      ) : (
        <ol className="run-members">
          {run.members.map((member) => (
            <li key={member.claim_id}>
              <Link className="mono" to={claimPage(member.claim_id)}>
                {member.claim_id}
              </Link>{" "}
              <StatusChip status={member.claim_status} />
              {member.status === "skipped" && (
                <span> Already being resumed</span>
              )}
              {member.error && <span> {member.error}</span>}
            </li>
          ))}
        </ol>
      )}
      {run.summary && (
        <>
          <h3>Summary for the handlers</h3>
          <p className="summary">{run.summary}</p>
        </>
      )}
    </>
  );
}
