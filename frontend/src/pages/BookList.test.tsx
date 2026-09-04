import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Book } from "../api/types";
import BookList from "./BookList";

vi.mock("../api", () => ({
  api: {
    listBooks: vi.fn(),
    startNovelGeneration: vi.fn(),
    createBook: vi.fn(),
    deleteBook: vi.fn(),
  },
}));

import { api } from "../api";

function renderWithProviders() {
  return render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter>
          <BookList />
        </MemoryRouter>
      </AntdApp>
    </ConfigProvider>
  );
}

describe("BookList", () => {
  it("没有书籍时展示空状态提示", async () => {
    vi.mocked(api.listBooks).mockResolvedValue([]);
    renderWithProviders();
    expect(await screen.findByText(/还没有书籍/)).toBeInTheDocument();
  });

  it("书籍卡片展示简介,不把用户原话 brief 当作简介", async () => {
    const books: Book[] = [
      {
        id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        title: "未命名",
        genre: "",
        tone: "",
        intro: "",
        brief: "AI时代，中年程序员靠古法编程力挽狂澜",
        target_words: 10000,
        key_elements: "[]",
        created_at: "2026-01-01T00:00:00Z",
      },
    ];
    vi.mocked(api.listBooks).mockResolvedValue(books);
    renderWithProviders();
    expect(await screen.findByText("未命名")).toBeInTheDocument();
    expect(screen.queryByText(/中年程序员靠古法编程/)).not.toBeInTheDocument();
    expect(screen.getByText("暂无简介")).toBeInTheDocument();
  });

  it("加载到书籍列表后渲染书名", async () => {
    const books: Book[] = [
      {
        id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        title: "剑起苍穹",
        genre: "玄幻",
        tone: "热血",
        intro: "一个关于剑的故事",
        brief: "",
        target_words: 100000,
        key_elements: "[]",
        created_at: "2026-01-01T00:00:00Z",
      },
    ];
    vi.mocked(api.listBooks).mockResolvedValue(books);
    renderWithProviders();
    expect(await screen.findByText("剑起苍穹")).toBeInTheDocument();
  });

  it("提交一句话出书会调用自动生成接口", async () => {
    vi.mocked(api.listBooks).mockResolvedValue([]);
    vi.mocked(api.startNovelGeneration).mockResolvedValue({
      book: {
        id: "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        title: "少年觉醒",
        intro: "",
        brief: "一个少年觉醒血脉的故事",
        genre: "",
        tone: "",
        target_words: 100000,
        key_elements: "[]",
        created_at: "2026-01-01T00:00:00Z",
      },
      task: {
        id: "task-1",
        book_id: "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        type: "novel",
        target_id: null,
        status: "running",
        progress: 0,
        error: "",
        checkpoint_json: "{}",
        created_at: "2026-01-01T00:00:00Z",
      },
    });
    const user = userEvent.setup();
    renderWithProviders();
    await user.click(await screen.findByRole("button", { name: /一句话出书/ }));
    const textarea = await screen.findByPlaceholderText(/少年林逸/);
    await user.type(textarea, "一个少年觉醒血脉的故事");
    await user.click(screen.getByRole("button", { name: /开始生成/ }));
    expect(api.startNovelGeneration).toHaveBeenCalledWith({
      brief: "一个少年觉醒血脉的故事",
      target_words: 100000,
    });
  });
});
