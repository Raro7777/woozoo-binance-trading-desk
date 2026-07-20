"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet } from "../lib/api";
import { Hold } from "./ui";

export type ResourceState<T> =
  | Readonly<{ state: "loading" }>
  | Readonly<{ state: "ready"; value: T }>
  | Readonly<{ state: "hold"; message: string }>;

export function useResource<T>(path: `/api/v1/${string}`): ResourceState<T> & Readonly<{ reload: () => void }> {
  const [nonce, setNonce] = useState(0);
  const [resource, setResource] = useState<ResourceState<T>>({ state: "loading" });
  const reload = useCallback(() => {
    setResource({ state: "loading" });
    setNonce((value) => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    apiGet<T>(path)
      .then((value) => {
        if (!controller.signal.aborted) setResource({ state: "ready", value });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          const detail = error instanceof Error ? error.message : "The control API is unavailable.";
          setResource({ state: "hold", message: detail });
        }
      });
    return () => controller.abort();
  }, [nonce, path]);

  return { ...resource, reload };
}

export function ResourceBoundary<T>({
  resource,
  children,
}: Readonly<{ resource: ResourceState<T>; children: (value: T) => React.ReactNode }>) {
  if (resource.state === "loading") return <p className="loading" aria-live="polite">Loading authoritative state…</p>;
  if (resource.state === "hold") return <Hold>{resource.message} No action is available until authoritative state returns.</Hold>;
  return children(resource.value);
}
