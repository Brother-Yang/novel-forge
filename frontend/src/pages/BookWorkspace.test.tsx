import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
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

function renderWorkspace(initialEntry: string) {
  return render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter initialEntries={[initialEntry]}>
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
});
