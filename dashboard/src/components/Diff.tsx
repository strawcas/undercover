function changeClass(line: string) {
  if (line.startsWith("+++") || line.startsWith("---")) return "diff-context";
  if (line.startsWith("+")) return "diff-add";
  if (line.startsWith("-")) return "diff-remove";
  return "diff-context";
}

export function Diff({ text }: Readonly<{ text: string }>) {
  if (!text) return <p className="muted">No text changes.</p>;
  return (
    <pre className="diff" aria-label="Text changes">
      {text.split("\n").map((line, index) => (
        <span key={`${String(index)}:${line}`} className={changeClass(line)}>
          {line || " "}
        </span>
      ))}
    </pre>
  );
}
