import { useState } from "react";
import { Link } from "react-router";
import type { ConfigurationHistory, Proposal, Round } from "../api";
import {
  gradeScore,
  label,
  modelCost,
  money,
  percent,
  roundLabels,
  roundClosed,
  roundMeasurePlaceholder,
  timestamp,
} from "../format";
import { CONFIGURATION_PAGE } from "../paths";
import { RoundChip } from "./Chips";
import { Diff } from "./Diff";
import { RoundScores } from "./RoundScores";
import { ModelComparison } from "./ModelComparison";

import type { ProposalChoice } from "../useRoundActions";

function ProposalModels({
  proposal,
  round,
  base,
}: Readonly<{
  proposal: Proposal;
  round: Round;
  base?: ConfigurationHistory;
}>) {
  if (proposal.kind !== "configuration") return null;
  return (
    <ModelComparison
      before={base}
      after={proposal.arguments}
      beforeLabel={`Before · version ${String(round.base_version)}`}
      afterLabel="Proposed model and effort"
    />
  );
}

function PendingProposal({
  proposal,
  round,
  base,
  canDecide,
  onDecide,
}: Readonly<{
  proposal: Proposal;
  round: Round;
  base?: ConfigurationHistory;
  canDecide: boolean;
  onDecide: (choice: ProposalChoice) => void;
}>) {
  const [reason, setReason] = useState("");
  const closed = roundClosed(round.status);
  return (
    <section
      className="proposal"
      aria-label={closed ? "Undecided proposal" : "Pending proposal"}
    >
      <div className="run-heading">
        <h3>
          {proposal.kind === "configuration"
            ? "Proposed configuration"
            : "Proposed recipe"}
        </h3>
        <span
          className={`chip ${closed ? "grade-ungraded" : "round-awaiting_configuration"}`}
        >
          {closed ? "Closed without a decision" : "Awaiting approval"}
        </span>
      </div>
      <p className="narrative">{proposal.arguments.reason}</p>
      <ProposalModels proposal={proposal} round={round} base={base} />
      <Diff text={proposal.diff} />
      {canDecide ? (
        <div className="approval-form">
          <label>
            Rejection reason
            <textarea
              value={reason}
              rows={3}
              onChange={(event) => {
                setReason(event.target.value);
              }}
              placeholder="Explain what the improver should reconsider."
            />
          </label>
          <div className="actions">
            <button
              type="button"
              onClick={() => {
                onDecide({
                  roundId: round.round_id,
                  proposal,
                  decision: "approve",
                  reason: null,
                });
              }}
            >
              Approve {proposal.kind === "configuration" ? "testing" : "recipe"}
            </button>
            <button
              type="button"
              className="secondary"
              disabled={!reason.trim()}
              onClick={() => {
                onDecide({
                  roundId: round.round_id,
                  proposal,
                  decision: "reject",
                  reason: reason.trim(),
                });
              }}
            >
              Reject proposal
            </button>
          </div>
          <p className="muted small">
            {proposal.kind === "configuration"
              ? "Approval creates a candidate for testing. The backend decides whether it becomes current."
              : "An approved recipe applies to future rounds."}
          </p>
        </div>
      ) : (
        <p className="muted small">
          {closed
            ? "This round closed before the proposal was decided."
            : "Only a senior handler can approve or reject this proposal."}
        </p>
      )}
    </section>
  );
}

