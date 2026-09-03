import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { describe, expect, it, vi } from "vitest";
import TaskPanel from "./TaskPanel";

vi.mock("../api", () => ({
  api: {
    listTasks: vi.fn(),
    getTree: vi.fn(),
    startNovelFromBook: vi.fn(),
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
        <TaskPanel bookId="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" />
      </AntdApp>
    </ConfigProvider>
  );
}

describe("TaskPanel", () => {
  it("点击一键全自动生成会调用 from-book 接口", async () => {
    vi.mocked(api.getTree).mockResolvedValue({ volumes: [], loose_chapters: [] });
    vi.mocked(api.listTasks).mockResolvedValue([]);
    vi.mocked(api.startNovelFromBook).mockResolvedValue({
      id: 9,
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

  it("任务列表展示类型与当前阶段", async () => {
    vi.mocked(api.getTree).mockResolvedValue({ volumes: [], loose_chapters: [] });
    vi.mocked(api.listTasks).mockResolvedValue([
      {
        id: 3,
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
    expect(await screen.findByText(/#3 全书自动生成 · 生成人物/)).toBeInTheDocument();
  });
});
