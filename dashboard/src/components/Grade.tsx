import type { Comparison } from "../api";
import { gradeScore, judgeCheck, NOT_GRADED } from "../format";

export function Grade({
  value,
}: Readonly<{
  value: Pick<Comparison, "grade" | "letter" | "same_grounds" | "same_section">;
}>) {
  return (
    <div className="grade-summary">
      <span className={`chip grade-${value.letter ?? "ungraded"}`}>
        {value.letter ?? NOT_GRADED}
      </span>
      {value.grade !== null && (
        <p className="mono">{gradeScore(value.grade)}</p>
      )}
      <p className="small">Same grounds: {judgeCheck(value.same_grounds)}</p>
      <p className="small">Same section: {judgeCheck(value.same_section)}</p>
    </div>
  );
}
