import { render, screen } from "@testing-library/react";
import { App as AntdApp, ConfigProvider } from "antd";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Book } from "../api/types";
import BookList from "./BookList";

vi.mock("../api", () => ({
  api: {
    listBooks: vi.fn(),
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
    expect(await screen.findByText("还没有书籍,点击右上角新建一本吧。")).toBeInTheDocument();
  });

  it("加载到书籍列表后渲染书名", async () => {
    const books: Book[] = [
      {
        id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        title: "剑起苍穹",
        genre: "玄幻",
        tone: "热血",
        intro: "一个关于剑的故事",
        target_words: 100000,
        created_at: "2026-01-01T00:00:00Z",
      },
    ];
    vi.mocked(api.listBooks).mockResolvedValue(books);
    renderWithProviders();
    expect(await screen.findByText("剑起苍穹")).toBeInTheDocument();
  });
});
