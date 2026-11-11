import { afterEach, describe, expect, it, vi } from "vitest";
import { http, onUnauthorized, sseFetch } from "./client";

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
    mockFetch({ jsonBody: { id: "book-1" } });
    const data = await http.get<{ id: string }>("/books");
    expect(data).toEqual({ id: "book-1" });
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

  it("保护接口 401 触发会话失效，登录失败仅返回提示", async () => {
    const expired = vi.fn();
    const unsubscribe = onUnauthorized(expired);
    try {
      mockFetch({ ok: false, status: 401, textBody: '{"detail":"访问密码不正确"}' });
      await expect(http.post("/auth/login", { password: "wrong" })).rejects.toThrow(
        "访问密码不正确"
      );
      expect(expired).not.toHaveBeenCalled();
      await expect(http.get("/books")).rejects.toThrow("401");
      expect(expired).toHaveBeenCalledTimes(1);
    } finally {
      unsubscribe();
    }
  });
});

const encoder = new TextEncoder();
const terminal = "event: done\ndata: \n\n";

function streamResponse(chunks: (string | Uint8Array)[], close = true) {
  const cancel = vi.fn();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      }
      if (close) controller.close();
    },
    cancel,
  });
  const response = new Response(stream, { headers: { "Content-Type": "text/event-stream" } });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  return { response, stream, cancel };
}

async function consume(chunks: (string | Uint8Array)[], close = true) {
  const fixture = streamResponse(chunks, close);
  const events: { event: string; data: string }[] = [];
  const onError = vi.fn();
  await sseFetch(
    "/generate/chapter",
    { chapter_id: "chapter-1" },
    {
      onEvent: (event, data) => events.push({ event, data }),
      onError,
    }
  );
  return { ...fixture, events, onError };
}

