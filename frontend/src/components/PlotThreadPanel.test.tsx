import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { PlotThread } from "../api/types";
import PlotThreadPanel from "./PlotThreadPanel";

vi.mock("../api", () => ({ api: { listPlotThreads: vi.fn() } }));

const thread: PlotThread = {
  id: "thread-1",
  book_id: "book-1",
  name: "寻找姐姐",
  description: "寻找失踪姐姐的下落",
  status: "active",
  derived_status: "closed",
};

beforeEach(() => vi.resetAllMocks());

describe("剧情进展", () => {
  it("正文派生状态优先展示，刷新后的失效事实回退到原始计划", async () => {
    vi.mocked(api.listPlotThreads)
      .mockResolvedValueOnce([thread])
      .mockResolvedValue([{ ...thread, derived_status: null }]);
    render(<PlotThreadPanel bookId="book-1" />);
    expect(await screen.findByText("已收束")).toBeInTheDocument();
    expect(screen.getByText("依据正文事实")).toBeInTheDocument();
    expect(screen.getByText(thread.description)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "刷新剧情进展" }));
    expect(await screen.findByText("推进中")).toBeInTheDocument();
    expect(screen.getByText("依据剧情计划，尚无正文事实更新")).toBeInTheDocument();
    expect(screen.queryByText("已收束")).not.toBeInTheDocument();
  });

  it("查询失败可重试并恢复到空状态", async () => {
    vi.mocked(api.listPlotThreads)
      .mockRejectedValueOnce(new Error("暂时不可用"))
      .mockResolvedValue([]);
    render(<PlotThreadPanel bookId="book-1" />);
    expect(await screen.findByText("暂时不可用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findByText("尚未创建剧情线程")).toBeInTheDocument();
    expect(screen.queryByText("暂时不可用")).not.toBeInTheDocument();
  });

  it("切换书籍后忽略旧书返回的剧情", async () => {
    let resolveOld!: (items: PlotThread[]) => void;
    vi.mocked(api.listPlotThreads)
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveOld = resolve;
        })
      )
      .mockResolvedValue([]);
    const view = render(<PlotThreadPanel bookId="book-1" />);
    view.rerender(<PlotThreadPanel bookId="book-2" />);
    expect(await screen.findByText("尚未创建剧情线程")).toBeInTheDocument();
    await act(async () => resolveOld([thread]));
    expect(screen.queryByText("寻找姐姐")).not.toBeInTheDocument();
    expect(api.listPlotThreads).toHaveBeenLastCalledWith("book-2");
  });
});
