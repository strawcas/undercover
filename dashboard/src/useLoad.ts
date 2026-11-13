import { useCallback, useEffect, useRef, useState } from "react";
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
  const gate = useRef<{ pending: boolean; queued: (() => void) | null }>({
    pending: false,
    queued: null,
  });

  useEffect(() => {
    let active = true;
    const requests = gate.current;
    const fetchNow = () => {
      if (!active || requests.pending) return;
      requests.pending = true;
      load()
        .then(
          (data) => {
            if (active) setLoaded({ data });
          },
          (error: unknown) => {
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
        )
        .finally(() => {
          requests.pending = false;
          const next = requests.queued;
          requests.queued = null;
          next?.();
        });
    };
    if (requests.pending) requests.queued = fetchNow;
    else fetchNow();
    const timer =
      refreshMs === undefined ? undefined : setInterval(fetchNow, refreshMs);
    return () => {
      active = false;
      if (requests.queued === fetchNow) requests.queued = null;
      clearInterval(timer);
    };
  }, [load, refreshMs, version]);

  const reload = useCallback(() => {
    setVersion((current) => current + 1);
  }, []);
  return { ...loaded, reload };
}
