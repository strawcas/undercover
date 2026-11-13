import { Link } from "react-router";
import { api, type Run, type RunMember, type RunStatus } from "../api";
import { modelCost, money, seconds, time, timestamp } from "../format";
import { claimPage } from "../paths";
import { REFRESH_MS, useLoad } from "../useLoad";
import { DecisionChip, RunChip, StatusChip } from "./Chips";
import { Message } from "./Message";
import { RunStartForm } from "./RunStartForm";

const NOT_STARTED = "Not started";
const UNAVAILABLE = "Unavailable";

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
      <p className="small">
        Configuration version {run.configuration_version} ·{" "}
        <span className="mono">{run.model}</span> · Reasoning:{" "}
        {run.reasoning_effort ?? "default"}
      </p>
      <p className="muted small">
        Started at {time(run.started_at)}, {run.concurrency} agents at once
        {run.finished_at && `, finished at ${time(run.finished_at)}`}
      </p>
      <p>
        Model cost: {modelCost(run.llm_cost)} · Active claim time:{" "}
        {seconds(run.active_seconds)} · Elapsed: {seconds(run.seconds)}
      </p>
      <h3 id="dispatch-claims-title">Claims in this dispatch</h3>
      {run.members.length === 0 ? (
        <p className="muted">No claims selected.</p>
      ) : (
        <MembersTable members={run.members} status={run.status} />
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

function MembersTable({
  members,
  status,
}: Readonly<{ members: RunMember[]; status: RunStatus }>) {
  return (
    <>
      <p id="dispatch-time-note" className="muted small">
        Start times use your local time. Time to finish is elapsed time per
        claim; held approvals are unfinished.
      </p>
      <div
        className="table-scroll"
        role="region"
        aria-label="Dispatch table scroll area"
      >
        <table
          className="table dispatch-members"
          aria-labelledby="dispatch-claims-title"
          aria-describedby="dispatch-time-note"
        >
          <thead>
            <tr>
              <th scope="col">Claim</th>
              <th scope="col">Customer</th>
              <th scope="col">State</th>
              <th scope="col">Decision</th>
              <th scope="col" className="num">
                Payout
              </th>
              <th scope="col">Started</th>
              <th scope="col" className="num">
                Time to finish
              </th>
              <th scope="col" className="num">
                Model cost
              </th>
            </tr>
          </thead>
          <tbody>
            {members.map((member) => (
              <tr key={member.claim_id}>
                <td className="nowrap">
                  <Link className="mono" to={claimPage(member.claim_id)}>
                    {member.claim_id}
                  </Link>
                </td>
                <td>{member.customer_name}</td>
                <td>
                  <StatusChip status={member.claim_status} />
                  {member.status === "skipped" && (
                    <p className="member-note small">Already being resumed</p>
                  )}
                  {member.error && (
                    <p className="member-note small">{member.error}</p>
                  )}
                </td>
                <td>
                  {member.decision ? (
                    <DecisionChip decision={member.decision} />
                  ) : (
                    "Pending"
                  )}
                </td>
                <td className="num mono">
                  {member.payout === null ? "—" : money(member.payout)}
                </td>
                <td className="mono nowrap">
                  <MemberStart member={member} />
                </td>
                <td className="num mono">{completionTime(member, status)}</td>
                <td className="num mono">{modelCost(member.llm_cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function MemberStart({ member }: Readonly<{ member: RunMember }>) {
  if (member.started_at === null)
    return <>{unstarted(member) ? NOT_STARTED : UNAVAILABLE}</>;
  return (
    <time dateTime={member.started_at}>{timestamp(member.started_at)}</time>
  );
}

function unstarted(member: RunMember): boolean {
  return (
    member.started_at === null &&
    (member.status === "skipped" ||
      (member.status === "selected" && member.claim_status === "waiting"))
  );
}

function completionTime(member: RunMember, status: RunStatus): string {
  if (member.elapsed_seconds !== null) return seconds(member.elapsed_seconds);
  if (member.status === "skipped" && member.started_at === null)
    return "Not worked";
  if (member.claim_status === "waiting_for_sign_off")
    return "Awaiting sign-off";
  if (member.claim_status === "decided") return UNAVAILABLE;
  if (member.decision === "approve" && member.finished_at === null)
    return "Awaiting payment";
  if (member.claim_status === "left_for_handler") return "Needs handler";
  if (unstarted(member)) return NOT_STARTED;
  return status === "running" ? "In progress" : "Not completed";
}