describe("sseFetch", () => {
  it("流式生成的 HTTP 限额错误展示后端信息并携带会话", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response('{"detail":"生成请求已达到本时段限额"}', {
          status: 429,
          headers: { "Content-Type": "application/json" },
        })
      )
    );
    const onError = vi.fn();
    const onEvent = vi.fn();
    await sseFetch("/generate/chapter", {}, { onEvent, onError });
    expect(onError).toHaveBeenCalledWith(new Error("429: 生成请求已达到本时段限额"));
    expect(onEvent).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledWith(
      "/api/generate/chapter",
      expect.objectContaining({ credentials: "include" })
    );
  });
  it.each(["\n", "\r", "\r\n"])(
    "在 %j 换行及每个字节边界正确分发多行 UTF-8 data",
    async (newline) => {
      const bytes = encoder.encode(
        [
          "event: token",
          "data:   你好🐉  ",
          "data:\t缩进",
          "data",
          "",
          "event: done",
          "data:",
          "",
          "",
        ].join(newline)
      );
      const expected = [
        { event: "token", data: "  你好🐉  \n\t缩进\n" },
        { event: "done", data: "" },
      ];
      for (let split = 1; split < bytes.length; split++) {
        const result = await consume([bytes.slice(0, split), bytes.slice(split)]);
        expect(result.events, `split=${split}`).toEqual(expected);
        expect(result.onError).not.toHaveBeenCalled();
        expect(result.stream.locked).toBe(false);
      }
      const result = await consume(Array.from(bytes, (byte) => Uint8Array.of(byte)));
      expect(result.events).toEqual(expected);
      expect(result.onError).not.toHaveBeenCalled();
    }
  );

  it("跨块 CRLF 不提前分发尚未出现 data 的事件", async () => {
    const result = await consume(["event: token\r", "\ndata: 开头\r", "\n\r", "\n", terminal]);
    expect(result.events).toEqual([
      { event: "token", data: "开头" },
      { event: "done", data: "" },
    ]);
    expect(result.onError).not.toHaveBeenCalled();
  });

  it("只移除冒号后单个普通空格，保留正文与事件类型的原始空白", async () => {
    const result = await consume([
      "event:  token \ndata:   空格  \ndata:\t制表符\ndata:　全角空格\n\n",
      "data:    \n\n",
      terminal,
    ]);
    expect(result.events).toEqual([
      { event: " token ", data: "  空格  \n\t制表符\n　全角空格" },
      { event: "message", data: "   " },
      { event: "done", data: "" },
    ]);
  });

  it("忽略心跳、未知字段和无 data 的事件，空 data 仍分发且事件类型按事件重置", async () => {
    const result = await consume([
      "\ufeff: heartbeat\n\nevent: ignored\nid: 1\nretry: 1000\n\n",
      "event: token\nevent:\ndata\n\ndata: next\n\n",
      terminal,
    ]);
    expect(result.events).toEqual([
      { event: "message", data: "" },
      { event: "message", data: "next" },
      { event: "done", data: "" },
    ]);
    expect(result.onError).not.toHaveBeenCalled();
  });

  it.each(["done", "error"])("收到 %s 后停止处理缓冲内容并取消仍开放的流", async (event) => {
    const result = await consume([`event: ${event}\ndata: 结果\n\ndata: 不应出现\n\n`], false);
    expect(result.events).toEqual([{ event, data: "结果" }]);
    expect(result.cancel).toHaveBeenCalledTimes(1);
    expect(result.onError).not.toHaveBeenCalled();
    expect(result.stream.locked).toBe(false);
  });

  it.each(["", "data: 残缺", "event: done\ndata: 完成\n"])(
    "EOF 丢弃未闭合事件并报告连接中断：%j",
    async (tail) => {
      const result = await consume(["event: token\ndata: 已完成事件\n\n", tail]);
      expect(result.events).toEqual([{ event: "token", data: "已完成事件" }]);
      expect(result.onError).toHaveBeenCalledTimes(1);
      expect(result.onError.mock.calls[0][0]).toEqual(expect.any(Error));
      expect(result.stream.locked).toBe(false);
    }
  );

  it("混用 CR、LF、CRLF 且 Content-Type 带 charset 时仍正确解析", async () => {
    const fixture = streamResponse([
      "event: token\rdata: 第一行\ndata: 第二行\r\n\r",
      "\nevent: done\rdata:\r\r",
    ]);
    fixture.response.headers.set("Content-Type", "text/event-stream; charset=utf-8");
    const events: string[][] = [];
    const onError = vi.fn();
    await sseFetch("/generate/chapter", {}, { onEvent: (...event) => events.push(event), onError });
    expect(events).toEqual([
      ["token", "第一行\n第二行"],
      ["done", ""],
    ]);
    expect(onError).not.toHaveBeenCalled();
  });

  it("reader 取消失败也释放锁，并只报告一次清理错误", async () => {
    const failure = new Error("cancel failed");
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(terminal));
      },
      cancel() {
        throw failure;
      },
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(stream, { headers: { "Content-Type": "text/event-stream" } })
        )
    );
    const onError = vi.fn();
    await sseFetch("/generate/chapter", {}, { onEvent: vi.fn(), onError });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(failure);
    expect(stream.locked).toBe(false);
  });

  it("保留请求 POST、JSON 和 AbortSignal 契约", async () => {
    streamResponse([terminal]);
    const controller = new AbortController();
    await sseFetch(
      "/generate/chapter",
      { chapter_id: "chapter-1" },
      { onEvent: vi.fn(), signal: controller.signal }
    );
    expect(fetch).toHaveBeenCalledWith(
      "/api/generate/chapter",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
        body: JSON.stringify({ chapter_id: "chapter-1" }),
        signal: controller.signal,
      })
    );
  });

  it("取消等待中的读取后只通知一次错误并释放 reader", async () => {
    const fixture = streamResponse([], false);
    const controller = new AbortController();
    const onError = vi.fn();
    const onEvent = vi.fn();
    const pending = sseFetch(
      "/generate/chapter",
      {},
      { onEvent, onError, signal: controller.signal }
    );
    await vi.waitFor(() => expect(fixture.stream.locked).toBe(true));
    controller.abort();
    await pending;
    expect(onEvent).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toMatchObject({ name: "AbortError" });
    expect(fixture.cancel).toHaveBeenCalledTimes(1);
    expect(fixture.stream.locked).toBe(false);
  });

  it("回调中取消后不再处理同一块的后续事件", async () => {
    const fixture = streamResponse(["data: first\n\ndata: second\n\n"], false);
    const controller = new AbortController();
    const onError = vi.fn();
    const onEvent = vi.fn(() => controller.abort());
    await sseFetch("/generate/chapter", {}, { onEvent, onError, signal: controller.signal });
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(fixture.stream.locked).toBe(false);
  });

  it("已取消的请求不发起 fetch", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const onError = vi.fn();
    await sseFetch(
      "/generate/chapter",
      {},
      { onEvent: vi.fn(), onError, signal: controller.signal }
    );
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it("读取流失败保留已分发内容，报告原始错误并释放 reader", async () => {
    const failure = new Error("connection reset");
    let source: ReadableStreamDefaultController<Uint8Array> | undefined;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        source = controller;
      },
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(stream, { headers: { "Content-Type": "text/event-stream" } })
        )
    );
    const onEvent = vi.fn(() => source?.error(failure));
    const onError = vi.fn();
    const pending = sseFetch("/generate/chapter", {}, { onEvent, onError });
    source?.enqueue(encoder.encode("data: 保留\n\n"));
    await pending;
    expect(onEvent).toHaveBeenCalledWith("message", "保留");
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(failure);
    expect(stream.locked).toBe(false);
  });

  it("事件回调失败时取消流、释放锁并报告原始错误", async () => {
    const fixture = streamResponse(["data: first\n\n"], false);
    const failure = new Error("callback failed");
    const onError = vi.fn();
    await sseFetch(
      "/generate/chapter",
      {},
      {
        onEvent() {
          throw failure;
        },
        onError,
      }
    );
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(failure);
    expect(fixture.cancel).toHaveBeenCalledTimes(1);
    expect(fixture.stream.locked).toBe(false);
  });

  it.each([
    { status: 503, contentType: "text/event-stream" },
    { status: 200, contentType: "application/json" },
  ])("拒绝异常响应 $status / $contentType 并释放响应体", async ({ status, contentType }) => {
    const fixture = streamResponse([], false);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(fixture.stream, { status, headers: { "Content-Type": contentType } })
        )
    );
    const onError = vi.fn();
    await sseFetch("/generate/chapter", {}, { onEvent: vi.fn(), onError });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(fixture.cancel).toHaveBeenCalledTimes(1);
    expect(fixture.stream.locked).toBe(false);
  });

  it("无响应体时报告错误", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    const onError = vi.fn();
    await sseFetch("/generate/chapter", {}, { onEvent: vi.fn(), onError });
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it("请求失败时调用 onError 而不是抛出异常", async () => {
    const failure = new Error("network down");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(failure));
    const onError = vi.fn();
    await sseFetch("/generate/chapter", {}, { onEvent: vi.fn(), onError });
    expect(onError).toHaveBeenCalledWith(failure);
  });
});
