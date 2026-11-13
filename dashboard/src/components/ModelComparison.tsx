import type { Configuration } from "../api";

type ModelSelection = Pick<Configuration, "model" | "reasoning_effort">;

export function ModelComparison({
  before,
  after,
  beforeLabel,
  afterLabel,
  emptyBefore = "Unavailable",
}: Readonly<{
  before: ModelSelection | undefined;
  after: ModelSelection;
  beforeLabel: string;
  afterLabel: string;
  emptyBefore?: string;
}>) {
  return (
    <dl className="proposal-models">
      <div>
        <dt>{beforeLabel}</dt>
        <dd>
          {before
            ? `${before.model} · ${before.reasoning_effort}`
            : emptyBefore}
        </dd>
      </div>
      <div>
        <dt>{afterLabel}</dt>
        <dd>
          {after.model} · {after.reasoning_effort}
        </dd>
      </div>
    </dl>
  );
}
