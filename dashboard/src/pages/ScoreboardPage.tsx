import { useState } from "react";
import { Link } from "react-router";
import { api, type EvaluationSet } from "../api";
import { EvaluationSummary } from "../components/EvaluationSummary";
import { Message } from "../components/Message";
import { RunStartForm } from "../components/RunStartForm";
import { evaluationName, timestamp } from "../format";
import { evaluationPage } from "../paths";
import { REFRESH_MS, useLoad } from "../useLoad";

export function ScoreboardPage() {
  const [setName, setSetName] = useState<EvaluationSet>("evaluation");
  const [tiny, setTiny] = useState(false);
  const { data: runs, error } = useLoad(api.listEvaluations, REFRESH_MS);
  return (
    <>
      <h1>Scoreboard</h1>
      <section className="panel" aria-label="Start an evaluation">
        <h2>Start an evaluation</h2>
        <p>
          Compare the agent with our handlers on fresh copies. The queue stays
          as it is.
        </p>
        <RunStartForm
          name="Evaluation"
          start={(n) => api.startEvaluation(setName, tiny, n)}
        >
          <label>
            Claim set
            <select
              value={setName}
              onChange={(event) => {
                setSetName(event.target.value as EvaluationSet);
              }}
            >
              <option value="evaluation">Evaluation</option>
              <option value="held_out">Held-out</option>
            </select>
          </label>
          <label>
            Selection
            <select
              value={tiny ? "tiny" : "full"}
              onChange={(event) => {
                setTiny(event.target.value === "tiny");
              }}
            >
              <option value="full">Full (25 claims)</option>
              <option value="tiny">Tiny (5 claims)</option>
            </select>
          </label>
        </RunStartForm>
      </section>
      {runs === undefined && <Message error={error} />}
      {runs?.length === 0 && <p className="message">No evaluations yet.</p>}
      <ol className="evaluations" aria-label="Evaluations">
        {runs?.map((run) => (
          <li className="panel" key={run.run_id}>
            <h2>
              <Link to={evaluationPage(run.run_id)}>
                {evaluationName(run.set_name, run.tiny)} ·{" "}
                {timestamp(run.started_at)}
              </Link>
            </h2>
            <EvaluationSummary run={run} />
          </li>
        ))}
      </ol>
    </>
  );
}
