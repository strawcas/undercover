import { useState } from "react";
import { api } from "../api";
import { Diff } from "../components/Diff";
import { Message } from "../components/Message";
import { gradeScore, modelCost, percent, seconds, time } from "../format";
import { REFRESH_MS, useLoad } from "../useLoad";

export function ConfigurationPage() {
  const current = useLoad(api.getCurrentConfiguration, REFRESH_MS);
  const history = useLoad(api.listConfigurations, REFRESH_MS);
  const [selected, setSelected] = useState<number | null>(null);
  const value = history.data?.find(
    (item) => item.version === (selected ?? current.data?.version),
  );
  const failure = history.error ?? current.error;
  const placeholder =
    history.data?.length === 0 && !failure ? (
      <p className="message">
        No configurations are available. Contact a senior handler.
      </p>
    ) : (
      <Message error={failure} />
    );
  return (
    <>
      <h1>Configuration</h1>
      <p className="lead">
        Review the claim agent's configuration, its history and the documents it
        works from.
      </p>
      {value && failure && <Message error={failure} />}
      {!value ? (
        placeholder
      ) : (
        <div className="column">
          <section className="panel table-scroll" aria-label="Version scores">
            <h2>Held-out scores by version</h2>
            <p className="muted small">
              Averages of completed whole-set evaluations run by improvement
              rounds.
            </p>
            <table className="table">
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
                {history.data?.map((version) => (
                  <tr key={version.version}>
                    <th scope="row">{version.version}</th>
                    <td className="num">
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
          </section>
          <section className="panel" aria-label="Configuration versions">
            <h2>Current version {current.data?.version}</h2>
            <label>
              Version
              <select
                value={value.version}
                onChange={(event) => {
                  setSelected(Number(event.target.value));
                }}
              >
                {history.data?.map((item) => (
                  <option key={item.version} value={item.version}>
                    Version {item.version}
                  </option>
                ))}
              </select>
            </label>
            <dl className="facts">
              <div>
                <dt>Model</dt>
                <dd className="mono">{value.model}</dd>
              </div>
              <div>
                <dt>Reasoning effort</dt>
                <dd>{value.reasoning_effort}</dd>
              </div>
              <div>
                <dt>Created</dt>
                <dd>{time(value.created_at)}</dd>
              </div>
              <div>
                <dt>Approved by</dt>
                <dd>{value.approved_by ?? "Initial version"}</dd>
              </div>
            </dl>
            <p>{value.reason}</p>
            <p className="muted">
              Held-out accuracy:{" "}
              {value.held_out_scores
                ? `${(value.held_out_scores.accuracy * 100).toFixed(1)}%`
                : "No round evaluations yet"}
            </p>
          </section>
          <section className="panel" aria-label="Changes">
            <h2>Changes from version {value.parent_version ?? "start"}</h2>
            <Diff text={value.diff} />
          </section>
          <section className="panel" aria-label="Working a claim">
            <h2>Working a claim</h2>
            <pre className="instructions">{value.prompt}</pre>
          </section>
        </div>
      )}
    </>
  );
}
