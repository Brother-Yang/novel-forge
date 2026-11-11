import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { MemoryProgress, MemoryStatus } from "../api/types";
import MemoryPanel from "./MemoryPanel";

vi.mock("../api", () => ({
  api: { getMemoryStatus: vi.fn(), getMemoryProgress: vi.fn(), repairMemory: vi.fn() },
}));

const failed: MemoryStatus = {
  pending: 0,
  running: 0,
  failed: 1,
  missing: 1,
  ready: 2,
  total: 4,
  items: [
    {
      id: "job",
      source_id: "chapter",
      source_type: "chapter_summary",
      status: "error",
      attempts: 1,
      error: "摘要生成失败",
      updated_at: "2026-11-11T00:00:00Z",
    },
  ],
};
const pending: MemoryStatus = { ...failed, pending: 2, failed: 0, missing: 0, items: [] };
const ready: MemoryStatus = { ...pending, pending: 0, ready: 4 };
const queued: MemoryProgress = {
  pending: 2,
  running: 0,
  failed: 0,
  ready: 2,
  total: 4,
  items: [],
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
  vi.mocked(api.getMemoryStatus).mockResolvedValue(failed);
  vi.mocked(api.getMemoryProgress).mockResolvedValue(queued);
  vi.mocked(api.repairMemory).mockResolvedValue(queued);
});
afterEach(() => vi.useRealTimers());

