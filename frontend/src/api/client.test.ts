import { afterEach, describe, expect, it, vi } from "vitest";
import { http, sseFetch } from "./client";

function mockFetch(response: Partial<Response> & { jsonBody?: unknown; textBody?: string }) {
  const res = {
    ok: response.ok ?? true,
    status: response.status ?? 200,
    json: vi.fn().mockResolvedValue(response.jsonBody),
    text: vi.fn().mockResolvedValue(response.textBody ?? ""),
  } as unknown as Response;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res));
  return res;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("http", () => {
  it("get 请求成功时返回解析后的 JSON", async () => {
    mockFetch({ jsonBody: { id: 1 } });
    const data = await http.get<{ id: number }>("/books");
    expect(data).toEqual({ id: 1 });
    expect(fetch).toHaveBeenCalledWith(
      "/api/books",
      expect.objectContaining({ headers: { "Content-Type": "application/json" } })
    );
  });

  it("post 请求会带上 JSON body", async () => {
    mockFetch({ jsonBody: { ok: true } });
    await http.post("/books", { title: "书名" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/books",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ title: "书名" }),
      })
    );
  });

  it("响应非 2xx 时抛出包含状态码与错误文本的异常", async () => {
    mockFetch({ ok: false, status: 404, textBody: "书籍不存在" });
    await expect(http.get("/books/999")).rejects.toThrow("404: 书籍不存在");
  });

  it("204 No Content 时返回 undefined", async () => {
    mockFetch({ ok: true, status: 204 });
    const data = await http.del("/books/1");
    expect(data).toBeUndefined();
  });
});

describe("sseFetch", () => {
  it("按 \\n\\n 拆分事件并解析 event/data 字段", async () => {
    const chunks = ["event: token\ndata: 你好\n\n", "event: done\ndata: \n\n"];
    const encoder = new TextEncoder();
    let i = 0;
    const reader = {
      read: vi.fn().mockImplementation(async () => {
        if (i < chunks.length) {
          return { done: false, value: encoder.encode(chunks[i++]) };
        }
        return { done: true, value: undefined };
      }),
    };
    const res = {
      ok: true,
      status: 200,
      body: { getReader: () => reader },
    } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res));

    const events: { event: string; data: string }[] = [];
    await sseFetch(
      "/generate/worldbuilding",
      { book_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" },
      {
        onEvent: (event, data) => events.push({ event, data }),
      }
    );

    expect(events).toEqual([
      { event: "token", data: "你好" },
      { event: "done", data: "" },
    ]);
  });

  it("请求失败时调用 onError 而不是抛出异常", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    const onError = vi.fn();
    await sseFetch("/generate/worldbuilding", {}, { onEvent: vi.fn(), onError });
    expect(onError).toHaveBeenCalledWith(new Error("network down"));
  });
});
