import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes, useParams, useSearchParams } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Book } from "../api/types";
import { routePatterns } from "../router/paths";
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function renderWithProviders() {
  function BookDestination() {
    const { bookId } = useParams();
    const [query] = useSearchParams();
    return <div>{`${bookId} ${query.get("view")}`}</div>;
  }

  return render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter>
          <Routes>
            <Route path={routePatterns.home} element={<BookList />} />
            <Route path={routePatterns.book} element={<BookDestination />} />
          </Routes>
        </MemoryRouter>
      </AntdApp>
    </ConfigProvider>
  );
}

describe("BookList", () => {
  beforeEach(() => vi.resetAllMocks());

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

  it.each([
    { status: "running", error: "" },
    { status: "error", error: "暂时无法启动生成任务" },
  ])("一句话出书返回 $status 时进入已建书的任务页并显示结果", async ({ status, error }) => {
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
        status,
        progress: 0,
        error,
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
    expect(await screen.findByText("bbbbbbbb-cccc-dddd-eeee-ffffffffffff tasks")).toBeVisible();
    if (error) {
      expect(await screen.findByText(error)).toBeVisible();
      expect(screen.queryByText("已开始自动生成")).not.toBeInTheDocument();
    } else {
      expect(await screen.findByText("已开始自动生成")).toBeVisible();
    }
  });

  it("首次失败显示书架重试入口，成功空列表才展示空状态", async () => {
    const first = deferred<Book[]>();
    vi.mocked(api.listBooks).mockReturnValueOnce(first.promise).mockResolvedValueOnce([]);
    renderWithProviders();
    expect(screen.getByRole("status")).toHaveTextContent("正在加载书架");
    expect(screen.queryByText(/还没有书籍/)).not.toBeInTheDocument();
    await act(async () => {
      first.reject(new Error("服务暂不可用"));
    });
    expect(screen.getByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.queryByText(/还没有书籍/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载书架" }));
    expect(await screen.findByText(/还没有书籍/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("新建空书只提交一次，迟到的书架请求不会覆盖新结果", async () => {
    const stale = deferred<Book[]>();
    const create = deferred<Book>();
    const book: Book = {
      id: "new-book",
      title: "新建书稿",
      intro: "",
      brief: "",
      genre: "",
      tone: "",
      target_words: 100000,
      key_elements: "[]",
      created_at: "2026-01-01T00:00:00Z",
    };
    vi.mocked(api.listBooks).mockReturnValueOnce(stale.promise).mockResolvedValueOnce([book]);
    vi.mocked(api.createBook).mockReturnValue(create.promise);
    const user = userEvent.setup();
    renderWithProviders();
    await user.click(screen.getByRole("button", { name: /新建空书/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/书名/), book.title);
    const confirm = within(dialog).getByRole("button", { name: /创\s*建/ });
    await act(async () => {
      fireEvent.click(confirm);
      fireEvent.click(confirm);
    });
    expect(api.createBook).toHaveBeenCalledTimes(1);
    await act(async () => {
      create.resolve(book);
    });
    expect(await screen.findByText(book.title)).toBeInTheDocument();
    const [staleSignal] = vi.mocked(api.listBooks).mock.calls[0];
    expect(staleSignal?.aborted).toBe(true);
    await act(async () => {
      stale.resolve([]);
    });
    expect(screen.getByText(book.title)).toBeInTheDocument();
  });

  it("一句话出书提交期间屏蔽重复确认，失败可用原输入重试", async () => {
    const first = deferred<Awaited<ReturnType<typeof api.startNovelGeneration>>>();
    vi.mocked(api.listBooks).mockResolvedValue([]);
    vi.mocked(api.startNovelGeneration)
      .mockReturnValueOnce(first.promise)
      .mockRejectedValueOnce(new Error("仍未恢复"));
    const user = userEvent.setup();
    renderWithProviders();
    await user.click(screen.getByRole("button", { name: /一句话出书/ }));
    const dialog = await screen.findByRole("dialog");
    const idea = within(dialog).getByLabelText(/故事想法/);
    await user.type(idea, "重试仍要保留的故事");
    const confirm = within(dialog).getByRole("button", { name: "开始生成" });
    await act(async () => {
      fireEvent.click(confirm);
      fireEvent.click(confirm);
    });
    expect(api.startNovelGeneration).toHaveBeenCalledTimes(1);
    await act(async () => {
      first.reject(new Error("暂时无法生成"));
    });
    expect(await screen.findByText("暂时无法生成")).toBeVisible();
    expect(idea).toHaveValue("重试仍要保留的故事");
    fireEvent.click(confirm);
    await waitFor(() => expect(api.startNovelGeneration).toHaveBeenCalledTimes(2));
  });
});
