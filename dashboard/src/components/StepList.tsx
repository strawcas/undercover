import { useCallback } from "react";
import { api, type Step } from "../api";
import { time } from "../format";
import { REFRESH_MS, useLoad } from "../useLoad";
import { Message } from "./Message";

const SUMMARY_LENGTH = 90;

export function StepList({ claimId }: Readonly<{ claimId: string }>) {
  const load = useCallback(() => api.listSteps(claimId), [claimId]);
  const { data: steps, error, reload } = useLoad(load, REFRESH_MS);

  return (
    <section className="panel" aria-labelledby="steps-title">
      <h2 id="steps-title">Agent at work</h2>
      {(!steps || error) && <Message error={error} retry={reload} />}
      {steps?.length === 0 && (
        <p className="muted">
          No steps yet. They appear here as the agent works.
        </p>
      )}
      {steps && steps.length > 0 && <SectionsRead steps={steps} />}
      {steps && steps.length > 0 && (
        <ol className="steps">
          {steps.map((step) => (
            <li key={step.step_id} className="step">
              <span className="step-time small muted">
                {time(step.created_at)}
              </span>
              <StepBody step={step} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function SectionsRead({ steps }: Readonly<{ steps: Step[] }>) {
  const read = [
    ...new Set(steps.filter((s) => s.kind === "reading").map((s) => s.content)),
  ];
  if (read.length === 0) return null;
  return (
    <div className="sections-read">
      <h3>Sections read</h3>
      <ul>
        {read.map((section) => (
          <li key={section}>{section}</li>
        ))}
      </ul>
    </div>
  );
}

function StepBody({ step }: Readonly<{ step: Step }>) {
  if (step.kind === "tool_call")
    return (
      <p>
        <span className="step-label">Called</span>{" "}
        <code className="mono">{step.content}</code>
      </p>
    );
  if (step.kind === "tool_result") {
    const summary =
      step.content.length > SUMMARY_LENGTH
        ? `${step.content.slice(0, SUMMARY_LENGTH)}...`
        : step.content;
    return (
      <details>
        <summary>
          <span className="step-label">Result</span>{" "}
          <code className="mono">{summary}</code>
        </summary>
        <pre className="step-result">{step.content}</pre>
      </details>
    );
  }
  if (step.kind === "reading")
    return (
      <p>
        <span className="step-label">Read</span> {step.content}
      </p>
    );
  if (step.kind === "error")
    return (
      <p>
        <span className="chip step-failed">Failed</span> {step.content}
      </p>
    );
  return (
    <p>
      <span className="step-label">Agent</span> {step.content}
    </p>
  );
}
