import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes, useParams, useSearchParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import AgentChat from "./AgentChat";

vi.mock("../api", () => ({
  api: {
    getBook: vi.fn(),
    getTask: vi.fn(),
    getTaskTrace: vi.fn(),
    startAgentGeneration: vi.fn(),
    startNovelGeneration: vi.fn(),
  },
}));

import { api } from "../api";

function renderChat(initialEntries = ["/agent"]) {
  return render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter initialEntries={initialEntries}>
          <AgentChat />
        </MemoryRouter>
      </AntdApp>
    </ConfigProvider>
  );
}

describe("AgentChat", () => {
  it("初始展示欢迎输入框", async () => {
    renderChat();
    expect(await screen.findByText("AI 自主创作")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/少年林逸/)).toBeInTheDocument();
  });

  it("提交后调用 startAgentGeneration 并展示对话头部", async () => {
    vi.mocked(api.startAgentGeneration).mockResolvedValue({
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
        type: "agent",
        target_id: null,
        status: "running",
        progress: 0,
        error: "",
        checkpoint_json: "{}",
        created_at: "2026-01-01T00:00:00Z",
      },
    });
    vi.mocked(api.getTask).mockResolvedValue({
      id: "task-1",
      book_id: "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
      type: "agent",
      target_id: null,
      status: "running",
      progress: 10,
      error: "",
      checkpoint_json: "{}",
      created_at: "2026-01-01T00:00:00Z",
    });
    vi.mocked(api.getTaskTrace).mockResolvedValue([
      {
        id: "trace-1",
        task_id: "task-1",
        step_index: 1,
        kind: "decision",
        tool_name: "get_book_state",
        payload: "get_book_state({})",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    vi.mocked(api.getBook).mockResolvedValue({
      id: "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
      title: "少年觉醒",
      intro: "",
      brief: "一个少年觉醒血脉的故事",
      genre: "",
      tone: "",
      target_words: 100000,
      key_elements: "[]",
      created_at: "2026-01-01T00:00:00Z",
    });

    const user = userEvent.setup();
    renderChat();
    await user.type(screen.getByPlaceholderText(/少年林逸/), "一个少年觉醒血脉的故事");
    const sendButtons = screen.getAllByRole("button");
    const sendButton = sendButtons[sendButtons.length - 1];
    await user.click(sendButton);

    expect(api.startAgentGeneration).toHaveBeenCalledWith({
      brief: "一个少年觉醒血脉的故事",
      target_words: 100000,
    });
    expect(await screen.findByText("少年觉醒")).toBeInTheDocument();
    expect(await screen.findByText(/调用工具:get_book_state/)).toBeInTheDocument();
  });

  it("400 错误时自动降级为固定流程生成", async () => {
    vi.mocked(api.startAgentGeneration).mockRejectedValue(
      new Error("400: 当前 LLM 后端不支持自主决策")
    );
    vi.mocked(api.startNovelGeneration).mockResolvedValue({
      book: {
        id: "cccccccc-dddd-eeee-ffff-000000000000",
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
        id: "task-2",
        book_id: "cccccccc-dddd-eeee-ffff-000000000000",
        type: "novel",
        target_id: null,
        status: "running",
        progress: 0,
        error: "",
        checkpoint_json: "{}",
        created_at: "2026-01-01T00:00:00Z",
      },
    });
    vi.mocked(api.getTask).mockResolvedValue({
      id: "task-2",
      book_id: "cccccccc-dddd-eeee-ffff-000000000000",
      type: "novel",
      target_id: null,
      status: "running",
      progress: 5,
      error: "",
      checkpoint_json: "{}",
      created_at: "2026-01-01T00:00:00Z",
    });
    vi.mocked(api.getTaskTrace).mockResolvedValue([]);
    vi.mocked(api.getBook).mockResolvedValue({
      id: "cccccccc-dddd-eeee-ffff-000000000000",
      title: "少年觉醒",
      intro: "",
      brief: "一个少年觉醒血脉的故事",
      genre: "",
      tone: "",
      target_words: 100000,
      key_elements: "[]",
      created_at: "2026-01-01T00:00:00Z",
    });

    const user = userEvent.setup();
    renderChat();
    await user.type(screen.getByPlaceholderText(/少年林逸/), "一个少年觉醒血脉的故事");
    const sendButtons = screen.getAllByRole("button");
    await user.click(sendButtons[sendButtons.length - 1]);

    await waitFor(() => expect(api.startNovelGeneration).toHaveBeenCalled());
    expect(await screen.findByText("少年觉醒")).toBeInTheDocument();
  });

  it("通过 URL 参数打开已有任务时直接展示对话视图", async () => {
    vi.mocked(api.getBook).mockResolvedValue({
      id: "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
      title: "少年觉醒",
      intro: "",
      brief: "",
      genre: "",
      tone: "",
      target_words: 100000,
      key_elements: "[]",
      created_at: "2026-01-01T00:00:00Z",
    });
    vi.mocked(api.getTask).mockResolvedValue({
      id: "task-1",
      book_id: "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
      type: "agent",
      target_id: null,
      status: "done",
      progress: 100,
      error: "",
      checkpoint_json: "{}",
      created_at: "2026-01-01T00:00:00Z",
    });
    vi.mocked(api.getTaskTrace).mockResolvedValue([]);

    renderChat(["/agent?bookId=bbbbbbbb-cccc-dddd-eeee-ffffffffffff&taskId=task-1"]);
    expect(await screen.findByText("少年觉醒")).toBeInTheDocument();
    expect(await screen.findByText(/创作完成/)).toBeInTheDocument();
  });

  it("生成中点击查看书籍会带上返回对话所需的任务参数", async () => {
    vi.mocked(api.getBook).mockResolvedValue({
      id: "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
      title: "少年觉醒",
      intro: "",
      brief: "",
      genre: "",
      tone: "",
      target_words: 100000,
      key_elements: "[]",
      created_at: "2026-01-01T00:00:00Z",
    });
    vi.mocked(api.getTask).mockResolvedValue({
      id: "task-1",
      book_id: "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
      type: "agent",
      target_id: null,
      status: "running",
      progress: 40,
      error: "",
      checkpoint_json: "{}",
      created_at: "2026-01-01T00:00:00Z",
    });
    vi.mocked(api.getTaskTrace).mockResolvedValue([]);

    function BookSearchProbe() {
      const { bookId } = useParams();
      const [params] = useSearchParams();
      return (
        <div>
          书籍页 bookId={bookId} view={params.get("view")} fromTask={params.get("fromTask")}
        </div>
      );
    }

    const user = userEvent.setup();
    render(
      <ConfigProvider>
        <AntdApp>
          <MemoryRouter
            initialEntries={["/agent?bookId=bbbbbbbb-cccc-dddd-eeee-ffffffffffff&taskId=task-1"]}
          >
            <Routes>
              <Route path="/agent" element={<AgentChat />} />
              <Route path="/books/:bookId" element={<BookSearchProbe />} />
            </Routes>
          </MemoryRouter>
        </AntdApp>
      </ConfigProvider>
    );

    await user.click(await screen.findByRole("button", { name: "查看书籍" }));
    expect(await screen.findByText(/fromTask=task-1/)).toBeInTheDocument();
    expect(screen.getByText(/view=tasks/)).toBeInTheDocument();
  });
});
