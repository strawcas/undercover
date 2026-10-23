import { type ReactNode } from "react";
import {
  type ClaimDetail as Detail,
  type JsonValue,
  label,
  money,
  stateLabels,
} from "./api";

function RecordValue({ value }: Readonly<{ value: JsonValue }>) {
  if (value === null) return <span>None</span>;
  if (Array.isArray(value))
    return value.length ? (
      <ul className="record-list">
        {value.map((item, index) => (
          <li key={index}>
            <RecordValue value={item} />
          </li>
        ))}
      </ul>
    ) : (
      <span>None</span>
    );
  if (typeof value === "object") return <RecordFields record={value} />;
  if (typeof value === "boolean") return <span>{value ? "Yes" : "No"}</span>;
  return <span>{String(value)}</span>;
}

function RecordFields({
  record,
}: Readonly<{ record: Record<string, JsonValue> }>) {
  return (
    <dl className="record-fields">
      {Object.entries(record).map(([key, value]) => (
        <div key={key}>
          <dt>{label(key)}</dt>
          <dd>
            <RecordValue value={value} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function DecisionPanel({ claim }: Readonly<{ claim: Detail }>) {
  const decision = claim.decision;
  if (!decision)
    return (
      <section className="panel decision-empty">
        <div className="agent-symbol" aria-hidden="true">
          u
        </div>
        <div>
          <h3>Ready for a closer look</h3>
          <p>The agent's decision and policy references will appear here.</p>
        </div>
      </section>
    );
  return (
    <section className="panel decision-panel" aria-label="Agent decision">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="eyebrow">Agent decision</p>
          <h3>{label(decision.outcome)}</h3>
        </div>
        <strong className="payout">{money(decision.payout)}</strong>
      </div>
      <p>{decision.explanation}</p>
      <div className="calculation">
        <span className="eyebrow">Payout calculation</span>
        <p>{decision.calculation}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {decision.policy_sections.map((section) => (
          <span className="policy-chip" key={section}>
            Policy section {section}
          </span>
        ))}
      </div>
      {claim.status === "awaiting_sign_off" && (
        <p className="notice">Payment is held until a handler signs off.</p>
      )}
      {claim.sign_off && (
        <p className="notice">
          Signed off by {claim.sign_off.handler_name} for{" "}
          {money(claim.sign_off.amount)} at{" "}
          <time>{new Date(claim.sign_off.created_at).toLocaleString()}</time>.
        </p>
      )}
      {claim.payment && (
        <p className="notice">
          Simulated payment recorded: {money(claim.payment.amount)} at{" "}
          <time>{new Date(claim.payment.created_at).toLocaleString()}</time>. No
          real money was transferred.
        </p>
      )}
    </section>
  );
}

function ClaimRecords({ claim }: Readonly<{ claim: Detail }>) {
  return (
    <>
      <section className="panel">
        <div className="section-title">
          <h3>What happened</h3>
          <span>{claim.incident_location}</span>
        </div>
        <p className="story">{claim.story}</p>
        <dl className="facts">
          <div>
            <dt>Incident date</dt>
            <dd>{claim.incident_date}</dd>
          </div>
          <div>
            <dt>Submitted</dt>
            <dd>{claim.submitted_on}</dd>
          </div>
        </dl>
      </section>
      <section className="panel">
        <div className="section-title">
          <h3>Items claimed</h3>
          <span>
            {claim.items.length} {claim.items.length === 1 ? "item" : "items"}
          </span>
        </div>
        <table className="items-table">
          <caption className="sr-only">Claimed expenses in USD</caption>
          <thead>
            <tr>
              <th scope="col">Description</th>
              <th scope="col">Amount</th>
            </tr>
          </thead>
          <tbody>
            {claim.items.map((item, index) => (
              <tr key={index}>
                <td>
                  {item.description}
                  <small>{label(item.category)}</small>
                </td>
                <td>{money(item.amount)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row">Total claimed</th>
              <td>{money(claim.amount_claimed)}</td>
            </tr>
          </tfoot>
        </table>
      </section>
      <section className="panel">
        <div className="section-title">
          <h3>Supporting documents</h3>
          <span>
            {claim.documents.length}{" "}
            {claim.documents.length === 1 ? "document" : "documents"}
          </span>
        </div>
        {claim.documents.map((document, index) => (
          <details className="document" key={index}>
            <summary>
              {document.name}
              <small>{label(document.type)}</small>
            </summary>
            <p>{document.content}</p>
          </details>
        ))}
      </section>
      <section className="panel records">
        <h3>Policy and customer records</h3>
        <details>
          <summary>Policy · {claim.policy_id}</summary>
          <RecordFields record={claim.policy} />
        </details>
        <details>
          <summary>Plan limits and deductible</summary>
          <RecordFields record={claim.plan} />
          {claim.add_ons.map((addOn, index) => (
            <RecordFields record={addOn} key={index} />
          ))}
        </details>
        <details>
          <summary>Customer and claim history · {claim.customer_id}</summary>
          <RecordFields record={claim.customer} />
        </details>
      </section>
    </>
  );
}

export function ClaimView({
  claim,
  children,
}: Readonly<{
  claim: Detail;
  children?: ReactNode;
}>) {
  return (
    <article className="claim-detail" aria-label={`Claim ${claim.claim_id}`}>
      <header className="panel claim-header">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="identifier">
            {claim.claim_id}{" "}
            <span className="muted">/ {label(claim.claim_type)}</span>
          </span>
          <span className={`badge ${claim.status}`}>
            {stateLabels[claim.status]}
          </span>
        </div>
        <h2>{claim.summary}</h2>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <p className="muted">
            {claim.customer.name}
            <br />
            <span className="identifier">{claim.policy_id}</span>
          </p>
          <div className="claimed-total">
            <span>Amount claimed</span>
            <strong>{money(claim.amount_claimed)}</strong>
          </div>
        </div>
      </header>
      {children}
      <DecisionPanel claim={claim} />
      <ClaimRecords claim={claim} />
    </article>
  );
}
