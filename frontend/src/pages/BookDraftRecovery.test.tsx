import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App, ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ApiError } from "../api/client";
import type { Book, ChapterContentSaved } from "../api/types";
import { chapterDraftKey, useAppStore } from "../store";
import BookList from "./BookList";
import BookWorkspace from "./BookWorkspace";

vi.mock("../api", () => ({
  api: { getBook: vi.fn(), listBooks: vi.fn(), getChapter: vi.fn(), getContent: vi.fn() },
  sseEndpoints: { chapter: "/generate/chapter" },
}));
vi.mock("../components/CatalogTree", () => ({
  default: ({ onSelectChapter }: { onSelectChapter: (id: string) => void }) => (
    <button onClick={() => onSelectChapter("test-chapter")}>选择测试章</button>
  ),
}));

const book: Book = {
  id: "test-book",
  title: "测试书",
  intro: "",
  brief: "",
  genre: "",
  tone: "",
  target_words: 100000,
  key_elements: "[]",
  created_at: "2026-01-01",
};
const draftKey = chapterDraftKey(book.id, "test-chapter");
const otherKey = chapterDraftKey("other-book", "other-chapter");
const draft = "测试用未保存正文";
const base = { body: "原正文", version: "a".repeat(64) };

function Navigation() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate("/")}>返回首页</button>
      <button onClick={() => navigate(-1)}>浏览器后退</button>
    </>
  );
}

function renderRoutes(initialEntry = `/books/${book.id}`) {
  render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <App>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Navigation />
          <Routes>
            <Route path="/" element={<BookList />} />
            <Route path="/books/:bookId" element={<BookWorkspace />} />
          </Routes>
        </MemoryRouter>
      </App>
    </ConfigProvider>
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  useAppStore.getState().clearChapterDrafts();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

it("书籍被另一窗口删除后，首页和回退入口仍可复制或明确丢弃该书的正文草稿", async () => {
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, "writeText");
  vi.mocked(api.getBook)
    .mockResolvedValueOnce(book)
    .mockRejectedValue(new ApiError(404, "书籍不存在"));
  vi.mocked(api.listBooks).mockResolvedValue([{ ...book, id: "other-book", title: "另一本书" }]);
  vi.mocked(api.getChapter)
    .mockResolvedValueOnce({
      id: "test-chapter",
      book_id: book.id,
      volume_id: null,
      title: "测试章",
      outline: "原纲",
      order_index: 0,
      status: "outline",
      word_count: 3,
    })
    .mockRejectedValue(new ApiError(404, "章节不存在"));
  vi.mocked(api.getContent).mockResolvedValue({ chapter_id: "test-chapter", ...base });
  useAppStore.getState().setChapterDraft(otherKey, { body: "另一书草稿", base, title: "另章" });

  renderRoutes();
  await user.click(await screen.findByRole("button", { name: "选择测试章" }));
  fireEvent.change(await screen.findByPlaceholderText(/点击“生成正文”/), {
    target: { value: draft },
  });
  expect(screen.getByText("未保存草稿")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "返回首页" }));
  expect(await screen.findByRole("button", { name: "找回草稿：测试章" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "找回草稿：另章" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "浏览器后退" }));
  expect(await screen.findByText("书籍不存在")).toBeVisible();
  expect(screen.queryByRole("textbox", { name: "本地正文草稿" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "查看草稿：测试章" }));
  const recovered = await screen.findByRole("textbox", { name: "本地正文草稿" });
  expect(recovered).toHaveValue(draft);
  expect(recovered).toHaveAttribute("readonly");
  await user.click(screen.getByRole("button", { name: "复制正文" }));
  expect(copy).toHaveBeenCalledWith(draft);
  await user.click(screen.getByRole("button", { name: "丢弃本地草稿" }));
  await user.click(await screen.findByRole("button", { name: "保留草稿" }));
  expect(useAppStore.getState().chapterDrafts[draftKey]?.body).toBe(draft);
  await user.click(screen.getByRole("button", { name: "丢弃本地草稿" }));
  await user.click(await screen.findByRole("button", { name: "丢弃草稿" }));
  expect(useAppStore.getState().chapterDrafts[draftKey]).toBeUndefined();
  expect(useAppStore.getState().chapterDrafts[otherKey]?.body).toBe("另一书草稿");
  await user.click(screen.getByRole("button", { name: "返回书架" }));
  expect(await screen.findByText("另一本书")).toBeVisible();
  expect(screen.queryByRole("button", { name: /找回草稿/ })).not.toBeInTheDocument();
  copy.mockRestore();
});

it("书籍或书单的网络错误仅提供重试，草稿仍保留", async () => {
  vi.mocked(api.getBook).mockRejectedValue(new Error("书籍网络断开"));
  vi.mocked(api.listBooks).mockRejectedValue(new Error("书单网络断开"));
  useAppStore.getState().setChapterDraft(draftKey, { body: draft, base, title: "测试章" });
  renderRoutes();
  expect(await screen.findByText("书籍网络断开")).toBeVisible();
  expect(screen.queryByRole("button", { name: /查看草稿/ })).not.toBeInTheDocument();
  expect(useAppStore.getState().chapterDrafts[draftKey]?.verification).toBeUndefined();
  fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
  expect(await screen.findByText(/书单网络断开/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /找回草稿/ })).not.toBeInTheDocument();
  expect(useAppStore.getState().chapterDrafts[draftKey]?.body).toBe(draft);
});

it("书单确认书籍缺失后，迟到的保存响应保留原草稿，旧书单不标记新草稿", async () => {
  const list = deferred<Book[]>();
  const response = deferred<ChapterContentSaved>();
  vi.mocked(api.listBooks).mockReturnValue(list.promise);
  const savedDraft = { body: draft, base, title: "测试章" };
  useAppStore.getState().setChapterDraft(draftKey, savedDraft);
  const pending = useAppStore
    .getState()
    .startChapterSave(draftKey, savedDraft, () => response.promise);
  renderRoutes("/");
  await waitFor(() => expect(api.listBooks).toHaveBeenCalledTimes(1));
  await act(async () => {
    useAppStore.getState().setChapterDraft(otherKey, { body: "新草稿", base, title: "新章" });
    list.resolve([]);
  });
  expect(await screen.findByRole("button", { name: "找回草稿：测试章" })).toBeVisible();
  expect(useAppStore.getState().chapterDrafts[draftKey]?.verification).toBe("missing");
  expect(useAppStore.getState().chapterDrafts[otherKey]?.verification).toBeUndefined();
  expect(screen.queryByRole("button", { name: "找回草稿：新章" })).not.toBeInTheDocument();
  await act(async () => {
    response.resolve({
      chapter_id: "test-chapter",
      body: draft,
      version: "b".repeat(64),
      status: "done",
      word_count: draft.length,
    });
    await pending;
  });
  expect(useAppStore.getState().chapterDrafts[draftKey]?.body).toBe(draft);
  expect(screen.getByRole("button", { name: "找回草稿：测试章" })).toBeVisible();
});
