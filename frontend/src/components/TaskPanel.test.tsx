import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskPanel from "./TaskPanel";
import type { GenerationTask } from "../api/types";

vi.mock("../api", () => ({
  api: {
    listTasks: vi.fn(),
    getMemoryStatus: vi.fn(),
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
import { sseFetch } from "../api/client";

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
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.getTree).mockResolvedValue({ volumes: [], loose_chapters: [] });
    vi.mocked(api.listTasks).mockResolvedValue([]);
    vi.mocked(api.getMemoryStatus).mockResolvedValue({
      pending: 0,
      running: 0,
      failed: 0,
      missing: 0,
      ready: 0,
      total: 0,
      items: [],
    });
  });

  it("提交期间防止重复启动，失败后恢复按钮并显示错误", async () => {
    let reject!: (error: Error) => void;
    vi.mocked(api.startNovelFromBook).mockReturnValue(
      new Promise((_resolve, no) => {
        reject = no;
      })
    );
    renderPanel();
    const button = screen.getByRole("button", { name: /一键全自动生成/ });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    fireEvent.click(button);
    expect(api.startNovelFromBook).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    await act(async () => {
      reject(new Error("任务启动失败"));
    });
    expect(await screen.findByText("任务启动失败")).toBeInTheDocument();
    expect(button).toBeEnabled();
  });

  it("启动成功后等待任务列表刷新，失败只禁用依赖任务状态的启动入口", async () => {
    const task: GenerationTask = {
      id: "task",
      book_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      type: "novel",
      target_id: null,
      status: "running",
      progress: 0,
      error: "",
      checkpoint_json: "{}",
      created_at: "2026-01-01T00:00:00Z",
    };
    let reject!: (error: Error) => void;
    const list = new Promise<GenerationTask[]>((_, no) => {
      reject = no;
    });
    vi.mocked(api.startNovelFromBook).mockResolvedValue(task);
    vi.mocked(api.listTasks)
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(list)
      .mockResolvedValueOnce([{ ...task, status: "done" }]);
    renderPanel();
    const start = screen.getByRole("button", { name: "一键全自动生成" });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);
    await waitFor(() => expect(api.listTasks).toHaveBeenCalledTimes(2));
    expect(start).toBeDisabled();
    fireEvent.click(start);
    expect(api.startNovelFromBook).toHaveBeenCalledTimes(1);
    await act(async () => {
      reject(new Error("启动后暂时读不到任务列表"));
    });
    expect(screen.getByText("启动后暂时读不到任务列表")).toBeVisible();
    expect(start).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重试任务列表" }));
    await waitFor(() => expect(start).toBeEnabled());
  });

  it.each([
    { name: /一键全自动生成/, start: "startNovelFromBook", type: "novel", manual: false },
    { name: /AI 自主创作/, start: "startAgentFromBook", type: "agent", manual: false },
    { name: /全书批量生成正文/, start: "startBookGeneration", type: "book", manual: true },
  ] as const)(
    "$start 返回已保存的错误任务时显示失败和重试入口",
    async ({ name, start, type, manual }) => {
      const task: GenerationTask = {
        id: "failed-task",
        book_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        type,
        target_id: null,
        status: "error",
        progress: 0,
        error: "后台暂时无法调度任务",
        checkpoint_json: "{}",
        created_at: "2026-01-01T00:00:00Z",
      };
      vi.mocked(api[start]).mockResolvedValue(task);
      const user = userEvent.setup();
      renderPanel();
      if (manual) await user.click(screen.getByText("手工分层（生成后维护）"));
      const button = await screen.findByRole("button", { name });
      await waitFor(() => expect(button).toBeEnabled());
      vi.mocked(api.listTasks).mockResolvedValue([task]);
      await user.click(button);

      expect(await screen.findByRole("button", { name: /^重\s*试$/ })).toBeVisible();
      expect(screen.getAllByText(task.error)[0]).toBeVisible();
      expect(screen.queryByText(/已启动/)).not.toBeInTheDocument();
    }
  );

  it("手工生成互斥且限制日志长度，离开面板时中止 SSE", async () => {
    vi.mocked(sseFetch).mockReturnValue(new Promise(() => {}));
    const view = renderPanel();
    const user = userEvent.setup();
    await user.click(screen.getByText("手工分层（生成后维护）"));
    const generate = await screen.findByRole("button", { name: /1. 生成世界观/ });
    await waitFor(() => expect(generate).toBeEnabled());
    await user.click(generate);
    expect(sseFetch).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /2. 生成人物/ })).toBeDisabled();
    const handlers = vi.mocked(sseFetch).mock.calls[0][2];
    act(() => handlers.onEvent?.("token", "正文".repeat(30_000)));
    expect(view.container.querySelector(".nf-log")?.textContent?.length).toBe(50_000);
    view.unmount();
    expect(handlers.signal?.aborted).toBe(true);
    act(() => handlers.onEvent?.("error", "过期响应"));
    expect(screen.queryByText(/过期响应/)).not.toBeInTheDocument();
  });

  it("手工阶段错误会恢复按钮并允许重试", async () => {
    vi.mocked(sseFetch).mockReturnValue(new Promise(() => {}));
    renderPanel();
    const user = userEvent.setup();
    await user.click(screen.getByText("手工分层（生成后维护）"));
    const generate = await screen.findByRole("button", { name: /1. 生成世界观/ });
    await waitFor(() => expect(generate).toBeEnabled());
    await user.click(generate);
    act(() => vi.mocked(sseFetch).mock.calls[0][2].onError?.(new Error("连接中断")));
    expect(screen.getByText(/错误:连接中断/)).toBeInTheDocument();
    await waitFor(() => expect(generate).toBeEnabled());
    await user.click(generate);
    expect(sseFetch).toHaveBeenCalledTimes(2);
  });
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
