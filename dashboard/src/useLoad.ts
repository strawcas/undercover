import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export const REFRESH_MS = 2000;

export interface Loaded<T> {
  data?: T;
  error?: Error;
  reload: () => void;
  replace: (data: T) => void;
}

interface LoadState<T> {
  loader: () => Promise<T>;
  data?: T;
  error?: Error;
}

// Loads data once, or again every refreshMs milliseconds when that's given,
// stopping if the data isn't found, and again whenever reload is called.
// `load` must keep the same identity between renders, as with useCallback.
export function useLoad<T>(
  load: () => Promise<T>,
  refreshMs?: number,
): Loaded<T> {
  const [loaded, setLoaded] = useState<LoadState<T>>({ loader: load });
  const [version, setVersion] = useState(0);
  const gate = useRef({
    pending: false,
    queued: null as (() => void) | null,
    revision: 0,
  });

  useEffect(() => {
    let active = true;
    const requests = gate.current;
    const fetchNow = () => {
      if (!active || requests.pending) return;
      requests.pending = true;
      const revision = requests.revision;
      load()
        .then(
          (data) => {
            if (active && revision === requests.revision)
              setLoaded({ loader: load, data });
          },
          (error: unknown) => {
            if (!active || revision !== requests.revision) return;
            const missing = error instanceof ApiError && error.status === 404;
            if (missing) clearInterval(timer);
            setLoaded((previous) => ({
              loader: load,
              data:
                !missing && previous.loader === load
                  ? previous.data
                  : undefined,
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
  const replace = useCallback(
    (data: T) => {
      gate.current.revision += 1;
      setLoaded((previous) =>
        previous.loader === load ? { loader: load, data } : previous,
      );
      reload();
    },
    [load, reload],
  );
  const current = loaded.loader === load ? loaded : undefined;
  return { data: current?.data, error: current?.error, reload, replace };
}
