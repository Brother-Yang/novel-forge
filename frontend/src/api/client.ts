export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";
const BASE = API_BASE;

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const http = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export interface SSEHandlers {
  onEvent: (event: string, data: string) => void;
  onError?: (err: unknown) => void;
  signal?: AbortSignal;
}

/**
 * 通过 POST + fetch ReadableStream 消费 SSE(EventSource 只支持 GET)。
 * 解析 `event:` 与 `data:` 行,支持多行 data。
 */
export async function sseFetch(path: string, body: unknown, handlers: SSEHandlers): Promise<void> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
      signal: handlers.signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(`SSE ${res.status}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    const dispatch = (raw: string) => {
      if (!raw.trim()) return;
      let event = "message";
      const dataLines: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
      }
      handlers.onEvent(event, dataLines.join("\n"));
    };

    while (true) {
      const { done, value } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      if (done) buffer += decoder.decode();
      // sse-starlette 使用 CRLF(\r\n\r\n) 分隔事件,统一成 \n\n 再拆
      buffer = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        dispatch(raw);
      }
      if (done) {
        dispatch(buffer);
        break;
      }
    }
  } catch (err) {
    handlers.onError?.(err);
  }
}
