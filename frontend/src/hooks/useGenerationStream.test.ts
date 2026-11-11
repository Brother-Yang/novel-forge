import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { sseFetch } from "../api/client";
import { useGenerationStream } from "./useGenerationStream";

vi.mock("../api/client", () => ({ sseFetch: vi.fn() }));

const path = "/generate/chapter";
const body = { chapter_id: "chapter" };
const mockFetch = vi.mocked(sseFetch);

function callbacks() {
  return { onEvent: vi.fn(), onDone: vi.fn(), onError: vi.fn() };
}

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockImplementation(() => new Promise<void>(() => {}));
});

it("同步拒绝重复启动，只转发一次终态", () => {
  const first = callbacks();
  const duplicate = callbacks();
  const { result } = renderHook(() => useGenerationStream("chapter"));
  act(() => {
    result.current.start(path, body, first);
    result.current.start(path, body, duplicate);
  });
  expect(mockFetch).toHaveBeenCalledTimes(1);
  expect(result.current.running).toBe(true);
  const handlers = mockFetch.mock.calls[0][2];
  act(() => {
    handlers.onEvent("token", "有效正文");
    handlers.onEvent("done", "");
    handlers.onEvent("token", "迟到正文");
    handlers.onEvent("done", "");
    handlers.onError?.(new Error("迟到传输错误"));
  });
  expect(first.onEvent).toHaveBeenCalledExactlyOnceWith("token", "有效正文");
  expect(first.onDone).toHaveBeenCalledTimes(1);
  expect(first.onError).not.toHaveBeenCalled();
  expect(duplicate.onDone).not.toHaveBeenCalled();
  expect(result.current.running).toBe(false);
});

it("取消时忽略旧回调，旧连接失败也不会结束后来启动的生成", async () => {
  let failFirst!: (error: Error) => void;
  mockFetch.mockReturnValueOnce(
    new Promise<void>((_resolve, reject) => {
      failFirst = reject;
    })
  );
  const first = callbacks();
  const next = callbacks();
  const { result } = renderHook(() => useGenerationStream("chapter"));
  act(() => result.current.start(path, body, first));
  const original = mockFetch.mock.calls[0][2];
  act(() => {
    result.current.cancel();
    result.current.start(path, body, next);
  });
  expect(original.signal?.aborted).toBe(true);
  await act(async () => {
    original.onEvent("done", "");
    original.onError?.(new Error("过期错误"));
    failFirst(new Error("旧连接断开"));
  });
  expect(first.onDone).not.toHaveBeenCalled();
  expect(first.onError).not.toHaveBeenCalled();
  expect(result.current.running).toBe(true);
  const latest = mockFetch.mock.calls[1][2];
  act(() => {
    latest.onEvent("error", "模型失败");
    latest.onError?.(new Error("重复错误"));
  });
  expect(next.onError).toHaveBeenCalledExactlyOnceWith(new Error("模型失败"));
  expect(result.current.running).toBe(false);
});

it("切换作用域和卸载会取消连接，回到旧作用域时不会恢复旧运行状态", () => {
  const first = callbacks();
  const { result, rerender, unmount } = renderHook(({ scope }) => useGenerationStream(scope), {
    initialProps: { scope: "first" },
  });
  act(() => result.current.start(path, body, first));
  const original = mockFetch.mock.calls[0][2];
  rerender({ scope: "second" });
  expect(original.signal?.aborted).toBe(true);
  expect(result.current.running).toBe(false);
  rerender({ scope: "first" });
  expect(result.current.running).toBe(false);
  const second = callbacks();
  act(() => result.current.start(path, body, second));
  const current = mockFetch.mock.calls[1][2];
  unmount();
  expect(current.signal?.aborted).toBe(true);
  act(() => current.onEvent("done", ""));
  expect(second.onDone).not.toHaveBeenCalled();
});

it.each(["throw", "reject", "eof"])("传输 %s 时只报告一次失败并允许重试", async (outcome) => {
  const failure = new Error("连接异常");
  if (outcome === "throw") {
    mockFetch.mockImplementationOnce(() => {
      throw failure;
    });
  } else if (outcome === "reject") mockFetch.mockRejectedValueOnce(failure);
  else mockFetch.mockResolvedValueOnce();
  const handlers = callbacks();
  const { result } = renderHook(() => useGenerationStream("chapter"));
  await act(async () => {
    result.current.start(path, body, handlers);
  });
  expect(handlers.onError).toHaveBeenCalledExactlyOnceWith(
    outcome === "eof" ? new Error("生成连接已中断，未收到结束事件") : failure
  );
  expect(handlers.onDone).not.toHaveBeenCalled();
  expect(result.current.running).toBe(false);
  act(() => result.current.start(path, body, callbacks()));
  expect(mockFetch).toHaveBeenCalledTimes(2);
});

it("完成后的业务回调出错时报告该错误并释放生成状态", () => {
  const failure = new Error("刷新失败");
  const handlers = callbacks();
  handlers.onDone.mockImplementationOnce(() => {
    throw failure;
  });
  const { result } = renderHook(() => useGenerationStream("chapter"));
  act(() => result.current.start(path, body, handlers));
  act(() => mockFetch.mock.calls[0][2].onEvent("done", ""));
  expect(handlers.onError).toHaveBeenCalledExactlyOnceWith(failure);
  expect(result.current.running).toBe(false);
});
