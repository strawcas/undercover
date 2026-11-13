import { useState } from "react";
import { api, type Round } from "../api";
import { useAuth } from "../auth";
import { RoundChip } from "../components/Chips";
import { RoundActionDialog } from "../components/RoundActionDialog";
import { RecipeHistory } from "../components/RecipeHistory";
import { useRoundActions } from "../useRoundActions";
import { PageHeader } from "../components/PageHeader";
import { RoundDetail } from "../components/RoundDetail";
import { VersionChart } from "../components/VersionChart";
import {
  gradeScore,
  modelCost,
  percent,
  roundClosed,
  roundMeasurePlaceholder,
  roundVerdictLabel,
  timestamp,
} from "../format";
import { REFRESH_MS, useLoad } from "../useLoad";

async function loadImprovement() {
  const [rounds, recipes, configurations] = await Promise.allSettled([
    api.listRounds(),
    api.listRecipes(),
    api.listConfigurations(),
  ]);
  return {
    rounds: settledValue(rounds),
    recipes: settledValue(recipes),
    configurations: settledValue(configurations),
  };
}

function settledValue<T>(result: PromiseSettledResult<T>): T {
  if (result.status === "fulfilled") return result.value;
  throw result.reason instanceof Error
    ? result.reason
    : new Error("The request failed");
}

function History({
  rounds,
  selected,
  onSelect,
}: Readonly<{
  rounds: Round[];
  selected?: string;
  onSelect: (identity: string) => void;
}>) {
  return (
    <section className="panel" aria-label="Round history">
      <h2>Round history</h2>
      {rounds.length === 0 ? (
        <p className="muted">
          No rounds yet. Start a round to establish baseline results and review
          a proposed change.
        </p>
      ) : (
        <div className="table-scroll">
          <table className="table round-history">
            <thead>
              <tr>
                <th>Round / started</th>
                <th>Versions</th>
                <th>State</th>
                <th>Verdict</th>
                <th className="num">Accuracy · before → after</th>
                <th className="num">Grade · before → after</th>
                <th className="num">Total (USD)</th>
              </tr>
            </thead>
            <tbody>
              {rounds.map((round) => (
                <tr
                  key={round.round_id}
                  aria-selected={selected === round.round_id}
                >
                  <th scope="row">
                    <button
                      type="button"
                      className="text-button mono"
                      onClick={() => {
                        onSelect(round.round_id);
                      }}
                    >
                      Review {round.round_id.slice(0, 8)}
                    </button>
                    <p className="muted small">{timestamp(round.started_at)}</p>
                  </th>
                  <td className="mono">
                    v{round.base_version} →{" "}
                    {round.candidate_version === null
                      ? "None"
                      : `v${String(round.candidate_version)}`}
                  </td>
                  <td>
                    <RoundChip status={round.status} />
                  </td>
                  <td>{roundVerdictLabel(round)}</td>
                  <td className="num mono">
                    {round.before
                      ? percent(round.before.accuracy)
                      : roundMeasurePlaceholder(round.status)}{" "}
                    →{" "}
                    {round.after
                      ? percent(round.after.accuracy)
                      : roundMeasurePlaceholder(round.status)}
                  </td>
                  <td className="num mono">
                    {round.before
                      ? gradeScore(round.before.grade)
                      : roundMeasurePlaceholder(round.status)}{" "}
                    →{" "}
                    {round.after
                      ? gradeScore(round.after.grade)
                      : roundMeasurePlaceholder(round.status)}
                  </td>
                  <td className="num mono">
                    {modelCost(round.spending.total)}
                    {round.spending.unknown ? " + unknown" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Workspace({
  data,
  senior,
  selected,
  onSelect,
  actions,
}: Readonly<{
  data: Awaited<ReturnType<typeof loadImprovement>>;
  senior: boolean;
  selected: string | null;
  onSelect: (identity: string) => void;
  actions: ReturnType<typeof useRoundActions>;
}>) {
  const open = data.rounds.find((round) => !roundClosed(round.status));
  const current =
    data.rounds.find((round) => round.round_id === selected) ??
    open ??
    data.rounds.at(0);
  return (
    <div className="column">
      {open && current?.round_id !== open.round_id && (
        <p className="notice">
          An open round is waiting for review.{" "}
          <button
            type="button"
            className="text-button"
            onClick={() => {
              onSelect(open.round_id);
            }}
          >
            Review open round
          </button>
        </p>
      )}
      {current && (
        <RoundDetail
          key={current.round_id}
          round={current}
          configurations={data.configurations}
          senior={senior}
          open={current.round_id === open?.round_id}
          onStop={() => {
            actions.choose({ kind: "stop", roundId: current.round_id });
          }}
          onDecide={(choice) => {
            actions.choose({ kind: "decision", ...choice });
          }}
        />
      )}
      <History
        rounds={data.rounds}
        selected={current?.round_id}
        onSelect={onSelect}
      />
      <VersionChart versions={data.configurations} />
      <RecipeHistory recipes={data.recipes} />
    </div>
  );
}

function startDisabled(
  data: Awaited<ReturnType<typeof loadImprovement>> | undefined,
  actions: ReturnType<typeof useRoundActions>,
) {
  if (!data || actions.busy) return true;
  if (data.rounds.some((round) => !roundClosed(round.status))) return true;
  return (
    actions.accepted !== null &&
    !data.rounds.some((round) => round.round_id === actions.accepted)
  );
}

export function ImprovementPage() {
  const { handler } = useAuth();
  const senior = handler?.can_approve_over_limit ?? false;
  const loaded = useLoad(loadImprovement, REFRESH_MS);
  const [selected, setSelected] = useState<string | null>(null);
  const actions = useRoundActions(loaded.reload, setSelected);
  const data = loaded.data;
  const open = data?.rounds.find((round) => !roundClosed(round.status));
  return (
    <>
      <PageHeader
        title="Improvement"
        actions={
          senior && (
            <button
              type="button"
              disabled={startDisabled(data, actions)}
              onClick={() => {
                actions.choose({ kind: "start" });
              }}
            >
              Start round
            </button>
          )
        }
      >
        Review proposed changes, approve their testing and follow the backend's
        keep or rollback verdict.
      </PageHeader>
      {!senior && (
        <p className="notice">
          You can review rounds and recipes. A senior handler is required to
          start or stop rounds and approve or reject proposals.
        </p>
      )}
      {open && (
        <p className="muted small round-notice">
          Finish or stop the open round before starting another.
        </p>
      )}
      {actions.announcement && (
        <p className="notice" role="status">
          {actions.announcement}
        </p>
      )}
      {loaded.error && (
        <div className="form-error" role="alert">
          <p>Could not refresh improvement history: {loaded.error.message}</p>
          <button type="button" className="secondary" onClick={loaded.reload}>
            Retry
          </button>
        </div>
      )}
      {!data && !loaded.error && (
        <p className="message" role="status">
          Loading improvement history...
        </p>
      )}
      {data && (
        <Workspace
          data={data}
          senior={senior}
          selected={selected}
          onSelect={setSelected}
          actions={actions}
        />
      )}
      <RoundActionDialog actions={actions} />
    </>
  );
}
