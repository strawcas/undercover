import { useCallback } from "react";
import { api, type Step } from "../api";
import { time } from "../format";
import { REFRESH_MS, useLoad } from "../useLoad";
import { ContextUsed, SourceSummary } from "./ContextUsed";
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
      {steps && <ContextUsed steps={steps} />}
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

function StepBody({ step }: Readonly<{ step: Step }>) {
  if (step.kind === "lookup")
    return (
      <div>
        <p>
          <span className="step-label">Looked up</span> {step.content.subject}
        </p>
        {step.content.sources.map((source) => (
          <SourceSummary key={source.file} source={source} />
        ))}
      </div>
    );
  if (step.kind === "memory_search")
    return (
      <div>
        <p>
          <span className="step-label">Searched memory</span>{" "}
          {step.content.query}
        </p>
        {step.content.results.length ? (
          <ul>
            {step.content.results.map((result, index) => (
              <li key={`${String(index)}-${result}`}>{result}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">No earlier memories found.</p>
        )}
      </div>
    );
  if (step.kind === "memory_save")
    return (
      <div>
        <p className="step-label">Saved to memory</p>
        {step.content.messages.map((message) => (
          <details key={message.role}>
            <summary>
              {message.role === "customer" ? "Claim summary" : "Agent decision"}
            </summary>
            <pre className="step-result">{message.text}</pre>
          </details>
        ))}
      </div>
    );
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
  if (step.kind === "refusal")
    return (
      <p>
        <span className="chip step-failed">
          Refused by {step.content.source}
        </span>{" "}
        {step.content.reason}
      </p>
    );
  if (step.kind === "security")
    return (
      <p>
        <span className="step-label">Security check</span> {step.content}
      </p>
    );
  return (
    <p>
      <span className="step-label">Agent</span> {step.content}
    </p>
  );
}
