import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Book } from "../api/types";
import BookWorkspace from "./BookWorkspace";

vi.mock("../api", () => ({
  api: {
    getBook: vi.fn(),
    exportUrl: vi.fn(),
  },
}));

vi.mock("../components/TaskPanel", () => ({
  default: ({
    onGenerationActivityChange,
  }: {
    onGenerationActivityChange: (active: boolean) => void;
  }) => (
    <div>
      任务面板
      <button onClick={() => onGenerationActivityChange(true)}>任务开始</button>
      <button onClick={() => onGenerationActivityChange(false)}>任务结束</button>
    </div>
  ),
}));
vi.mock("../components/CatalogTree", () => ({ default: () => <div>目录</div> }));
vi.mock("../components/ChapterEditor", () => ({ default: () => <div>编辑器</div> }));
vi.mock("../components/CharacterPanel", () => ({ default: () => <div>人物</div> }));
vi.mock("../components/WorldbuildingPanel", () => ({ default: () => <div>设定</div> }));
vi.mock("../components/PlotThreadPanel", () => ({ default: () => <div>剧情面板已加载</div> }));
vi.mock("../utils/download", () => ({ downloadFile: vi.fn() }));

import { api } from "../api";
import { downloadFile } from "../utils/download";

const bookId = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff";
const makeBook = (id: string, title: string): Book => ({
  id,
  title,
  intro: "",
  brief: "",
  genre: "",
  tone: "",
  target_words: 100000,
  key_elements: "[]",
  created_at: "2026-01-01T00:00:00Z",
});

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.useRealTimers());

function SwitchBook() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/books/next-book")}>切换书籍</button>;
}

function renderWorkspace(initialEntry: string) {
  return render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter initialEntries={[initialEntry]}>
          <SwitchBook />
          <Routes>
            <Route path="/books/:bookId" element={<BookWorkspace />} />
            <Route path="/agent" element={<div>AI 自主创作对话</div>} />
            <Route path="/" element={<div>书籍列表</div>} />
          </Routes>
        </MemoryRouter>
      </AntdApp>
    </ConfigProvider>
  );
}

describe("BookWorkspace", () => {
  it("点击剧情入口按需加载当前书的剧情面板", async () => {
    vi.mocked(api.getBook).mockResolvedValue(makeBook(bookId, "剧情测试"));
    const user = userEvent.setup();
    renderWorkspace(`/books/${bookId}`);
    expect(screen.queryByText("剧情面板已加载")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("menuitem", { name: /剧情/ }));
    expect(await screen.findByText("剧情面板已加载")).toBeInTheDocument();
  });

  it("导出服务失败时在工作区显示提示", async () => {
    vi.mocked(api.getBook).mockResolvedValue(makeBook(bookId, "要导出的小说"));
    vi.mocked(api.exportUrl).mockReturnValue("/api/books/test/export?format=txt");
    vi.mocked(downloadFile).mockRejectedValueOnce(new Error("导出服务暂不可用"));
    const user = userEvent.setup();
    renderWorkspace(`/books/${bookId}`);

    await user.hover(await screen.findByRole("button", { name: /导\s*出/ }));
    await user.click(await screen.findByRole("menuitem", { name: "导出 TXT" }));
    expect(await screen.findByText("导出服务暂不可用")).toBeVisible();
    expect(downloadFile).toHaveBeenCalledWith(
      "/api/books/test/export?format=txt",
      "要导出的小说.txt"
    );
  });

  it("从 AI 创作跳来时展示返回入口并可回到对话页", async () => {
    vi.mocked(api.getBook).mockResolvedValue({
      id: bookId,
      title: "少年觉醒",
      intro: "",
      brief: "",
      genre: "玄幻",
      tone: "",
      target_words: 100000,
      key_elements: "[]",
      created_at: "2026-01-01T00:00:00Z",
    });

    const user = userEvent.setup();
    renderWorkspace(`/books/${bookId}?view=tasks&fromTask=1`);
    await user.click(await screen.findByRole("button", { name: /返回 AI 创作/ }));
    expect(await screen.findByText("AI 自主创作对话")).toBeInTheDocument();
  });

  it("普通进入书籍页时不展示返回 AI 创作", async () => {
    vi.mocked(api.getBook).mockResolvedValue({
      id: bookId,
      title: "少年觉醒",
      intro: "",
      brief: "",
      genre: "玄幻",
      tone: "",
      target_words: 100000,
      key_elements: "[]",
      created_at: "2026-01-01T00:00:00Z",
    });

    renderWorkspace(`/books/${bookId}`);
    expect(await screen.findByText("少年觉醒")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /返回 AI 创作/ })).not.toBeInTheDocument();
  });

  it("首次请求失败显示错误和重试入口，重试成功后进入工作区", async () => {
    vi.mocked(api.getBook)
      .mockRejectedValueOnce(new Error("书籍服务暂不可用"))
      .mockResolvedValueOnce(makeBook(bookId, "恢复后的书籍"));
    renderWorkspace(`/books/${bookId}`);
    expect(await screen.findByText("书籍服务暂不可用")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("恢复后的书籍")).toBeInTheDocument();
    expect(screen.queryByText("书籍服务暂不可用")).not.toBeInTheDocument();
  });

  it.each(["success", "failure"])("路由切换后忽略旧书的迟到%s响应", async (outcome) => {
    let resolve!: (book: Book) => void;
    let reject!: (error: Error) => void;
    const old = new Promise<Book>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    vi.mocked(api.getBook)
      .mockReturnValueOnce(old)
      .mockResolvedValueOnce(makeBook("next-book", "新书工作区"));
    renderWorkspace(`/books/${bookId}`);
    await waitFor(() => expect(api.getBook).toHaveBeenCalledWith(bookId, expect.any(AbortSignal)));
    const [, previousSignal] = vi.mocked(api.getBook).mock.calls[0];
    await userEvent.setup().click(screen.getByRole("button", { name: "切换书籍" }));
    expect(await screen.findByText("新书工作区")).toBeInTheDocument();
    expect(previousSignal?.aborted).toBe(true);
    await act(async () => {
      if (outcome === "success") resolve(makeBook(bookId, "迟到旧书"));
      else reject(new Error("旧书加载失败"));
    });
    expect(screen.getByText("新书工作区")).toBeInTheDocument();
    expect(screen.queryByText("迟到旧书")).not.toBeInTheDocument();
    expect(screen.queryByText("旧书加载失败")).not.toBeInTheDocument();
  });

  it("任务活跃时才刷新书籍，失败可重试，任务结束后停止轮询", async () => {
    vi.mocked(api.getBook)
      .mockResolvedValueOnce(makeBook(bookId, "生成中的书籍"))
      .mockResolvedValueOnce(makeBook(bookId, "生成中的书籍"))
      .mockRejectedValueOnce(new Error("任务信息刷新失败"))
      .mockResolvedValueOnce(makeBook(bookId, "最新任务信息"))
      .mockResolvedValue(makeBook(bookId, "完成后的书籍"));
    renderWorkspace(`/books/${bookId}?view=tasks`);
    const start = await screen.findByRole("button", { name: "任务开始" });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(12000);
    });
    expect(api.getBook).toHaveBeenCalledTimes(1);
    await act(async () => {
      fireEvent.click(start);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(screen.getByText("任务信息刷新失败")).toBeInTheDocument();
    expect(screen.getByText("生成中的书籍")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    });
    expect(screen.getByText("最新任务信息")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "任务结束" }));
    });
    expect(screen.getByText("完成后的书籍")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(12000);
    });
    expect(api.getBook).toHaveBeenCalledTimes(5);
  });
});
