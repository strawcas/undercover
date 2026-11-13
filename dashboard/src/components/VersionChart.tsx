import { VersionScoresTable } from "./VersionScoresTable";
import type { ConfigurationHistory } from "../api";
import { gradeScore, percent } from "../format";

function MeasureChart({
  versions,
  measure,
}: Readonly<{
  versions: ConfigurationHistory[];
  measure: "accuracy" | "grade";
}>) {
  const accuracy = measure === "accuracy";
  const maximum = accuracy ? 1 : 3;
  const width = Math.max(440, versions.length * 80 + 100);
  const x = (index: number) =>
    versions.length === 1
      ? width / 2
      : 65 + (index * (width - 110)) / Math.max(1, versions.length - 1);
  const y = (value: number) => 155 - (value / maximum) * 125;
  const values = versions.map(
    (version) => version.held_out_scores?.[measure] ?? null,
  );
  const name = accuracy
    ? "Held-out accuracy (0–100%)"
    : "Held-out judge grade (0–3)";
  return (
    <div className={`version-chart ${measure}`}>
      <h3>{name}</h3>
      <div className="table-scroll">
        <svg
          role="img"
          aria-label={name}
          viewBox={`0 0 ${String(width)} 205`}
          width={width}
          height="205"
        >
          <title>{name}. The equivalent values are in the table below.</title>
          {[0, 0.5, 1].map((fraction) => (
            <g key={fraction}>
              <line
                x1="55"
                x2={width - 25}
                y1={y(fraction * maximum)}
                y2={y(fraction * maximum)}
                className="chart-grid"
              />
              <text x="45" y={y(fraction * maximum) + 4} textAnchor="end">
                {accuracy
                  ? `${String(fraction * 100)}%`
                  : String(fraction * maximum)}
              </text>
            </g>
          ))}
          {versions.map((version, index) => {
            const value = values[index];
            const previous = values[index - 1];
            return (
              <g key={version.version}>
                {value !== null && index > 0 && previous !== null && (
                  <line
                    className="chart-series"
                    x1={x(index - 1)}
                    y1={y(previous)}
                    x2={x(index)}
                    y2={y(value)}
                  />
                )}{" "}
                {value !== null ? (
                  <circle
                    className="chart-point"
                    cx={x(index)}
                    cy={y(value)}
                    r="5"
                  >
                    <title>{`Version ${String(version.version)}: ${accuracy ? percent(value) : gradeScore(value)}`}</title>
                  </circle>
                ) : (
                  <text x={x(index)} y="105" textAnchor="middle">
                    No result
                  </text>
                )}
                <text x={x(index)} y="185" textAnchor="middle">
                  v{version.version}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

export function VersionChart({
  versions,
}: Readonly<{ versions: ConfigurationHistory[] }>) {
  const ordered = [...versions].sort((a, b) => a.version - b.version);
  return (
    <section className="panel" aria-label="Version performance">
      <h2>Held-out performance by version</h2>
      <p className="muted small">
        Averages of completed whole-set round evaluations. Missing results
        remain unavailable, including versions whose testing stopped.
      </p>
      {ordered.length === 0 ? (
        <p className="message">No configuration versions are available yet.</p>
      ) : (
        <>
          <div className="chart-pair">
            <MeasureChart versions={ordered} measure="accuracy" />
            <MeasureChart versions={ordered} measure="grade" />
          </div>
          <VersionScoresTable
            versions={ordered}
            label="Version performance values"
          />
        </>
      )}
    </section>
  );
}
