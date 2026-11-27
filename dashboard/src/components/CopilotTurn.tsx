import { Link } from "react-router";
import type { CopilotStep, CopilotTurn as Turn } from "../copilotTypes";
import { label, modelCost, money, seconds } from "../format";
import { claimPage } from "../paths";
import { CopilotMarkdown } from "./CopilotMarkdown";

const icons: Record<CopilotStep["kind"], string> = {
  claim: "▤",
  customer: "◉",
  context: "◫",
  work: "↻",
  memory: "◇",
  action: "→",
};

function displayInput(input: string): string {
  try {
    const value: unknown = JSON.parse(input);
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return Object.values(value)
        .filter((item) => item !== null)
        .map(String)
        .join(" · ");
    }
  } catch {
    return input;
  }
  return input;
}

function StepCard({
  step,
  now,
  active,
}: Readonly<{ step: CopilotStep; now: number; active: boolean }>) {
  const elapsed =
    step.seconds ?? Math.max(0, (now - Date.parse(step.started_at)) / 1000);
  return (
    <details className={`copilot-step copilot-step--${step.kind}`}>
      <summary>
        <span aria-hidden="true" className="copilot-step-icon">
          {icons[step.kind]}
        </span>
        <span className="copilot-step-name">
          <strong>{step.tool}</strong>
          {displayInput(step.input) && <span>{displayInput(step.input)}</span>}
        </span>
        <span className="copilot-step-status">
          {step.status === "working" && active && (
            <span className="copilot-spinner" aria-hidden="true" />
          )}
          {step.status === "working" && !active
            ? "Interrupted"
            : `${label(step.status)} · ${seconds(elapsed)}`}
        </span>
      </summary>
      <div className="copilot-step-detail">
        {step.reason && <p>{step.reason}</p>}
        {step.input && <pre>{step.input}</pre>}
        {step.result !== null && (
          <pre>
            {typeof step.result === "string"
              ? step.result
              : JSON.stringify(step.result, null, 2)}
          </pre>
        )}
      </div>
    </details>
  );
}

interface Props {
  turn: Turn;
  now: number;
  busy: boolean;
  onConfirm: (turnId: string, confirmed: boolean) => void;
}

export function CopilotTurn({ turn, now, busy, onConfirm }: Readonly<Props>) {
  const card = turn.confirmation;
  const usage = turn.usage;
  return (
    <article className="copilot-turn">
      <p className="copilot-question">{turn.message}</p>
      {turn.steps.map((step) => (
        <StepCard
          key={step.step_id}
          step={step}
          now={now}
          active={turn.status === "working"}
        />
      ))}
      {turn.status === "working" && (
        <p className="copilot-working" role="status">
          <span className="copilot-spinner" aria-hidden="true" /> Copilot is
          working
        </p>
      )}
      {turn.status === "waiting" && card && (
        <section className="copilot-confirm" aria-label="Confirm payout">
          <h3>Confirm payout of {money(card.amount)}</h3>
          <p>
            <Link to={claimPage(card.claim_id)}>{card.claim_id}</Link> ·{" "}
            {card.customer_name}
          </p>
          <p>{card.reason}</p>
          <div className="actions">
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => {
                onConfirm(turn.turn_id, true);
              }}
            >
              Sign off and pay
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                onConfirm(turn.turn_id, false);
              }}
            >
              Cancel
            </button>
          </div>
        </section>
      )}
      {turn.failure && (
        <p className="copilot-answer form-error">{turn.failure}</p>
      )}
      {turn.answer && (
        <div className="copilot-answer">
          <CopilotMarkdown text={turn.answer} />
        </div>
      )}
      {turn.answer && usage && (
        <p className="copilot-usage">
          {usage.model} · {usage.calls} model{" "}
          {usage.calls === 1 ? "call" : "calls"} · {modelCost(usage.cost)} ·{" "}
          {seconds(usage.seconds)}
        </p>
      )}
    </article>
  );
}
