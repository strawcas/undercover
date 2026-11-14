import { type SyntheticEvent, useState } from "react";
import { api, type Measures, type PlatformSettings } from "../api";
import { useAuth } from "../auth";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Message } from "../components/Message";
import { PageHeader } from "../components/PageHeader";
import { money, time } from "../format";
import { getTheme, setTheme, type Theme } from "../theme";
import { REFRESH_MS, useLoad } from "../useLoad";

const fieldNames = {
  approval: "Approval limit (USD)",
  spending: "Round spending limit (USD)",
  concurrency: "Agents at once in round evaluations",
  accuracy: "Held-out accuracy floor (%)",
  grade: "Held-out grade floor (0–3)",
};
const measureFields: Record<
  keyof Measures,
  { name: string; unit: string; scale: number; max: number | undefined }
> = {
  accuracy: { name: "Accuracy", unit: "percentage points", scale: 100, max: 1 },
  grade: { name: "Grade", unit: "points", scale: 1, max: 3 },
  cost_per_claim: {
    name: "Cost per claim",
    unit: "%",
    scale: 100,
    max: undefined,
  },
  seconds_per_claim: {
    name: "Time per claim",
    unit: "%",
    scale: 100,
    max: undefined,
  },
};
const measureKeys = Object.keys(measureFields) as (keyof Measures)[];
const thresholdGroups = ["noise", "tolerance"] as const;

function measureLabel(key: keyof Measures, group: string) {
  const field = measureFields[key];
  return `${field.name} ${group} (${field.unit})`;
}

function displayNumber(value: number, scale = 1) {
  return scale === 1 ? value : Number((value * scale).toFixed(10));
}

function measureValue(key: keyof Measures, value: number) {
  const field = measureFields[key];
  const suffix = field.unit === "%" ? "%" : ` ${field.unit}`;
  return `${String(displayNumber(value, field.scale))}${suffix}`;
}

function SettingsSummary({ values }: Readonly<{ values: PlatformSettings }>) {
  const rows = [
    [fieldNames.approval, money(values.payout_approval_limit)],
    [fieldNames.spending, money(values.spending_limit)],
    [fieldNames.concurrency, values.concurrency],
    [
      fieldNames.accuracy,
      `${String(displayNumber(values.objectives.accuracy, 100))}%`,
    ],
    [fieldNames.grade, `${String(values.objectives.grade)} / 3`],
    ...thresholdGroups.flatMap((group) =>
      measureKeys.map((key) => [
        measureLabel(key, group),
        measureValue(key, values[group][key]),
      ]),
    ),
  ];
  return (
    <dl className="evaluation-measures">
      {rows.map(([name, value]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd className="mono">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SettingsPage() {
  const { data, error, reload } = useLoad(api.getSettings, REFRESH_MS);
  const [theme, updateTheme] = useState(getTheme);
  return (
    <>
      <PageHeader title="Settings">
        Set the business rules and limits for future improvement rounds and new
        decisions.
      </PageHeader>
      {data && error && <Message error={error} retry={reload} />}
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
                        <section aria-label="Before settings change">
                          <h3>Before</h3>
                          <SettingsSummary values={change.before} />
                        </section>
                        <section aria-label="After settings change">
                          <h3>After</h3>
                          <SettingsSummary values={change.after} />
                        </section>
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
          <Message error={error} retry={reload} />
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
        Accuracy is a percentage; its noise and tolerance use percentage points
        (60% to 70% is 10 points). Grade uses a 0–3 score and score-point
        changes. Cost and time thresholds are percentages of the baseline: 15%
        means a relative change of 15%.
      </p>
      <form className="stack-form" onSubmit={submit}>
        <fieldset disabled={!canEdit || sending}>
          <legend>Business limits</legend>
          <div className="settings-grid">
            <NumberField
              label={fieldNames.approval}
              value={values.payout_approval_limit}
              onChange={(value) => {
                setValues({ ...values, payout_approval_limit: value });
              }}
            />
            <NumberField
              label={fieldNames.spending}
              value={values.spending_limit}
              onChange={(value) => {
                setValues({ ...values, spending_limit: value });
              }}
            />
            <NumberField
              label={fieldNames.concurrency}
              value={values.concurrency}
              max={25}
              min={1}
              step={1}
              onChange={(value) => {
                setValues({ ...values, concurrency: value });
              }}
            />
            <NumberField
              label={fieldNames.accuracy}
              value={values.objectives.accuracy}
              scale={100}
              max={1}
              onChange={(value) => {
                setValues({
                  ...values,
                  objectives: { ...values.objectives, accuracy: value },
                });
              }}
            />
            <NumberField
              label={fieldNames.grade}
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
        {thresholdGroups.map((group) => (
          <fieldset key={group} disabled={!canEdit || sending}>
            <legend>
              {group === "noise"
                ? "Noise: required improvement"
                : "Tolerance: allowed regression"}
            </legend>
            <div className="settings-grid">
              {measureKeys.map((key) => (
                <NumberField
                  key={key}
                  label={measureLabel(key, group)}
                  value={values[group][key]}
                  scale={measureFields[key].scale}
                  max={measureFields[key].max}
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
        <SettingsSummary values={values} />
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
  scale = 1,
}: Readonly<{
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  scale?: number;
}>) {
  return (
    <label>
      {label}
      <input
        type="number"
        required
        value={displayNumber(value, scale)}
        min={min * scale}
        max={max === undefined ? undefined : max * scale}
        step={step}
        onChange={(event) => {
          onChange(Number(event.target.value) / scale);
        }}
      />
    </label>
  );
}
