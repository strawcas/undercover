import { createContext, useContext } from "react";
import type { Handler } from "./api";

export interface AuthState {
  handler: Handler | null;
  loading: boolean;
  error: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  copilotToken: () => Promise<string>;
}

export const AuthContext = createContext<AuthState | null>(null);

export function useAuth() {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("The dashboard requires an auth provider");
  return auth;
}
