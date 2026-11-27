import { type ReactNode, useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import {
  api,
  ApiError,
  HTTP_TOO_MANY_REQUESTS,
  onUnauthorized,
  setAccessToken,
  type Handler,
} from "./api";
import { AuthContext } from "./auth";
import { authClient } from "./supabase";

const COPILOT_MINIMUM_SECONDS = 20 * 60;

export function AuthProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [connection] = useState(() => {
    try {
      return { client: authClient(), error: null };
    } catch (reason) {
      return {
        client: null,
        error:
          reason instanceof Error ? reason.message : "Sign-in is unavailable",
      };
    }
  });
  const [handler, setHandler] = useState<Handler | null>(null);
  const [loading, setLoading] = useState(connection.client !== null);
  const [error, setError] = useState<string | null>(connection.error);

  useEffect(() => {
    const client = connection.client;
    if (!client) return;
    let active = true;
    let revision = 0;
    let identity: string | undefined;
    let verified = false;
    const unsubscribe = onUnauthorized(() => {
      ++revision;
      identity = undefined;
      verified = false;
      setHandler(null);
      setLoading(false);
      setError("Your sign-in expired. Sign in again.");
    });
    async function update(session: Session | null) {
      const current = ++revision;
      const isCurrent = () => active && current === revision;
      setAccessToken(session?.access_token ?? null);
      if (identity !== session?.user.id) {
        verified = false;
        setHandler(null);
        setLoading(true);
        identity = session?.user.id;
      }
      try {
        const next = session ? await api.getCurrentHandler() : null;
        if (isCurrent()) {
          verified = next !== null;
          setHandler(next);
          setError(null);
        }
      } catch (reason) {
        if (isCurrent()) {
          const throttled =
            reason instanceof ApiError &&
            reason.status === HTTP_TOO_MANY_REQUESTS;
          if (!verified || !throttled) {
            verified = false;
            setHandler(null);
            setAccessToken(null);
          }
          setError(
            reason instanceof Error
              ? reason.message
              : "Sign-in could not be verified",
          );
        }
      } finally {
        if (isCurrent()) setLoading(false);
      }
    }
    const { data } = client.auth.onAuthStateChange((_event, session) => {
      if (active) void update(session);
    });
    void client.auth.getSession().then(({ data: restored, error: failure }) => {
      if (!active || revision) return;
      if (failure) {
        setError(failure.message);
        setLoading(false);
      } else void update(restored.session);
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
      unsubscribe();
    };
  }, [connection]);

  async function signIn(email: string, password: string) {
    const { error: failure } = await authClient().auth.signInWithPassword({
      email,
      password,
    });
    if (failure) throw failure;
  }

  async function signOut() {
    const { error: failure } = await authClient().auth.signOut();
    if (failure) {
      setError(failure.message);
      return;
    }
    setAccessToken(null);
    setHandler(null);
  }

  async function copilotToken() {
    const client = authClient();
    const { data, error: failure } = await client.auth.getSession();
    if (failure) throw failure;
    let session = data.session;
    if (!session || session.user.email !== handler?.email)
      throw new Error("Sign in again to use the Copilot.");
    if (
      (session.expires_at ?? 0) - Date.now() / 1000 <
      COPILOT_MINIMUM_SECONDS
    ) {
      const refreshed = await client.auth.refreshSession();
      if (refreshed.error) throw refreshed.error;
      session = refreshed.data.session;
    }
    if (!session || session.user.email !== handler?.email)
      throw new Error("Sign in again to use the Copilot.");
    return session.access_token;
  }

  return (
    <AuthContext.Provider
      value={{ handler, loading, error, signIn, signOut, copilotToken }}
    >
      {children}
    </AuthContext.Provider>
  );
}
