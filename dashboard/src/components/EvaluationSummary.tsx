import type { Evaluation } from "../api";
import {
  evaluationName,
  gradeScore,
  modelCost,
  money,
  percent,
  seconds,
  timestamp,
} from "../format";
import { RunChip } from "./Chips";

export function EvaluationSummary({ run }: Readonly<{ run: Evaluation }>) {
  const measures = [
    ["Average explanation grade", gradeScore(run.scores.grade)],
    ["Decision match", percent(run.scores.decision_accuracy)],
    ["Accuracy within $1", percent(run.scores.accuracy)],
    ["Cost of mistakes", money(run.scores.cost_of_mistakes)],
    ["Straight through", percent(run.scores.straight_through)],
    ["Model cost / claim", modelCost(run.scores.model_cost_per_claim)],
    ["Active time / claim", seconds(run.scores.seconds_per_claim)],
    ["Run elapsed time", seconds(run.seconds)],
    ["Total model cost", modelCost(run.llm_cost)],
    ["Total judge cost", modelCost(run.judge_cost)],
    ["Total active claim time", seconds(run.active_seconds)],
  ];
  return (
    <>
      <p>
        <RunChip status={run.status} /> {evaluationName(run.set_name, run.tiny)}{" "}
        · {run.members.length} claims · N={run.concurrency}
      </p>
      <p className="small">
        Configuration version {run.configuration_version} · Model:{" "}
        <span className="mono">{run.model}</span> · Reasoning:{" "}
        {run.reasoning_effort ?? "default"}
      </p>
      <p className="muted small">
        Started {timestamp(run.started_at)}
        {run.finished_at && ` · Ended ${timestamp(run.finished_at)}`}
      </p>
      <dl className="evaluation-measures">
        {measures.map(([name, value]) => (
          <div key={name}>
            <dt>{name}</dt>
            <dd className="mono">{value}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}
