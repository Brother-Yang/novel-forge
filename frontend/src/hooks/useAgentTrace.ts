import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AgentTracePageEntry } from "../api/types";

const POLL_INTERVAL = 2500;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败";
}

/** The caller mounts a fresh timeline for each task. */
export function useAgentTrace(taskId: string, running: boolean, beforePrepend: () => void) {
  const [entries, setEntries] = useState<AgentTracePageEntry[]>([]);
  const [initialized, setInitialized] = useState(false);
  const [hasEarlier, setHasEarlier] = useState(false);
  const [latestError, setLatestError] = useState<string | null>(null);
  const [earlierError, setEarlierError] = useState<string | null>(null);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const first = useRef<number | null>(null);
  const last = useRef<number | null>(null);
  const earlierRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    const request = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function loadLatest() {
      let catchUp = false;
      try {
        const after = last.current;
        const page = await api.getTaskTracePage(
          taskId,
          after === null ? {} : { after },
          request.signal
        );
        if (request.signal.aborted) return;
        if (after === null) {
          setInitialized(true);
          setHasEarlier(page.has_more);
        }
        first.current ??= page.entries[0]?.sequence ?? null;
        last.current = page.entries[page.entries.length - 1]?.sequence ?? after ?? 0;
        if (page.entries.length) setEntries((previous) => [...previous, ...page.entries]);
        setLatestError(null);
        catchUp = after !== null && page.has_more;
      } catch (error) {
        if (request.signal.aborted) return;
        setLatestError(errorMessage(error));
      }
      if (catchUp || running) {
        timer = setTimeout(loadLatest, catchUp ? 0 : POLL_INTERVAL);
      }
    }

    // The running-to-terminal transition starts a fresh request. This fetches
    // events committed after an earlier in-flight poll took its snapshot.
    void loadLatest();
    return () => {
      request.abort();
      clearTimeout(timer);
    };
  }, [taskId, running, retryCount]);

  useEffect(() => () => earlierRequest.current?.abort(), []);

  async function loadEarlier() {
    const before = first.current;
    if (!hasEarlier || before === null || earlierRequest.current) return;
    const request = new AbortController();
    earlierRequest.current = request;
    setLoadingEarlier(true);
    setEarlierError(null);
    try {
      const page = await api.getTaskTracePage(taskId, { before }, request.signal);
      if (request.signal.aborted) return;
      if (page.entries.length) {
        beforePrepend();
        first.current = page.entries[0].sequence;
        setEntries((previous) => [...page.entries, ...previous]);
      }
      setHasEarlier(page.has_more);
    } catch (error) {
      if (!request.signal.aborted) setEarlierError(errorMessage(error));
    } finally {
      if (!request.signal.aborted) {
        earlierRequest.current = null;
        setLoadingEarlier(false);
      }
    }
  }

  function retryLatest() {
    setLatestError(null);
    setRetryCount((count) => count + 1);
  }

  return {
    entries,
    initialized,
    hasEarlier,
    loadingEarlier,
    earlierError,
    latestError,
    loadEarlier,
    retryLatest,
  };
}
