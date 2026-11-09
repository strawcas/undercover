import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";

export const REFRESH_MS = 2000;

export interface Loaded<T> {
  data?: T;
  error?: Error;
  reload: () => void;
}

// Loads data once, or again every refreshMs milliseconds when that's given,
// stopping if the data isn't found, and again whenever reload is called.
// `load` must keep the same identity between renders, as with useCallback.
export function useLoad<T>(
  load: () => Promise<T>,
  refreshMs?: number,
): Loaded<T> {
  const [loaded, setLoaded] = useState<Omit<Loaded<T>, "reload">>({});
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    let loading = false;
    const fetchNow = () => {
      if (loading) return;
      loading = true;
      load().then(
        (data) => {
          loading = false;
          if (active) setLoaded({ data });
        },
        (error: unknown) => {
          loading = false;
          if (error instanceof ApiError && error.status === 404)
            clearInterval(timer);
          if (active)
            setLoaded((previous) => ({
              data: previous.data,
              error:
                error instanceof Error
                  ? error
                  : new Error("The request failed"),
            }));
        },
      );
    };
    fetchNow();
    const timer =
      refreshMs === undefined ? undefined : setInterval(fetchNow, refreshMs);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [load, refreshMs, version]);

  const reload = useCallback(() => {
    setVersion((current) => current + 1);
  }, []);
  return { ...loaded, reload };
}
