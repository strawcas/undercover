import { type ReactNode, type SyntheticEvent, useState } from "react";
import { api } from "../api";
import { REFRESH_MS, useLoad } from "../useLoad";

export function RunStartForm({
  name,
  start,
  children,
}: Readonly<{
  name: string;
  start: (concurrency: number) => Promise<unknown>;
  children?: ReactNode;
}>) {
  const [concurrency, setConcurrency] = useState(3);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const { data: active, reload } = useLoad(api.getActiveRun, REFRESH_MS);
  const busy = active !== undefined && active !== null;
  function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    setMessage("");
    start(concurrency)
      .then(
        () => {
          setMessage(`${name} accepted.`);
          reload();
        },
        (reason: unknown) => {
          setError(
            reason instanceof Error ? reason.message : "The start failed",
          );
        },
      )
      .finally(() => {
        setPending(false);
      });
  }
  return (
    <form className="run-start" onSubmit={submit}>
      {children}
      <label>
        Agents at once
        <input
          type="number"
          min={1}
          max={25}
          step={1}
          required
          value={concurrency}
          onChange={(event) => {
            setConcurrency(Number(event.target.value));
          }}
        />
      </label>
      <button type="submit" disabled={pending || busy}>
        {pending ? "Starting…" : `Start ${name.toLowerCase()}`}
      </button>
      {busy && <p role="status">A dispatch or evaluation is running.</p>}
      {message && <p role="status">{message}</p>}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
