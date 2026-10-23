import { useEffect, useState } from "react";
import { errorMessage, request } from "./api";

export function useResource<T>(
  path: string,
  loader: (path: string) => Promise<T> = request<T>,
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
    void loader(path).then(
      (data) => {
        if (current) setResult({ path, data });
      },
      (error: unknown) => {
        if (current) setResult({ path, error: errorMessage(error) });
      },
    );
    return () => {
      current = false;
    };
  }, [path, version, loader]);
  const fresh = result?.path === path ? result : undefined;
  return {
    data: fresh?.data,
    error: fresh?.error,
    refresh: () => {
      setVersion((v) => v + 1);
    },
  };
}
