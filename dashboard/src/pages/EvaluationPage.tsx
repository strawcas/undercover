import { useCallback } from "react";
import { Link, useParams } from "react-router";
import { ApiError, api } from "../api";
import { DecisionChip } from "../components/Chips";
import { EvaluationSummary } from "../components/EvaluationSummary";
import { Message } from "../components/Message";
import { Grade } from "../components/Grade";
import { PageHeader } from "../components/PageHeader";
import { modelCost, money, seconds } from "../format";
import { claimPage, SCOREBOARD_PAGE } from "../paths";
import { REFRESH_MS, useLoad } from "../useLoad";

export function EvaluationPage() {
  const { runId = "" } = useParams();
  const load = useCallback(() => api.getEvaluation(runId), [runId]);
  const { data: run, error, reload } = useLoad(load, REFRESH_MS);
  const header = (
    <PageHeader
      title="Evaluation results"
      actions={
        <Link className="button-link secondary" to={SCOREBOARD_PAGE}>
          Back to Scoreboard
        </Link>
      }
    >
      Review handler and agent decisions, explanation grades and the cost of
      working fresh copies.
    </PageHeader>
  );
  if (!run)
    return error instanceof ApiError && error.status === 404 ? (
      <PageHeader
        title="Evaluation not found"
        actions={
          <Link className="button-link secondary" to={SCOREBOARD_PAGE}>
            Back to Scoreboard
          </Link>
        }
      >
        Check the evaluation address or choose a result from the Scoreboard.
      </PageHeader>
    ) : (
      <>
        {header}
        <Message error={error} retry={reload} />
      </>
    );
  return (
    <>
      {header}
      {error && <Message error={error} retry={reload} />}
      <div className="column">
        <section className="panel">
          <EvaluationSummary run={run} />
        </section>
        <section className="panel" aria-label="Claim comparisons">
          <h2>Handler and agent decisions</h2>
          <p className="muted small">
            An undecided copy is scored as a referral with a zero payout. Scroll
            the table horizontally to read explanation grades and reasons.
          </p>
          <div className="table-scroll">
            <table className="table comparisons">
              <thead>
                <tr>
                  <th>Claim</th>
                  <th>Handler decision</th>
                  <th className="num">Handler payout (USD)</th>
                  <th>Agent decision</th>
                  <th className="num">Agent payout (USD)</th>
                  <th>Result</th>
                  <th className="num">Cost of mistake (USD)</th>
                  <th className="num">Model cost (USD)</th>
                  <th className="num">Active time (s)</th>
                  <th>Explanation grade</th>
                  <th>Explanations</th>
                </tr>
              </thead>
              <tbody>
                {run.comparisons.map((item) => (
                  <tr key={item.claim_id}>
                    <td>
                      <Link
                        className="mono nowrap"
                        to={claimPage(item.claim_id)}
                      >
                        {item.source_id}
                      </Link>
                    </td>
                    <td>
                      <DecisionChip decision={item.handler_decision} />
                    </td>
                    <td className="mono num">{money(item.handler_payout)}</td>
                    <td>
                      {item.decision ? (
                        <DecisionChip decision={item.decision} />
                      ) : (
                        "No decision (scored as referred)"
                      )}
                    </td>
                    <td className="mono num">{money(item.payout ?? 0)}</td>
                    <td>
                      <span className={`chip ${item.right ? "match" : "miss"}`}>
                        {item.right ? "Match" : "Miss"}
                      </span>
                    </td>
                    <td className="mono num">{money(item.mistake_cost)}</td>
                    <td className="mono num">{modelCost(item.llm_cost)}</td>
                    <td className="mono num">{seconds(item.active_seconds)}</td>
                    <td>
                      <Grade value={item} />
                    </td>
                    <td>
                      <p>
                        {item.reason ?? item.error ?? "No decision recorded"}
                      </p>
                      <p className="muted small">{item.agent_section}</p>
                      <details>
                        <summary>Handler reason</summary>
                        <p>{item.handler_reason}</p>
                        <p className="muted small">{item.handler_reference}</p>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </>
  );
}
