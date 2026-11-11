import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes, useParams, useSearchParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentTracePage, AgentTracePageEntry, Book, GenerationTask } from "../api/types";
import { routePatterns } from "../router/paths";
import AgentChat from "./AgentChat";

vi.mock("../api", () => ({
  api: {
    getBook: vi.fn(),
    getTask: vi.fn(),
    getTaskTracePage: vi.fn(),
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

const book: Book = {
  id: "book-1",
  title: "第一本书",
  brief: "",
  intro: "",
  genre: "",
  tone: "",
  target_words: 10000,
  key_elements: "[]",
  created_at: "2026-01-01T00:00:00Z",
};

function agentTask(overrides: Partial<GenerationTask> = {}): GenerationTask {
  return {
    id: "task-1",
    book_id: book.id,
    type: "agent",
    target_id: null,
    status: "running",
    progress: 10,
    error: "",
    checkpoint_json: "{}",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function event(sequence: number, payload: string, taskId = "task-1"): AgentTracePageEntry {
  return {
    id: `${taskId}-event-${sequence}`,
    task_id: taskId,
    sequence,
    step_index: 1,
    kind: "log",
    tool_name: null,
    payload,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe("AgentChat", () => {
  beforeEach(() => vi.resetAllMocks());
  afterEach(() => vi.useRealTimers());

  it("初始展示欢迎输入框", async () => {
    renderChat();
    expect(await screen.findByText("AI 自主创作")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/少年林逸/)).toBeInTheDocument();
  });

  it("降级生成返回启动错误时进入已建书任务页", async () => {
    vi.mocked(api.startAgentGeneration).mockRejectedValue(new Error("400: 当前模型不支持自主决策"));
    vi.mocked(api.startNovelGeneration).mockResolvedValue({
      book,
      task: agentTask({ type: "novel", status: "error", error: "固定流程启动失败" }),
    });

    function BookDestination() {
      const { bookId } = useParams();
      const [query] = useSearchParams();
      return <div>{`${bookId} ${query.get("view")} ${query.get("fromTask")}`}</div>;
    }

    render(
      <ConfigProvider>
        <AntdApp>
          <MemoryRouter initialEntries={[routePatterns.agentChat]}>
            <Routes>
              <Route path={routePatterns.agentChat} element={<AgentChat />} />
              <Route path={routePatterns.book} element={<BookDestination />} />
            </Routes>
          </MemoryRouter>
        </AntdApp>
      </ConfigProvider>
    );
    const user = userEvent.setup();
    const input = screen.getByPlaceholderText(/少年林逸/);
    await user.type(input, "一次失败但保留书稿的尝试{enter}");

    expect(await screen.findByText("book-1 tasks task-1")).toBeVisible();
    expect(await screen.findByText("固定流程启动失败")).toBeVisible();
    expect(screen.queryByText(/已自动切换为固定流程生成/)).not.toBeInTheDocument();
  });

  it("输入法确认不提交，连续回车只发一次请求，失败后仍可重试", async () => {
    const first = deferred<Awaited<ReturnType<typeof api.startAgentGeneration>>>();
    vi.mocked(api.startAgentGeneration)
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce({ book, task: agentTask() });
    vi.mocked(api.getTask).mockResolvedValue(agentTask());
    vi.mocked(api.getBook).mockResolvedValue(book);
    vi.mocked(api.getTaskTracePage).mockResolvedValue({ entries: [], has_more: false });
    renderChat();
    const input = screen.getByPlaceholderText(/少年林逸/);
    fireEvent.change(input, { target: { value: "还在输入的故事" } });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 13, isComposing: true });
    expect(api.startAgentGeneration).not.toHaveBeenCalled();
    act(() => {
      fireEvent.keyDown(input, { key: "Enter", keyCode: 13 });
      fireEvent.keyDown(input, { key: "Enter", keyCode: 13 });
    });
    expect(api.startAgentGeneration).toHaveBeenCalledTimes(1);
    await act(async () => {
      first.reject(new Error("暂时无法启动"));
    });
    expect(await screen.findByText("暂时无法启动")).toBeVisible();
    expect(input).toHaveValue("还在输入的故事");
    fireEvent.keyDown(input, { key: "Enter", keyCode: 13 });
    expect(await screen.findByText(book.title)).toBeVisible();
    expect(api.startAgentGeneration).toHaveBeenCalledTimes(2);
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
    vi.mocked(api.getTaskTracePage).mockResolvedValue({
      has_more: false,
      entries: [
        {
          id: "trace-1",
          task_id: "task-1",
          step_index: 1,
          sequence: 1,
          kind: "decision",
          tool_name: "get_book_state",
          payload: "get_book_state({})",
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
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
    vi.mocked(api.getTaskTracePage).mockResolvedValue({ entries: [], has_more: false });
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
    vi.mocked(api.getTaskTracePage).mockResolvedValue({ entries: [], has_more: false });

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
    vi.mocked(api.getTaskTracePage).mockResolvedValue({ entries: [], has_more: false });

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

  it("增量轮询失败后从原游标继续补齐，阅读历史时保留滚动位置", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    vi.mocked(api.getTask).mockResolvedValue(agentTask());
    vi.mocked(api.getBook).mockResolvedValue(book);
    vi.mocked(api.getTaskTracePage)
      .mockResolvedValueOnce({ entries: [event(50, "正在阅读的轨迹")], has_more: true })
      .mockRejectedValueOnce(new Error("连接中断"))
      .mockResolvedValueOnce({ entries: [event(51, "第一条新轨迹")], has_more: true })
      .mockResolvedValueOnce({ entries: [event(52, "第二条新轨迹")], has_more: false });

    await act(async () => {
      renderChat(["/agent?taskId=task-1"]);
    });
    const region = screen.getByRole("region", { name: "执行轨迹" });
    Object.defineProperties(region, {
      scrollHeight: { get: () => (screen.queryByText(/第二条新轨迹/) ? 900 : 700) },
      clientHeight: { value: 300 },
    });
    region.scrollTop = 120;
    fireEvent.scroll(region);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("连接中断");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "重试加载轨迹" }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(vi.mocked(api.getTaskTracePage).mock.calls.map(([, cursor]) => cursor)).toEqual([
      {},
      { after: 50 },
      { after: 50 },
      { after: 51 },
    ]);
    expect(within(region).getAllByText(/第[一二]条新轨迹/)).toHaveLength(2);
    expect(region.scrollTop).toBe(120);
    fireEvent.click(screen.getByRole("button", { name: "有新轨迹，跳到最新" }));
    expect(region.scrollTop).toBe(600);
    expect(screen.queryByRole("button", { name: "有新轨迹，跳到最新" })).not.toBeInTheDocument();
  });

  it("已完成任务可重试加载更早轨迹，前插记录时保留原阅读位置", async () => {
    vi.mocked(api.getTask).mockResolvedValue(agentTask({ status: "done" }));
    vi.mocked(api.getBook).mockResolvedValue(book);
    vi.mocked(api.getTaskTracePage)
      .mockResolvedValueOnce({ entries: [event(51, "最近轨迹")], has_more: true })
      .mockRejectedValueOnce(new Error("暂时不可用"))
      .mockResolvedValueOnce({ entries: [event(50, "更早的记录")], has_more: false });
    renderChat(["/agent?taskId=task-1"]);

    const latest = await screen.findByText(/最近轨迹/);
    const region = screen.getByRole("region", { name: "执行轨迹" });
    Object.defineProperties(region, {
      scrollHeight: { value: 700 },
      clientHeight: { value: 300 },
    });
    Object.defineProperty(latest, "offsetTop", {
      get: () => (latest.previousElementSibling ? 170 : 70),
    });
    region.scrollTop = 20;
    fireEvent.scroll(region);
    fireEvent.click(screen.getByRole("button", { name: "加载更早轨迹" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("暂时不可用");
    expect(region.scrollTop).toBe(20);
    fireEvent.click(screen.getByRole("button", { name: /重试加载更早轨迹/ }));
    expect(await screen.findByText(/更早的记录/)).toBeInTheDocument();
    expect(region.scrollTop).toBe(120);
    expect(vi.mocked(api.getTaskTracePage).mock.calls.map(([, cursor]) => cursor)).toEqual([
      {},
      { before: 51 },
      { before: 51 },
    ]);
    expect(
      within(region)
        .getAllByText(/更早的记录|最近轨迹/)
        .map((node) => node.textContent)
    ).toEqual(["—— 更早的记录 ——", "—— 最近轨迹 ——"]);
  });

  it("任务终止后刷新最后一页，包括初始空轨迹之后写入的记录", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const finalTask = deferred<GenerationTask>();
    const earlierPoll = deferred<AgentTracePage>();
    vi.mocked(api.getTask)
      .mockResolvedValueOnce(agentTask())
      .mockReturnValueOnce(finalTask.promise);
    vi.mocked(api.getBook).mockResolvedValue(book);
    vi.mocked(api.getTaskTracePage)
      .mockResolvedValueOnce({ entries: [], has_more: false })
      .mockReturnValueOnce(earlierPoll.promise)
      .mockResolvedValueOnce({ entries: [event(1, "完成前写入的轨迹")], has_more: false });

    await act(async () => {
      renderChat(["/agent?taskId=task-1"]);
    });
    expect(screen.getByText(/AI 正在思考第一步/)).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    await act(async () => {
      finalTask.resolve(agentTask({ status: "done" }));
    });
    const [, , previousSignal] = vi.mocked(api.getTaskTracePage).mock.calls[1];
    expect(previousSignal.aborted).toBe(true);
    await act(async () => {
      earlierPoll.resolve({ entries: [event(1, "完成前写入的轨迹")], has_more: false });
    });
    expect(screen.getByText(/完成前写入的轨迹/)).toBeInTheDocument();
    expect(screen.getByText(/创作完成/)).toBeInTheDocument();
    expect(vi.mocked(api.getTaskTracePage).mock.calls.map(([, cursor]) => cursor)).toEqual([
      {},
      { after: 0 },
      { after: 0 },
    ]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(api.getTaskTracePage).toHaveBeenCalledTimes(3);
  });

  it("书籍请求挂起时仍轮询任务终态和最后的轨迹", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const delayedBook = deferred<Book>();
    vi.mocked(api.getTask)
      .mockResolvedValueOnce(agentTask())
      .mockResolvedValueOnce(agentTask({ status: "done" }));
    vi.mocked(api.getBook)
      .mockReturnValueOnce(delayedBook.promise)
      .mockResolvedValueOnce({ ...book, title: "最新书名" });
    vi.mocked(api.getTaskTracePage).mockImplementation(async (_taskId, cursor) => {
      return {
        entries: cursor.after === 0 ? [event(1, "最后一次记录")] : [],
        has_more: false,
      };
    });

    await act(async () => {
      renderChat(["/agent?taskId=task-1"]);
    });
    const [, originalSignal] = vi.mocked(api.getBook).mock.calls[0];
    expect(originalSignal?.aborted).toBe(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(screen.getByText(/创作完成/)).toBeInTheDocument();
    expect(screen.getByText(/最后一次记录/)).toBeInTheDocument();
    expect(screen.getByText("最新书名")).toBeInTheDocument();
    expect(originalSignal?.aborted).toBe(true);
    await act(async () => {
      delayedBook.resolve(book);
      await vi.advanceTimersByTimeAsync(12000);
    });
    expect(screen.queryByText(book.title)).not.toBeInTheDocument();
    expect(api.getBook).toHaveBeenCalledTimes(2);
    expect(api.getTask).toHaveBeenCalledTimes(2);
  });

  it("切换 URL 任务时取消旧请求，晚到的旧书和轨迹不会覆盖新会话", async () => {
    const oldBook = deferred<Book>();
    const oldTrace = deferred<AgentTracePage>();
    vi.mocked(api.getTask).mockImplementation(async (id) =>
      id === "task-1"
        ? agentTask()
        : agentTask({ id, book_id: "book-2", status: "done", progress: 100 })
    );
    vi.mocked(api.getBook).mockImplementation((id) =>
      id === book.id ? oldBook.promise : Promise.resolve({ ...book, id, title: "第二本书" })
    );
    vi.mocked(api.getTaskTracePage).mockImplementation((id) =>
      id === "task-1"
        ? oldTrace.promise
        : Promise.resolve({ entries: [event(1, "第二本书的轨迹", id)], has_more: false })
    );

    function SwitchTask() {
      const [, setParams] = useSearchParams();
      return <button onClick={() => setParams({ taskId: "task-2" })}>切换任务</button>;
    }

    render(
      <ConfigProvider>
        <AntdApp>
          <MemoryRouter initialEntries={["/agent?taskId=task-1"]}>
            <SwitchTask />
            <AgentChat />
          </MemoryRouter>
        </AntdApp>
      </ConfigProvider>
    );
    await waitFor(() => expect(api.getTaskTracePage).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("status")).toHaveTextContent("正在加载轨迹");
    fireEvent.click(screen.getByRole("button", { name: "切换任务" }));
    expect(await screen.findByText("第二本书")).toBeInTheDocument();
    expect(await screen.findByText(/第二本书的轨迹/)).toBeInTheDocument();
    const [, , previousSignal] = vi.mocked(api.getTaskTracePage).mock.calls[0];
    expect(previousSignal.aborted).toBe(true);
    await act(async () => {
      oldBook.resolve(book);
      oldTrace.resolve({ entries: [event(1, "第一本书的轨迹")], has_more: false });
    });
    expect(screen.queryByText("第一本书")).not.toBeInTheDocument();
    expect(screen.queryByText(/第一本书的轨迹/)).not.toBeInTheDocument();
    expect(screen.getByText(/第二本书的轨迹/)).toBeInTheDocument();
  });
});
