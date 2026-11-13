import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | undefined;

export function authClient() {
  if (!client) {
    const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
    const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as
      string | undefined;
    if (!url || !key)
      throw new Error(
        "Configure the Supabase URL and publishable key, then restart the dashboard.",
      );
    client = createClient(url, key);
  }
  return client;
}
