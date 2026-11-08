import { useCallback } from "react";
import { Link, useParams } from "react-router";
import { ApiError, api } from "../api";
import { DecisionChip } from "../components/Chips";
import { EvaluationSummary } from "../components/EvaluationSummary";
import { Message } from "../components/Message";
import { modelCost, money, seconds } from "../format";
import { claimPage, SCOREBOARD_PAGE } from "../paths";
import { REFRESH_MS, useLoad } from "../useLoad";

export function EvaluationPage() {
  const { runId = "" } = useParams();
  const load = useCallback(() => api.getEvaluation(runId), [runId]);
  const { data: run, error } = useLoad(load, REFRESH_MS);
  if (!run)
    return error instanceof ApiError && error.status === 404 ? (
      <p className="message">Evaluation not found</p>
    ) : (
      <Message error={error} />
    );
  return (
    <>
      <p>
        <Link to={SCOREBOARD_PAGE}>Scoreboard</Link>
      </p>
      <h1>Evaluation results</h1>
      <div className="column">
        <section className="panel">
          <EvaluationSummary run={run} />
        </section>
        <section className="panel table-scroll" aria-label="Claim comparisons">
          <h2>Handler and agent decisions</h2>
          <p className="muted small">
            An undecided copy is scored as a referral with a zero payout.
          </p>
          <table className="table comparisons">
            <thead>
              <tr>
                <th>Claim</th>
                <th>Handler decision</th>
                <th>Handler payout</th>
                <th>Agent decision</th>
                <th>Agent payout</th>
                <th>Result</th>
                <th>Cost of mistake</th>
                <th>Model cost</th>
                <th>Active time</th>
                <th>Agent reason</th>
              </tr>
            </thead>
            <tbody>
              {run.comparisons.map((item) => (
                <tr key={item.claim_id}>
                  <td>
                    <Link className="mono" to={claimPage(item.claim_id)}>
                      {item.source_id}
                    </Link>
                  </td>
                  <td>
                    <DecisionChip decision={item.handler_decision} />
                  </td>
                  <td className="mono">{money(item.handler_payout)}</td>
                  <td>
                    {item.decision ? (
                      <DecisionChip decision={item.decision} />
                    ) : (
                      "No decision (scored as referred)"
                    )}
                  </td>
                  <td className="mono">{money(item.payout ?? 0)}</td>
                  <td>
                    <span className={`chip ${item.right ? "match" : "miss"}`}>
                      {item.right ? "Match" : "Miss"}
                    </span>
                  </td>
                  <td className="mono">{money(item.mistake_cost)}</td>
                  <td className="mono">{modelCost(item.llm_cost)}</td>
                  <td className="mono">{seconds(item.active_seconds)}</td>
                  <td>{item.reason ?? item.error ?? "No decision recorded"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </>
  );
}
