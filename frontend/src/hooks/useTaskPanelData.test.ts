import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import type { GenerationTask } from "../api/types";
import { useTaskPanelData } from "./useTaskPanelData";

vi.mock("../api", () => ({ api: { listTasks: vi.fn(), getTree: vi.fn() } }));
const task: GenerationTask = {
  id: "task",
  book_id: "book",
  type: "novel",
  target_id: null,
  status: "running",
  progress: 20,
  error: "",
  checkpoint_json: "{}",
  created_at: "2026-11-11",
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  vi.mocked(api.getTree).mockResolvedValue({ volumes: [], loose_chapters: [] });
  vi.mocked(api.listTasks).mockResolvedValue([]);
});
afterEach(() => vi.useRealTimers());

it("慢请求期间不会叠加轮询，操作后的刷新排队并丢弃旧结果", async () => {
  const old = deferred<GenerationTask[]>();
  const fresh = deferred<GenerationTask[]>();
  vi.mocked(api.listTasks).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
  const view = renderHook(() => useTaskPanelData("book"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(api.listTasks).toHaveBeenCalledTimes(1);
  act(() => {
    view.result.current.refreshTasks();
    view.result.current.refreshTasks();
  });
  await act(async () => {
    old.resolve([{ ...task, status: "paused" }]);
  });
  expect(api.listTasks).toHaveBeenCalledTimes(2);
  expect(view.result.current.tasks).toEqual([]);
  await act(async () => {
    fresh.resolve([task]);
  });
  expect(view.result.current.tasks).toEqual([task]);
  expect(view.result.current.tasksLoaded).toBe(true);
});

it("失败保留已加载状态并可立即重试，卸载后停止轮询", async () => {
  vi.mocked(api.listTasks)
    .mockResolvedValueOnce([task])
    .mockRejectedValueOnce(new Error("任务状态暂不可用"))
    .mockResolvedValue([]);
  const view = renderHook(() => useTaskPanelData("book"));
  await act(async () => {});
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500);
  });
  expect(view.result.current.tasksError).toBe("任务状态暂不可用");
  expect(view.result.current.tasks).toEqual([task]);
  await act(async () => {
    view.result.current.refreshTasks();
  });
  expect(view.result.current.tasksError).toBe("");
  expect(view.result.current.tasks).toEqual([]);
  view.unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(api.listTasks).toHaveBeenCalledTimes(3);
});

it("空闲和任务结束后不轮询，重新聚焦可发现其它窗口启动的任务", async () => {
  vi.mocked(api.listTasks)
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce([task])
    .mockResolvedValueOnce([{ ...task, status: "done" }]);
  const view = renderHook(() => useTaskPanelData("book"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(api.listTasks).toHaveBeenCalledTimes(1);
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
  });
  expect(view.result.current.tasks).toEqual([task]);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(api.listTasks).toHaveBeenCalledTimes(3);
  expect(view.result.current.tasks[0].status).toBe("done");
});
