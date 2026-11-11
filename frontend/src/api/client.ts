export const API_BASE = (import.meta.env.VITE_API_BASE_URL?.trim() || "/api").replace(/\/+$/, "");

const unauthorizedListeners = new Set<() => void>();
const responseSessions = new WeakMap<Response, number>();
let sessionVersion = 0;

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

export function markSessionChanged(): void {
  sessionVersion += 1;
}

export async function fetchWithSession(url: string, options: RequestInit = {}): Promise<Response> {
  const version = sessionVersion;
  const response = await fetch(url, { ...options, credentials: "include" });
  responseSessions.set(response, version);
  return response;
}

export async function requireOk(
  res: Response,
  reportUnauthorized = true,
  readErrorBody = true
): Promise<void> {
  if (res.ok) return;
  if (res.status === 401 && reportUnauthorized && responseSessions.get(res) === sessionVersion) {
    for (const listener of unauthorizedListeners) listener();
  }
  if (!readErrorBody) throw new ApiError(res.status, `生成请求失败（HTTP ${res.status}）`);
  const text = await res.text();
  let detail = text;
  try {
    const body: unknown = JSON.parse(text);
    if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
      detail = body.detail;
    }
  } catch {
    // A reverse proxy can return a plain-text error.
  }
  throw new ApiError(res.status, `${res.status}: ${detail || "请求失败"}`);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetchWithSession(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  await requireOk(res, path !== "/auth/login");
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const http = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
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
 * 通过 POST + fetch ReadableStream 消费生成接口的 SSE。
 * 按行保留跨块 CRLF 状态；done/error 结束生成，未收到终止事件的 EOF 视为中断。
 */
export async function sseFetch(path: string, body: unknown, handlers: SSEHandlers): Promise<void> {
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let cancellation: Promise<PromiseSettledResult<void>> | undefined;
  let failed = false;
  let failure: unknown;

  // 立即给 cancel 的拒绝结果注册处理器，避免 abort 事件中的异步清理产生未处理拒绝。
  const cancelReader = () => {
    if (reader && !cancellation) {
      cancellation = Promise.allSettled([reader.cancel()]).then(([result]) => result);
    }
  };

  try {
    handlers.signal?.throwIfAborted();
    const res = await fetchWithSession(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body ?? {}),
      signal: handlers.signal,
    });
    const contentType = res.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
    if (!res.ok && contentType !== "application/json") {
      // Do not wait for an error response that keeps streaming indefinitely.
      reader = res.body?.getReader();
      await requireOk(res, true, false);
    }
    await requireOk(res);
    reader = res.body?.getReader();
    if (!reader) throw new Error("生成连接没有响应正文");
    if (contentType !== "text/event-stream") {
      throw new Error("SSE 响应类型错误");
    }
    handlers.signal?.addEventListener("abort", cancelReader, { once: true });
    handlers.signal?.throwIfAborted();

    const decoder = new TextDecoder();
    let line = "";
    let skipLF = false;
    let event = "";
    let dataLines: string[] = [];
    let terminal = false;

    const processLine = () => {
      if (line === "") {
        const type = event || "message";
        const data = dataLines.join("\n");
        const hasData = dataLines.length > 0;
        event = "";
        dataLines = [];
        if (hasData) {
          handlers.signal?.throwIfAborted();
          handlers.onEvent(type, data);
          terminal = type === "done" || type === "error";
        }
        return;
      }
      if (line.startsWith(":")) return;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      // SSE 只去掉冒号后的一个 ASCII 空格，正文的其余空白属于数据。
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") dataLines.push(value);
    };

    const consume = (text: string) => {
      for (const char of text) {
        if (terminal) break;
        handlers.signal?.throwIfAborted();
        if (skipLF) {
          skipLF = false;
          if (char === "\n") continue;
        }
        if (char === "\r" || char === "\n") {
          processLine();
          line = "";
          skipLF = char === "\r";
        } else {
          line += char;
        }
      }
    };

    while (!terminal) {
      handlers.signal?.throwIfAborted();
      const { done, value } = await reader.read();
      handlers.signal?.throwIfAborted();
      consume(decoder.decode(value, { stream: !done }));
      if (done && !terminal) {
        // 未用空行结束的尾部事件按 SSE 规范丢弃，不能把断流误报为生成成功。
        throw new Error("生成连接已中断，未收到结束事件");
      }
    }
  } catch (err) {
    failed = true;
    failure = err;
  } finally {
    handlers.signal?.removeEventListener("abort", cancelReader);
    cancelReader();
    if (cancellation) {
      const result = await cancellation;
      // 已失败的 reader 再 cancel 会拒绝，保留真正的读取/回调错误。
      if (result.status === "rejected" && !failed) {
        failed = true;
        failure = result.reason;
      }
    }
    reader?.releaseLock();
  }
  if (failed) handlers.onError?.(failure);
}