describe("创作记忆状态与修复", () => {
  it("查询失败显示错误，用户可以重新加载", async () => {
    vi.mocked(api.getMemoryStatus).mockRejectedValueOnce(new Error("状态暂时不可用"));
    await act(async () => {
      render(<MemoryPanel bookId="book-a" />);
    });
    expect(screen.getByText("状态暂时不可用")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "重试查询" }));
    });
    expect(screen.getByText("失败 1")).toBeInTheDocument();
    expect(screen.queryByText("状态暂时不可用")).not.toBeInTheDocument();
  });

  it("人物和剧情事实的同步失败能单独定位", async () => {
    vi.mocked(api.getMemoryStatus).mockResolvedValue({
      ...failed,
      items: [{ ...failed.items[0], source_type: "chapter_facts", error: "正文事实需重试" }],
    });
    await act(async () => {
      render(<MemoryPanel bookId="book-a" />);
    });
    expect(screen.getByText("章节事实")).toBeInTheDocument();
    expect(screen.getByText("正文事实需重试")).toBeInTheDocument();
  });

  it("修复失败保留可重试入口，成功后显示后台处理进度", async () => {
    vi.mocked(api.repairMemory).mockRejectedValueOnce(new Error("修复提交失败"));
    await act(async () => {
      render(<MemoryPanel bookId="book-a" />);
    });
    const repairButton = screen.getByRole("button", { name: "修复缺失或失败的记忆" });
    await act(async () => {
      fireEvent.click(repairButton);
    });
    expect(screen.getByText("修复提交失败")).toBeInTheDocument();
    expect(repairButton).toBeEnabled();
    vi.mocked(api.getMemoryStatus).mockResolvedValue(ready);
    vi.mocked(api.getMemoryProgress)
      .mockResolvedValueOnce(queued)
      .mockResolvedValueOnce({
        ...queued,
        pending: 0,
        ready: 4,
      });
    await act(async () => {
      fireEvent.click(repairButton);
    });
    expect(api.repairMemory).toHaveBeenCalledTimes(2);
    expect(api.repairMemory).toHaveBeenLastCalledWith("book-a");
    expect(screen.getByText("等待 2")).toBeInTheDocument();
    expect(screen.queryByText("修复提交失败")).not.toBeInTheDocument();
    expect(repairButton).toBeDisabled();
    expect(api.getMemoryStatus).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(api.getMemoryProgress).toHaveBeenCalledTimes(1);
    expect(api.getMemoryStatus).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(api.getMemoryProgress).toHaveBeenCalledTimes(2);
    expect(api.getMemoryStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText("可用 4 / 4")).toBeInTheDocument();
  });

  it("后台处理时串行轮询，完成后停止；查询失败不会无限刷请求", async () => {
    const slow = deferred<MemoryStatus>();
    vi.mocked(api.getMemoryStatus).mockResolvedValueOnce(pending).mockResolvedValue(ready);
    vi.mocked(api.getMemoryProgress).mockReturnValueOnce(slow.promise).mockResolvedValue(ready);
    await act(async () => {
      render(<MemoryPanel bookId="book-a" />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(12500);
    });
    expect(api.getMemoryStatus).toHaveBeenCalledTimes(1);
    expect(api.getMemoryProgress).toHaveBeenCalledTimes(1);
    await act(async () => {
      slow.resolve(pending);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(screen.getByText("可用 4 / 4")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(25000);
    });
    expect(api.getMemoryStatus).toHaveBeenCalledTimes(2);
    expect(api.getMemoryProgress).toHaveBeenCalledTimes(2);
    vi.mocked(api.getMemoryStatus).mockRejectedValueOnce(new Error("网络已断开"));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(25000);
    });
    expect(api.getMemoryStatus).toHaveBeenCalledTimes(3);
    expect(screen.getByText("网络已断开")).toBeInTheDocument();
  });

  it("修复期间阻止重复提交，旧查询响应不会覆盖修复状态", async () => {
    const oldQuery = deferred<MemoryStatus>();
    const repair = deferred<MemoryProgress>();
    vi.mocked(api.repairMemory).mockReturnValue(repair.promise);
    await act(async () => {
      render(<MemoryPanel bookId="book-a" />);
    });
    vi.mocked(api.getMemoryStatus).mockReturnValueOnce(oldQuery.promise).mockResolvedValue(pending);
    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "修复缺失或失败的记忆" }));
      fireEvent.click(screen.getByRole("button", { name: /修复缺失或失败的记忆/ }));
      oldQuery.resolve(ready);
    });
    expect(api.repairMemory).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("可用 4 / 4")).not.toBeInTheDocument();
    await act(async () => {
      repair.resolve(queued);
    });
    expect(screen.getByText("等待 2")).toBeInTheDocument();
  });

  it("切换书籍后忽略旧书的查询和修复结果", async () => {
    const oldQuery = deferred<MemoryStatus>();
    vi.mocked(api.getMemoryStatus).mockReturnValueOnce(oldQuery.promise).mockResolvedValue(ready);
    const view = render(<MemoryPanel bookId="book-a" />);
    await act(async () => {
      view.rerender(<MemoryPanel bookId="book-b" />);
    });
    expect(api.getMemoryStatus).toHaveBeenLastCalledWith("book-b", expect.any(AbortSignal));
    await act(async () => {
      oldQuery.resolve(failed);
    });
    expect(screen.getByText("可用 4 / 4")).toBeInTheDocument();
    expect(screen.queryByText("失败 1")).not.toBeInTheDocument();
  });

  it("同步中只查轻量进度，可用数量仍来自完整校验；离开面板取消请求", async () => {
    vi.mocked(api.getMemoryStatus).mockResolvedValue(pending);
    vi.mocked(api.getMemoryProgress).mockResolvedValue({
      ...pending,
      ready: 99,
      total: 100,
      pending: 1,
    });
    const view = render(<MemoryPanel bookId="book-a" />);
    await act(async () => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(api.getMemoryStatus).toHaveBeenCalledTimes(1);
    expect(api.getMemoryProgress).toHaveBeenCalledTimes(4);
    expect(screen.getByText("可用 2 / 4")).toBeInTheDocument();
    expect(screen.getByText("等待 1")).toBeInTheDocument();
    expect(screen.queryByText("可用 99 / 100")).not.toBeInTheDocument();
    const signal = vi.mocked(api.getMemoryProgress).mock.calls[0][1];
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });
});
