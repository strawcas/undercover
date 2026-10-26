import { useState } from "react";
import {
  type QueueRow,
  type ClaimState,
  type InstructionsResponse,
  money,
  label,
  stateLabels,
  request,
} from "./api";
import { ClaimReader } from "./ClaimWorkflow";
import { useResource } from "./useResource";
import { RunProgress } from "./RunProgress";

const states: ClaimState[] = [
  "waiting",
  "in_progress",
  "awaiting_sign_off",
  "decided",
  "handler_review",
];

function watchQueue() {
  return true;
}

function Instructions() {
  const { data, error, refresh } = useResource<InstructionsResponse>(
    "/api/agent-instructions",
  );
  return (
    <section className="panel instructions">
      <p className="eyebrow">UnderCover Agent</p>
      <h2>Agent instructions</h2>
      <p className="muted">
        Read only · The instructions used for queue planning and claim
        assessment.
      </p>
      {error && (
        <p role="alert">
          {error} <button onClick={refresh}>Try again</button>
        </p>
      )}
      {data ? (
        <>
          <h3>Running the queue</h3>
          <pre>{data.queue_text}</pre>
          <h3>Working a claim</h3>
          <pre>{data.text}</pre>
        </>
      ) : (
        !error && <p role="status">Loading instructions…</p>
      )}
    </section>
  );
}

function Queue({
  rows,
  selected,
  onSelect,
}: Readonly<{
  rows: QueueRow[];
  selected: string | undefined;
  onSelect: (id: string) => void;
}>) {
  return (
    <section className="panel queue-panel">
      <div className="section-title">
        <h2>Claims queue</h2>
        <span className="count">{rows.length}</span>
      </div>
      <p className="muted queue-caption">
        Select a claim to review the details.
      </p>
      {rows.length === 0 && <p>No claims in the queue.</p>}
      <ul className="queue-list" aria-label="Claims queue">
        {rows.map((row) => (
          <li key={row.claim_id}>
            <button
              className={`queue-row ${selected === row.claim_id ? "selected" : ""}`}
              aria-label={`Open ${row.claim_id}`}
              aria-pressed={selected === row.claim_id}
              onClick={() => {
                onSelect(row.claim_id);
              }}
            >
              <span className="flex items-center justify-between gap-2">
                <span className="identifier">{row.claim_id}</span>
                <span className={`badge ${row.status}`}>
                  {stateLabels[row.status]}
                </span>
              </span>
              <strong>{row.customer_name}</strong>
              <span className="queue-summary">{row.summary}</span>
              <span className="flex items-center justify-between gap-2">
                <span>{label(row.claim_type)}</span>
                <span className="identifier">{money(row.amount_claimed)}</span>
              </span>
              {row.outcome && (
                <small>
                  {label(row.outcome)} · {label(row.payment_status)}
                </small>
              )}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function App() {
  const [page, setPage] = useState<"claims" | "instructions">("claims");
  const [selected, setSelected] = useState<string>();
  const {
    data: rows,
    error,
    refresh,
  } = useResource(
    "/api/claims",
    request<QueueRow[]>,
    page === "claims" ? watchQueue : undefined,
  );
  const activeId = selected ?? rows?.[0]?.claim_id;
  return (
    <div className="workspace">
      <aside className="sidebar">
        <a className="wordmark" href="/" aria-label="UnderCover home">
          <span className="brand-icon" aria-hidden="true">
            u
          </span>
          undercover<span className="brand-dot">.</span>
        </a>
        <p className="sidebar-label">Claims operations</p>
        <nav aria-label="Workspace">
          <button
            className={page === "claims" ? "active" : ""}
            onClick={() => {
              setPage("claims");
            }}
          >
            <span aria-hidden="true">≡</span>Claims queue
          </button>
          <button
            className={page === "instructions" ? "active" : ""}
            onClick={() => {
              setPage("instructions");
            }}
          >
            <span aria-hidden="true">⌘</span>Agent instructions
          </button>
        </nav>
        <div className="sidebar-footer">
          <span className="pilot-indicator" />
          Local pilot<p>Travel a little lighter.</p>
          <small>UnderCover Travel Insurance</small>
        </div>
      </aside>
      <main className="main">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">UnderCover / Claims operations</p>
            <h1>
              Claims desk<span className="heading-dot">.</span>
            </h1>
            <p className="muted">
              A clear view of every claim and its next step.
            </p>
          </div>
          <span className="environment">
            Pilot workspace <span>USD</span>
          </span>
        </header>
        <section className="stats" aria-label="Queue overview">
          {states.map((state, index) => (
            <div className={`stat stat-${String(index)}`} key={state}>
              <span>{stateLabels[state]}</span>
              <strong>
                {rows ? rows.filter((row) => row.status === state).length : "—"}
              </strong>
              <small>
                {state === "awaiting_sign_off"
                  ? "Handler review needed"
                  : "Claims in your queue"}
              </small>
            </div>
          ))}
        </section>
        {page === "instructions" ? (
          <Instructions />
        ) : (
          <>
            <RunProgress onSelect={setSelected} />
            {error && (
              <div className="panel" role="alert">
                <h2>Unable to load the queue</h2>
                <p>{error}</p>
                <button onClick={refresh}>Try again</button>
              </div>
            )}
            {!rows && !error && (
              <p className="panel" role="status">
                Loading claims…
              </p>
            )}
            {rows && (
              <div className="claims-layout">
                <Queue rows={rows} selected={activeId} onSelect={setSelected} />
                {activeId ? (
                  <ClaimReader
                    key={activeId}
                    claimId={activeId}
                    onChange={refresh}
                  />
                ) : (
                  <div className="panel empty-detail">Your queue is clear.</div>
                )}
              </div>
            )}
          </>
        )}
        <footer className="page-footer">
          UnderCover Travel Insurance{" "}
          <span>All amounts in USD · Simulated payments only</span>
        </footer>
      </main>
    </div>
  );
}
