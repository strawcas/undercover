import type { ConfigurationHistory } from "../api";
import { gradeScore, modelCost, percent, seconds } from "../format";

export function VersionScoresTable({
  versions,
  label = "Held-out version scores",
}: Readonly<{ versions: ConfigurationHistory[]; label?: string }>) {
  return (
    <div className="table-scroll">
      <table className="table" aria-label={label}>
        <thead>
          <tr>
            <th>Version</th>
            <th className="num">Accuracy</th>
            <th className="num">Grade / 3</th>
            <th className="num">Model cost / claim</th>
            <th className="num">Active time / claim</th>
          </tr>
        </thead>
        <tbody>
          {versions.map((version) => (
            <tr key={version.version}>
              <th scope="row">v{version.version}</th>
              <td className="num mono">
                {version.held_out_scores
                  ? percent(version.held_out_scores.accuracy)
                  : "No evaluations"}
              </td>
              <td className="num mono">
                {gradeScore(version.held_out_scores?.grade ?? null)}
              </td>
              <td className="num mono">
                {modelCost(
                  version.held_out_scores?.model_cost_per_claim ?? null,
                )}
              </td>
              <td className="num mono">
                {version.held_out_scores
                  ? seconds(version.held_out_scores.seconds_per_claim)
                  : "Unavailable"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
