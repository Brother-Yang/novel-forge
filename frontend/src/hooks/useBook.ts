import { useContext, useEffect, useState } from "react";
import { api } from "../api";
import type { Book } from "../api/types";
import { AuthSessionRevisionContext } from "../components/authContext";

export function useBook(bookId: string | undefined, polling: boolean, initialBook?: Book) {
  const sessionRevision = useContext(AuthSessionRevisionContext);
  const [book, setBook] = useState(initialBook);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    if (!bookId) return;
    const request = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function load(id: string) {
      setLoading(true);
      try {
        const data = await api.getBook(id, request.signal);
        if (request.signal.aborted) return;
        setBook(data);
        setError(null);
      } catch (err) {
        if (request.signal.aborted) return;
        setError(err instanceof Error ? err.message : "书籍加载失败");
      } finally {
        if (!request.signal.aborted) {
          setLoading(false);
          if (polling) timer = setTimeout(() => void load(id), 4000);
        }
      }
    }

    // A change to polling triggers a final refresh when generation stops.
    void load(bookId);
    return () => {
      request.abort();
      clearTimeout(timer);
    };
  }, [bookId, polling, retryCount, sessionRevision]);

  return { book, error, loading, retry: () => setRetryCount((count) => count + 1) };
}
