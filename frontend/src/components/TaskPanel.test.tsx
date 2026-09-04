import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import TaskPanel from "./TaskPanel";

vi.mock("../api", () => ({
  api: {
    listTasks: vi.fn(),
    getTree: vi.fn(),
    startNovelFromBook: vi.fn(),
    startAgentFromBook: vi.fn(),
    startBookGeneration: vi.fn(),
    pauseTask: vi.fn(),
    resumeTask: vi.fn(),
    retryTask: vi.fn(),
  },
  sseEndpoints: {
    worldbuilding: "/generate/worldbuilding",
    characters: "/generate/characters",
    outline: "/generate/outline",
    chapterOutlines: "/generate/chapter-outlines",
    chapter: "/generate/chapter",
  },
}));

vi.mock("../api/client", () => ({
  sseFetch: vi.fn(),
}));

import { api } from "../api";

function renderPanel() {
  return render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter>
          <TaskPanel bookId="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" />
        </MemoryRouter>
      </AntdApp>
    </ConfigProvider>
  );
}

describe("TaskPanel", () => {
  it("点击一键全自动生成会调用 from-book 接口", async () => {
    vi.mocked(api.getTree).mockResolvedValue({ volumes: [], loose_chapters: [] });
    vi.mocked(api.listTasks).mockResolvedValue([]);
    vi.mocked(api.startNovelFromBook).mockResolvedValue({
      id: "task-9",
      book_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      type: "novel",
      target_id: null,
      status: "running",
      progress: 0,
      error: "",
      checkpoint_json: '{"stage":"planner"}',
      created_at: "2026-01-01T00:00:00Z",
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: /一键全自动生成/ }));
    expect(api.startNovelFromBook).toHaveBeenCalledWith("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
  });

  it("点击 AI 自主创作会调用 agent/from-book 接口", async () => {
    vi.mocked(api.getTree).mockResolvedValue({ volumes: [], loose_chapters: [] });
    vi.mocked(api.listTasks).mockResolvedValue([]);
    vi.mocked(api.startAgentFromBook).mockResolvedValue({
      id: "task-11",
      book_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      type: "agent",
      target_id: null,
      status: "running",
      progress: 0,
      error: "",
      checkpoint_json: "{}",
      created_at: "2026-01-01T00:00:00Z",
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: /AI 自主创作/ }));
    expect(api.startAgentFromBook).toHaveBeenCalledWith("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
  });

  it("任务列表展示类型与当前阶段", async () => {
    vi.mocked(api.getTree).mockResolvedValue({ volumes: [], loose_chapters: [] });
    vi.mocked(api.listTasks).mockResolvedValue([
      {
        id: "task-3",
        book_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        type: "novel",
        target_id: null,
        status: "running",
        progress: 20,
        error: "",
        checkpoint_json: '{"stage":"characters"}',
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    renderPanel();
    expect(await screen.findByText(/#task-3 全书自动生成 · 生成人物/)).toBeInTheDocument();
  });

  it("agent 类型任务可点击查看对话跳回自主创作页", async () => {
    vi.mocked(api.getTree).mockResolvedValue({ volumes: [], loose_chapters: [] });
    vi.mocked(api.listTasks).mockResolvedValue([
      {
        id: "task-7",
        book_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        type: "agent",
        target_id: null,
        status: "running",
        progress: 40,
        error: "",
        checkpoint_json: "{}",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    const user = userEvent.setup();
    render(
      <ConfigProvider>
        <AntdApp>
          <MemoryRouter initialEntries={["/books/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]}>
            <Routes>
              <Route
                path="/books/:bookId"
                element={<TaskPanel bookId="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" />}
              />
              <Route path="/agent" element={<div>自主创作对话页</div>} />
            </Routes>
          </MemoryRouter>
        </AntdApp>
      </ConfigProvider>
    );
    await user.click(await screen.findByRole("button", { name: "查看对话" }));
    expect(await screen.findByText("自主创作对话页")).toBeInTheDocument();
  });
});
