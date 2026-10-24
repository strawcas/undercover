import { useCallback, useEffect, useState } from "react";
import { errorMessage, request } from "./api";

function neverPoll() {
  return false;
}

export function useResource<T>(
  path: string,
  loader: (path: string) => Promise<T> = request<T>,
  pollWhile: (data: T) => boolean = neverPoll,
): {
  data: T | undefined;
  error: string | undefined;
  refresh: () => void;
} {
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<{
    path: string;
    data?: T;
    error?: string;
  }>();
  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let poll = pollWhile !== neverPoll;
    async function load() {
      try {
        const data = await loader(path);
        poll = pollWhile(data);
        if (current) setResult({ path, data });
      } catch (error: unknown) {
        if (current)
          setResult((previous) => ({
            path,
            data: previous?.path === path ? previous.data : undefined,
            error: errorMessage(error),
          }));
      } finally {
        if (current && poll)
          timer = setTimeout(() => {
            void load();
          }, 1000);
      }
    }
    void load();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [path, version, loader, pollWhile]);
  const fresh = result?.path === path ? result : undefined;
  return {
    data: fresh?.data,
    error: fresh?.error,
    refresh: useCallback(() => {
      setVersion((v) => v + 1);
    }, []),
  };
}
