import { useCallback, useEffect, useRef, useState, type DependencyList, type Dispatch, type SetStateAction } from "react";

/**
 * Generic data-fetching hook. Replaces the repeated
 * `useState(loading) + useState(error) + useState(data) + useEffect(fetch)` pattern.
 *
 * @param fn — async function that returns the data. Chain `.then()` inside to transform.
 * @param deps — re-fetch when any dep changes. Empty = fetch once on mount.
 * @param options.enabled — skip fetch when false (loading immediately resolves to false).
 */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: DependencyList = [],
  options?: { enabled?: boolean },
): {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
  setData: Dispatch<SetStateAction<T | null>>;
} {
  const enabled = options?.enabled ?? true;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const tickRef = useRef(0);

  const execute = useCallback(() => {
    const tick = ++tickRef.current;
    setLoading(true);
    setError(null);
    fnRef.current()
      .then((result) => {
        if (tick !== tickRef.current) return;
        setData(result);
      })
      .catch((e: unknown) => {
        if (tick !== tickRef.current) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (tick !== tickRef.current) return;
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    execute();
    return () => { tickRef.current++; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, execute, ...deps]);

  return { data, loading, error, refetch: execute, setData };
}