function RoundSettings({ round }: Readonly<{ round: Round }>) {
  const values = round.settings;
  const thresholds = [
    [
      "Accuracy (percentage points)",
      (values.noise.accuracy * 100).toFixed(1),
      (values.tolerance.accuracy * 100).toFixed(1),
    ],
    [
      "Grade (points)",
      values.noise.grade.toFixed(2),
      values.tolerance.grade.toFixed(2),
    ],
    [
      "Cost / claim (% of baseline)",
      percent(values.noise.cost_per_claim),
      percent(values.tolerance.cost_per_claim),
    ],
    [
      "Time / claim (% of baseline)",
      percent(values.noise.seconds_per_claim),
      percent(values.tolerance.seconds_per_claim),
    ],
  ];
  return (
    <details className="round-settings">
      <summary>Settings captured for this round</summary>
      <dl className="evaluation-measures">
        <div>
          <dt>Accuracy floor</dt>
          <dd className="mono">{percent(values.objectives.accuracy)}</dd>
        </div>
        <div>
          <dt>Grade floor</dt>
          <dd className="mono">{gradeScore(values.objectives.grade)}</dd>
        </div>
        <div>
          <dt>Approval limit</dt>
          <dd className="mono">{money(values.payout_approval_limit)}</dd>
        </div>
        <div>
          <dt>Claim agents at once</dt>
          <dd className="mono">{values.concurrency}</dd>
        </div>
      </dl>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Measure and units</th>
              <th className="num">Noise</th>
              <th className="num">Tolerance</th>
            </tr>
          </thead>
          <tbody>
            {thresholds.map(([name, noise, tolerance]) => (
              <tr key={name}>
                <th scope="row">{name}</th>
                <td className="num mono">{noise}</td>
                <td className="num mono">{tolerance}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Later platform changes apply to future rounds. Accuracy and grade use
        absolute differences; cost and time use fractions of the baseline.
      </p>
    </details>
  );
}

function RoundMeasures({ round }: Readonly<{ round: Round }>) {
  return (
    <>
      {" "}
      <dl className="evaluation-measures">
        <div>
          <dt>Baseline version</dt>
          <dd className="mono">v{round.base_version}</dd>
        </div>
        <div>
          <dt>Candidate version</dt>
          <dd className="mono">
            {round.candidate_version === null
              ? "No version tried"
              : `v${String(round.candidate_version)}`}
          </dd>
        </div>
        <div>
          <dt>Recipe followed</dt>
          <dd className="mono">v{round.recipe_version}</dd>
        </div>
        <div>
          <dt>Total spending (USD)</dt>
          <dd className="mono">{modelCost(round.spending.total)}</dd>
        </div>
        <div>
          <dt>Remaining budget (USD)</dt>
          <dd className="mono">{modelCost(round.spending.remaining)}</dd>
        </div>
        <div>
          <dt>Round limit (USD)</dt>
          <dd className="mono">{money(round.settings.spending_limit)}</dd>
        </div>
      </dl>
      {round.spending.unknown && (
        <p className="form-error" role="status">
          Some provider usage is unavailable. Displayed spending is a known
          minimum; new work is refused.
        </p>
      )}
      <p className="muted small">
        Improver {modelCost(round.spending.improver)} · Claim evaluations{" "}
        {modelCost(round.spending.claims)} · Judge{" "}
        {modelCost(round.spending.judge)}
        <br />
        {round.improver_model} · {round.improver_effort} effort
      </p>
    </>
  );
}

export function RoundDetail({
  round,
  configurations,
  senior,
  open,
  onStop,
  onDecide,
}: Readonly<{
  round: Round;
  configurations: ConfigurationHistory[];
  senior: boolean;
  open: boolean;
  onStop: () => void;
  onDecide: (choice: ProposalChoice) => void;
}>) {
  const pending = round.proposals.find(
    (proposal) => proposal.decision === null,
  );
  const decided = round.proposals.filter(
    (proposal) => proposal.decision !== null,
  );
  const base = configurations.find(
    (version) => version.version === round.base_version,
  );
  const progress = round.status === "baseline" || round.status === "evaluating";
  return (
    <section className="panel column" aria-label="Selected round">
      <div className="section-heading">
        <div>
          <div className="run-heading">
            <h2>
              Round <span className="mono">{round.round_id.slice(0, 8)}</span>
            </h2>
            <RoundChip status={round.status} />
          </div>
          <p className="muted small">
            Started by {round.started_by} · {timestamp(round.started_at)}
          </p>
        </div>
        {senior && open && (
          <button type="button" className="secondary" onClick={onStop}>
            Stop round
          </button>
        )}
      </div>
      {progress && (
        <div className="round-progress">
          <label htmlFor="round-progress">
            {roundLabels[round.status]} ·{" "}
            {round.progress ? `${String(round.progress)} of 3` : "Preparing"}
          </label>
          <progress id="round-progress" max={3} value={round.progress} />
        </div>
      )}
      <RoundMeasures round={round} />
      {round.verdict && (
        <div className="verdict">
          <span className={`chip verdict-${round.verdict}`}>
            {label(round.verdict)}
          </span>
          <p>{round.explanation}</p>
        </div>
      )}
      {!round.verdict && round.explanation && <p>{round.explanation}</p>}
      <RoundScores
        before={round.before}
        after={round.after}
        emptyLabel={roundMeasurePlaceholder(round.status)}
      />
      {pending && (
        <PendingProposal
          key={pending.proposal_id}
          proposal={pending}
          round={round}
          base={base}
          canDecide={
            senior &&
            open &&
            (round.status === "awaiting_configuration" ||
              round.status === "awaiting_recipe")
          }
          onDecide={onDecide}
        />
      )}
      <RoundSettings round={round} />
      <div>
        <h3>Proposal decisions</h3>
        {decided.length === 0 ? (
          <p className="muted small">No proposals have been decided.</p>
        ) : (
          <ol className="proposal-history">
            {decided.map((proposal) => (
              <li key={proposal.proposal_id}>
                <details>
                  <summary>
                    {label(proposal.kind)} ·{" "}
                    {proposal.decision === "approve" ? "Approved" : "Rejected"}
                    {proposal.version !== null
                      ? ` · version ${String(proposal.version)}`
                      : ""}
                  </summary>
                  <p className="muted small">
                    {proposal.handler} ·{" "}
                    {proposal.decided_at
                      ? timestamp(proposal.decided_at)
                      : "Unavailable"}
                  </p>
                  <p className="narrative">{proposal.arguments.reason}</p>
                  <ProposalModels
                    proposal={proposal}
                    round={round}
                    base={base}
                  />
                  {proposal.feedback && (
                    <p className="narrative">
                      Handler feedback: {proposal.feedback}
                    </p>
                  )}
                  <Diff text={proposal.diff} />
                </details>
              </li>
            ))}
          </ol>
        )}
      </div>
      <Link to={CONFIGURATION_PAGE}>Review configuration history</Link>
    </section>
  );
}
