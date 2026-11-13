import type { Scores } from "../api";
import { gradeScore, modelCost, percent, seconds } from "../format";

export function RoundScores({
  before,
  after,
  emptyLabel,
}: Readonly<{
  before: Scores | null;
  after: Scores | null;
  emptyLabel: string;
}>) {
  const rows = [
    [
      "Accuracy",
      before ? percent(before.accuracy) : emptyLabel,
      after ? percent(after.accuracy) : emptyLabel,
    ],
    [
      "Judge grade / 3",
      before ? gradeScore(before.grade) : emptyLabel,
      after ? gradeScore(after.grade) : emptyLabel,
    ],
    [
      "Model cost / claim (USD)",
      before ? modelCost(before.model_cost_per_claim) : emptyLabel,
      after ? modelCost(after.model_cost_per_claim) : emptyLabel,
    ],
    [
      "Active time / claim",
      before ? seconds(before.seconds_per_claim) : emptyLabel,
      after ? seconds(after.seconds_per_claim) : emptyLabel,
    ],
  ];
  return (
    <div className="table-scroll">
      <table className="table" aria-label="Held-out before and after">
        <thead>
          <tr>
            <th>Held-out measure</th>
            <th className="num">Baseline</th>
            <th className="num">Candidate</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, oldValue, newValue]) => (
            <tr key={name}>
              <th scope="row">{name}</th>
              <td className="num mono">{oldValue}</td>
              <td className="num mono">{newValue}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
