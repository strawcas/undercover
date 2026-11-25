import { Fragment } from "react";
import { Link } from "react-router";
import { claimPage } from "../paths";

const CLAIM = /^CLM-\d+$/;
const TOKEN = /(\[(?:CLM-\d+)\]\([^)]*\)|CLM-\d+|\*\*[^*]+\*\*|`[^`]+`)/g;
const TABLE_SEPARATOR = /^\|?[\s:|-]+$/;
const BULLET = /^[-*] /;
const NUMBERED = /^\d+\. /;

function inline(text: string) {
  return text.split(TOKEN).map((value, index) => {
    const linked = /^\[(CLM-\d+)\]/.exec(value)?.[1] ?? value;
    if (CLAIM.test(linked))
      return (
        <Link key={index} to={claimPage(linked)}>
          {linked}
        </Link>
      );
    if (value.startsWith("**"))
      return <strong key={index}>{inline(value.slice(2, -2))}</strong>;
    if (value.startsWith("`"))
      return <code key={index}>{inline(value.slice(1, -1))}</code>;
    return <Fragment key={index}>{value}</Fragment>;
  });
}

function cells(row: string) {
  return row
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());
}

function table(rows: string[]) {
  return (
    <div className="copilot-table" role="region" aria-label="Copilot results">
      <table>
        <thead>
          <tr>
            {cells(rows[0]).map((cell, index) => (
              <th key={index}>{inline(cell)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(2).map((row, index) => (
            <tr key={index}>
              {cells(row).map((cell, column) => (
                <td key={column}>{inline(cell)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function block(text: string) {
  const rows = text
    .split("\n")
    .map((row) => row.trim())
    .filter(Boolean);
  if (
    rows.length >= 2 &&
    rows[0].includes("|") &&
    TABLE_SEPARATOR.test(rows[1])
  )
    return table(rows);
  if (rows.length > 0 && rows.every((row) => BULLET.test(row)))
    return (
      <ul>
        {rows.map((row, index) => (
          <li key={index}>{inline(row.slice(2))}</li>
        ))}
      </ul>
    );
  if (rows.length > 0 && rows.every((row) => NUMBERED.test(row)))
    return (
      <ol>
        {rows.map((row, index) => (
          <li key={index}>{inline(row.replace(NUMBERED, ""))}</li>
        ))}
      </ol>
    );
  return <p>{inline(rows.join("\n").replace(/^#{1,6}\s+/, ""))}</p>;
}

function blocks(text: string) {
  return text.split(/\r?\n\s*\r?\n/).flatMap((paragraph) => {
    const groups: string[][] = [];
    let previous = "";
    for (const line of paragraph.split(/\r?\n/)) {
      const current = line.trim();
      let kind = "text";
      if (BULLET.test(current)) kind = "bullet";
      else if (NUMBERED.test(current)) kind = "number";
      if (kind !== previous) groups.push([]);
      groups.at(-1)?.push(current);
      previous = kind;
    }
    return groups.map((lines) => lines.join("\n"));
  });
}

export function CopilotMarkdown({ text }: Readonly<{ text: string }>) {
  return (
    <div className="copilot-markdown">
      {blocks(text).map((part, index) => (
        <div key={index}>{block(part)}</div>
      ))}
    </div>
  );
}
