import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Book } from "../api/types";
import BookWorkspace from "./BookWorkspace";

vi.mock("../api", () => ({
  api: {
    getBook: vi.fn(),
    exportUrl: vi.fn(),
  },
}));

vi.mock("../components/TaskPanel", () => ({ default: () => <div>任务面板</div> }));
vi.mock("../components/CatalogTree", () => ({ default: () => <div>目录</div> }));
vi.mock("../components/ChapterEditor", () => ({ default: () => <div>编辑器</div> }));
vi.mock("../components/CharacterPanel", () => ({ default: () => <div>人物</div> }));
vi.mock("../components/WorldbuildingPanel", () => ({ default: () => <div>设定</div> }));

import { api } from "../api";

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
    await waitFor(() => expect(api.getBook).toHaveBeenCalledWith(bookId));
    await userEvent.setup().click(screen.getByRole("button", { name: "切换书籍" }));
    expect(await screen.findByText("新书工作区")).toBeInTheDocument();
    await act(async () => {
      if (outcome === "success") resolve(makeBook(bookId, "迟到旧书"));
      else reject(new Error("旧书加载失败"));
    });
    expect(screen.getByText("新书工作区")).toBeInTheDocument();
    expect(screen.queryByText("迟到旧书")).not.toBeInTheDocument();
    expect(screen.queryByText("旧书加载失败")).not.toBeInTheDocument();
  });

  it("任务视图轮询失败保持已有书籍，展示错误并支持立即重试", async () => {
    vi.mocked(api.getBook)
      .mockResolvedValueOnce(makeBook(bookId, "生成中的书籍"))
      .mockRejectedValueOnce(new Error("任务信息刷新失败"))
      .mockResolvedValueOnce(makeBook(bookId, "最新任务信息"));
    renderWorkspace(`/books/${bookId}?view=tasks`);
    expect(await screen.findByText("生成中的书籍")).toBeInTheDocument();
    expect(await screen.findByText("任务信息刷新失败", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText("生成中的书籍")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("最新任务信息")).toBeInTheDocument();
  });
});
