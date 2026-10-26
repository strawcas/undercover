import { type EvidenceResponse } from "./api";

export function EvidencePanel({
  evidence,
}: Readonly<{ evidence: EvidenceResponse }>) {
  return (
    <section className="panel evidence" aria-label="Sections read">
      <div className="section-title">
        <h3>Sections read</h3>
        <span>{evidence.reads.length} reads</span>
      </div>
      {evidence.reads.length ? (
        evidence.reads.map((read) => (
          <details key={read.evidence_id}>
            <summary>
              {read.source === "policy_wording"
                ? "Policy wording"
                : "Claims manual"}{" "}
              · {read.section_id} · {read.title}
            </summary>
            <p className="muted">
              Version {read.version} · Read{" "}
              {new Date(read.read_at).toLocaleString()}
            </p>
            <p className="muted run-identity">Attempt {read.thread_id}</p>
            <pre>{read.text}</pre>
          </details>
        ))
      ) : (
        <p className="muted">
          Full sections returned to the agent will appear here. Search snippets
          are not evidence reads.
        </p>
      )}
    </section>
  );
}
