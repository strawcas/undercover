import { type SyntheticEvent, useState } from "react";
import { useAuth } from "../auth";
import { PageHeader } from "../components/PageHeader";

export function SignInPage() {
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [sending, setSending] = useState(false);

  async function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    setSending(true);
    setError(undefined);
    try {
      await auth.signIn(email, password);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Sign-in failed. Try again.",
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="page sign-in-page">
      <section className="panel">
        <PageHeader title="Sign in to UnderCover">
          Use your handler account to work claims.
        </PageHeader>
        <form
          className="stack-form"
          onSubmit={(event) => {
            void submit(event);
          }}
        >
          <label>
            Email
            <input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
              }}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
            />
          </label>
          <button type="submit" disabled={sending}>
            {sending ? "Signing in…" : "Sign in"}
          </button>
          {(error ?? auth.error) && (
            <p className="form-error" role="alert">
              {error ?? auth.error}
            </p>
          )}
        </form>
      </section>
    </main>
  );
}
