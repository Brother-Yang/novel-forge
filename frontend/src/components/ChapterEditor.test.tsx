import { App } from "antd";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Chapter } from "../api/types";
import { api } from "../api";
import { sseFetch } from "../api/client";
import ChapterEditor from "./ChapterEditor";

vi.mock("../api", () => ({
  api: { getChapter: vi.fn(), getContent: vi.fn(), updateChapter: vi.fn(), saveContent: vi.fn() },
  sseEndpoints: { chapter: "/generate/chapter" },
}));
vi.mock("../api/client", () => ({ sseFetch: vi.fn() }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const chapter: Chapter = {
  id: "a",
  book_id: "book",
  volume_id: null,
  title: "章节 A",
  outline: "原大纲",
  order_index: 0,
  status: "pending",
  word_count: 3,
};
const bodyPlaceholder = /点击“生成正文”/;
function editor(chapterId = "a") {
  return (
    <App>
      <ChapterEditor bookId="book" chapterId={chapterId} />
    </App>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getChapter).mockResolvedValue(chapter);
  vi.mocked(api.getContent).mockResolvedValue({ chapter_id: "a", body: "原正文" });
  vi.mocked(api.updateChapter).mockResolvedValue(chapter);
  vi.mocked(api.saveContent).mockResolvedValue(undefined);
});

describe("ChapterEditor 编辑安全", () => {
  it("正文仍在加载时不能输入、保存或生成，加载后才展示已有正文", async () => {
    const content = deferred<{ chapter_id: string; body: string }>();
    vi.mocked(api.getContent).mockReturnValue(content.promise);
    render(editor());
    await waitFor(() => expect(api.getChapter).toHaveBeenCalled());
    await act(async () => {});
    const save = screen.queryByRole("button", { name: /保存$/ });
    const generate = screen.queryByRole("button", { name: /生成正文/ });
    const body = screen.queryByPlaceholderText(bodyPlaceholder);
    if (save) expect(save).toBeDisabled();
    if (generate) expect(generate).toBeDisabled();
    if (body) expect(body).toBeDisabled();
    expect(api.saveContent).not.toHaveBeenCalled();
    expect(sseFetch).not.toHaveBeenCalled();
    await act(async () => content.resolve({ chapter_id: "a", body: "服务器原正文" }));
    expect(await screen.findByPlaceholderText(bodyPlaceholder)).toHaveValue("服务器原正文");
    expect(screen.getByRole("button", { name: /保存$/ })).toBeEnabled();
  });

  it("标题保存响应不会覆盖仍在编辑的大纲", async () => {
    const pending = deferred<Chapter>();
    vi.mocked(api.updateChapter).mockReturnValue(pending.promise);
    render(editor());
    const title = await screen.findByDisplayValue("章节 A");
    fireEvent.change(title, { target: { value: "新标题" } });
    fireEvent.blur(title);
    const outline = screen.getByPlaceholderText(/本章梗概/);
    fireEvent.change(outline, { target: { value: "还在写的新大纲" } });
    await act(async () => pending.resolve({ ...chapter, title: "新标题" }));
    expect(outline).toHaveValue("还在写的新大纲");
  });

  it("正文保存期间保留继续输入的草稿并阻止生成覆盖，失败可重试", async () => {
    const pending = deferred<unknown>();
    vi.mocked(api.saveContent).mockReturnValueOnce(pending.promise);
    render(editor());
    const body = await screen.findByPlaceholderText(bodyPlaceholder);
    fireEvent.change(body, { target: { value: "待保存正文" } });
    fireEvent.click(screen.getByRole("button", { name: /保存$/ }));
    fireEvent.change(body, { target: { value: "继续输入的草稿" } });
    expect(screen.getByRole("button", { name: /生成正文/ })).toBeDisabled();
    await act(async () => pending.reject(new Error("正文保存失败")));
    expect(await screen.findByText("正文保存失败")).toBeInTheDocument();
    expect(body).toHaveValue("继续输入的草稿");
    fireEvent.click(screen.getByRole("button", { name: /保存$/ }));
    await waitFor(() => expect(api.saveContent).toHaveBeenLastCalledWith("a", "继续输入的草稿"));
  });

  it("切换章节后忽略旧加载结果，卸载后取消生成并忽略后续事件", async () => {
    const oldContent = deferred<{ chapter_id: string; body: string }>();
    const stream = deferred<void>();
    vi.mocked(api.getContent)
      .mockReturnValueOnce(oldContent.promise)
      .mockResolvedValueOnce({ chapter_id: "b", body: "正文 B" });
    vi.mocked(api.getChapter)
      .mockResolvedValueOnce(chapter)
      .mockResolvedValueOnce({ ...chapter, id: "b", title: "章节 B" });
    vi.mocked(sseFetch).mockReturnValueOnce(stream.promise);
    const { rerender, unmount } = render(editor());
    rerender(editor("b"));
    const body = await screen.findByPlaceholderText(bodyPlaceholder);
    expect(body).toHaveValue("正文 B");
    await act(async () => oldContent.resolve({ chapter_id: "a", body: "过期正文 A" }));
    expect(body).toHaveValue("正文 B");
    fireEvent.click(screen.getByRole("button", { name: /生成正文/ }));
    const handlers = vi.mocked(sseFetch).mock.calls[0][2];
    expect(body).toBeDisabled();
    expect(screen.getByRole("button", { name: /保存$/ })).toBeDisabled();
    unmount();
    expect(handlers.signal?.aborted).toBe(true);
    await act(async () => {
      handlers.onEvent("token", "过期 token");
      handlers.onEvent("error", "过期生成错误");
      stream.resolve();
    });
    expect(screen.queryByText("过期生成错误")).not.toBeInTheDocument();
  });

  it.each([{ tokens: [] }, { tokens: ["", " ", "\n\t"] }])(
    "生成没有有效正文时保留原草稿：$tokens",
    async ({ tokens }) => {
      const stream = deferred<void>();
      vi.mocked(sseFetch).mockReturnValueOnce(stream.promise);
      render(editor());
      const body = await screen.findByPlaceholderText(bodyPlaceholder);
      fireEvent.change(body, { target: { value: "尚未保存的手写草稿" } });
      fireEvent.click(screen.getByRole("button", { name: /生成正文/ }));
      const handlers = vi.mocked(sseFetch).mock.calls[0][2];
      await act(async () => {
        for (const token of tokens) handlers.onEvent("token", token);
        handlers.onEvent("error", "模型没有输出正文");
        stream.resolve();
      });
      expect(await screen.findByText("模型没有输出正文")).toBeInTheDocument();
      expect(body).toHaveValue("尚未保存的手写草稿");
      fireEvent.click(screen.getByRole("button", { name: /保存$/ }));
      await waitFor(() =>
        expect(api.saveContent).toHaveBeenLastCalledWith("a", "尚未保存的手写草稿")
      );
    }
  );

  it("元数据保存失败保留字段且可重新失焦重试", async () => {
    vi.mocked(api.updateChapter).mockRejectedValueOnce(new Error("标题保存失败"));
    render(editor());
    const title = await screen.findByDisplayValue("章节 A");
    fireEvent.change(title, { target: { value: "待保存标题" } });
    fireEvent.blur(title);
    expect(await screen.findByText("标题保存失败")).toBeInTheDocument();
    expect(title).toHaveValue("待保存标题");
    expect(title).toBeEnabled();
    fireEvent.blur(title);
    await waitFor(() => expect(api.updateChapter).toHaveBeenCalledTimes(2));
  });

  it("正文加载失败时提示错误且不能提交空正文", async () => {
    vi.mocked(api.getContent).mockRejectedValue(new Error("正文加载失败"));
    render(editor());
    expect(await screen.findByText("正文加载失败")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /保存$/ })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(bodyPlaceholder)).not.toBeInTheDocument();
  });
});
