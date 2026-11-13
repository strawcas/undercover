import { type SyntheticEvent, useState } from "react";
import { api, type Measures, type PlatformSettings } from "../api";
import { useAuth } from "../auth";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Message } from "../components/Message";
import { money, time } from "../format";
import { getTheme, setTheme, type Theme } from "../theme";
import { REFRESH_MS, useLoad } from "../useLoad";

const measureNames = {
  accuracy: "Accuracy",
  grade: "Grade",
  cost_per_claim: "Cost per claim",
  seconds_per_claim: "Time per claim",
};
const measureMax: Record<keyof Measures, number | undefined> = {
  accuracy: 1,
  grade: 3,
  cost_per_claim: undefined,
  seconds_per_claim: undefined,
};

export function SettingsPage() {
  const { data, error, reload } = useLoad(api.getSettings, REFRESH_MS);
  const [theme, updateTheme] = useState(getTheme);
  return (
    <>
      <h1>Settings</h1>
      <p className="lead">
        Set the business rules and limits for future improvement rounds and new
        decisions.
      </p>
      {data && error && <Message error={error} />}
      <div className="column">
        <section className="panel" aria-label="Appearance">
          <h2>Appearance</h2>
          <label>
            Theme
            <select
              value={theme}
              onChange={(event) => {
                const next = event.target.value as Theme;
                updateTheme(next);
                setTheme(next);
              }}
            >
              <option value="system">Same as computer</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
        </section>
        {data ? (
          <>
            <SettingsEditor initial={data.values} onSaved={reload} />
            <section className="panel" aria-label="Allowed models">
              <h2>Allowed models</h2>
              <p className="muted">
                Maintained in the model catalogue. Prices are US dollars per
                million tokens.
              </p>
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th>Reasoning efforts</th>
                      <th className="num">Input / 1M tokens</th>
                      <th className="num">Output / 1M tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.models_allowed).map(
                      ([name, model]) => (
                        <tr key={name}>
                          <td className="mono">{name}</td>
                          <td>{model.reasoning_efforts.join(", ")}</td>
                          <td className="num mono">
                            {money(model.input_price)}
                          </td>
                          <td className="num mono">
                            {money(model.output_price)}
                          </td>
                        </tr>
                      ),
                    )}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="panel" aria-label="Settings history">
              <h2>Settings history</h2>
              {data.changes.length ? (
                <ul className="history">
                  {data.changes.map((change) => (
                    <li key={change.id}>
                      {change.handler} · {time(change.changed_at)}
                      <details>
                        <summary>View changes</summary>
                        <pre className="instructions">
                          {JSON.stringify(
                            { before: change.before, after: change.after },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">No platform settings have changed.</p>
              )}
            </section>
          </>
        ) : (
          <Message error={error} />
        )}
      </div>
    </>
  );
}

function SettingsEditor({
  initial,
  onSaved,
}: Readonly<{ initial: PlatformSettings; onSaved: () => void }>) {
  const { handler } = useAuth();
  const [form, setForm] = useState({
    source: initial,
    values: initial,
    dirty: false,
  });
  const [confirming, setConfirming] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const [saved, setSaved] = useState(false);
  const canEdit = handler?.can_approve_over_limit === true;
  if (initial !== form.source) {
    setForm({
      source: initial,
      values: form.dirty ? form.values : initial,
      dirty:
        form.dirty && JSON.stringify(form.values) !== JSON.stringify(initial),
    });
  }
  const values = canEdit ? form.values : initial;
  function setValues(next: PlatformSettings) {
    setSaved(false);
    setForm({
      source: initial,
      values: next,
      dirty: JSON.stringify(next) !== JSON.stringify(initial),
    });
  }
  function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    setConfirming(true);
  }
  async function save() {
    setConfirming(false);
    setSending(true);
    setError(undefined);
    setSaved(false);
    try {
      const result = await api.changeSettings(values);
      setForm({ source: initial, values: result.values, dirty: false });
      setSaved(true);
      onSaved();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Settings could not be saved. Try again.",
      );
    } finally {
      setSending(false);
    }
  }
  return (
    <section className="panel" aria-label="Platform settings">
      <h2>Platform settings</h2>
      {!canEdit && (
        <p className="message">
          You can view settings. A senior handler is required to change them.
        </p>
      )}
      <p className="muted">
        Accuracy uses a 0–1 fraction and grade a 0–3 score. Noise and tolerance
        for cost and time are fractions of the old value: 0.15 means 15%.
      </p>
      <form className="stack-form" onSubmit={submit}>
        <fieldset disabled={!canEdit || sending}>
          <legend>Business limits</legend>
          <div className="settings-grid">
            <NumberField
              label="Approval limit (USD)"
              value={values.payout_approval_limit}
              onChange={(value) => {
                setValues({ ...values, payout_approval_limit: value });
              }}
            />
            <NumberField
              label="Round spending limit (USD)"
              value={values.spending_limit}
              onChange={(value) => {
                setValues({ ...values, spending_limit: value });
              }}
            />
            <NumberField
              label="Agents at once in round evaluations"
              value={values.concurrency}
              max={25}
              min={1}
              step={1}
              onChange={(value) => {
                setValues({ ...values, concurrency: value });
              }}
            />
            <NumberField
              label="Held-out accuracy floor"
              value={values.objectives.accuracy}
              max={1}
              onChange={(value) => {
                setValues({
                  ...values,
                  objectives: { ...values.objectives, accuracy: value },
                });
              }}
            />
            <NumberField
              label="Held-out grade floor"
              value={values.objectives.grade}
              max={3}
              onChange={(value) => {
                setValues({
                  ...values,
                  objectives: { ...values.objectives, grade: value },
                });
              }}
            />
          </div>
        </fieldset>
        {(["noise", "tolerance"] as const).map((group) => (
          <fieldset key={group} disabled={!canEdit || sending}>
            <legend>
              {group === "noise"
                ? "Noise: required improvement"
                : "Tolerance: allowed regression"}
            </legend>
            <div className="settings-grid">
              {(Object.keys(measureNames) as (keyof Measures)[]).map((key) => (
                <NumberField
                  key={key}
                  label={`${measureNames[key]} ${group}`}
                  value={values[group][key]}
                  max={measureMax[key]}
                  onChange={(value) => {
                    setValues({
                      ...values,
                      [group]: { ...values[group], [key]: value },
                    });
                  }}
                />
              ))}
            </div>
          </fieldset>
        ))}
        {canEdit && (
          <button type="submit" disabled={sending}>
            {sending ? "Saving…" : "Save settings"}
          </button>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {saved && <p role="status">Settings saved.</p>}
      </form>
      <ConfirmDialog
        open={confirming}
        title="Save platform settings?"
        confirmLabel="Save changes"
        onConfirm={() => {
          void save();
        }}
        onCancel={() => {
          setConfirming(false);
        }}
      >
        <p>
          New rounds use these values. Existing decisions retain their approval
          rules.
        </p>
        <pre className="instructions">{JSON.stringify(values, null, 2)}</pre>
      </ConfirmDialog>
    </section>
  );
}

function NumberField({
  label,
  value,
  onChange,
  min = 0,
  max,
  step = 0.01,
}: Readonly<{
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
}>) {
  return (
    <label>
      {label}
      <input
        type="number"
        required
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => {
          onChange(Number(event.target.value));
        }}
      />
    </label>
  );
}
