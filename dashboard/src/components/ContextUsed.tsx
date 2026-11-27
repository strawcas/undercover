import type { ContextSource, Step } from "../api";
import { date } from "../format";

export function SourceSummary({ source }: Readonly<{ source: ContextSource }>) {
  return (
    <>
      <code>{source.file}</code>
      <p className="small muted">
        {source.system} · {source.owner} · Reviewed {date(source.reviewed)}
      </p>
    </>
  );
}

export function ContextUsed({ steps }: Readonly<{ steps: Step[] }>) {
  const byFile = new Map<
    string,
    { source: ContextSource; subjects: Set<string> }
  >();
  for (const step of steps) {
    if (step.kind !== "lookup") continue;
    for (const source of step.content.sources) {
      const entry = byFile.get(source.file) ?? {
        source,
        subjects: new Set<string>(),
      };
      entry.subjects.add(step.content.subject);
      byFile.set(source.file, entry);
    }
  }
  if (byFile.size === 0) return null;
  return (
    <section className="context-used" aria-labelledby="context-used-title">
      <h3 id="context-used-title">Context used</h3>
      {[...byFile.values()].map(({ source, subjects }) => (
        <div key={source.file}>
          <SourceSummary source={source} />
          <ul>
            {[...subjects].map((subject) => (
              <li key={subject}>{subject}</li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
