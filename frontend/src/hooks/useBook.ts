import { useContext, useEffect, useState } from "react";
import { api } from "../api";
import { ApiError } from "../api/client";
import type { Book } from "../api/types";
import { AuthSessionRevisionContext } from "../components/authContext";

export function useBook(
  bookId: string | undefined,
  polling: boolean,
  initialBook?: Book,
  onNotFound?: (id: string) => void
) {
  const sessionRevision = useContext(AuthSessionRevisionContext);
  const [book, setBook] = useState(initialBook);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(false);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    if (!bookId) return;
    const request = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function load(id: string) {
      setLoading(true);
      let missing = false;
      try {
        const data = await api.getBook(id, request.signal);
        if (request.signal.aborted) return;
        setBook(data);
        setError(null);
        setNotFound(false);
      } catch (err) {
        if (request.signal.aborted) return;
        missing = err instanceof ApiError && err.status === 404;
        if (missing) {
          onNotFound?.(id);
          setNotFound(true);
        }
        setError(err instanceof Error ? err.message : "书籍加载失败");
      } finally {
        if (!request.signal.aborted) {
          setLoading(false);
          if (polling && !missing) timer = setTimeout(() => void load(id), 4000);
        }
      }
    }

    // A change to polling triggers a final refresh when generation stops.
    void load(bookId);
    return () => {
      request.abort();
      clearTimeout(timer);
    };
  }, [bookId, polling, retryCount, sessionRevision, onNotFound]);

  return { book, error, notFound, loading, retry: () => setRetryCount((count) => count + 1) };
}
