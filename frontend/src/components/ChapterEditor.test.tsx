import { App, ConfigProvider } from "antd";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Chapter, ChapterContent, ChapterContentSaved } from "../api/types";
import { api } from "../api";
import { ApiError, sseFetch } from "../api/client";
import ChapterEditor from "./ChapterEditor";

vi.mock("../api", () => ({
  api: { getChapter: vi.fn(), getContent: vi.fn(), updateChapter: vi.fn(), saveContent: vi.fn() },
  sseEndpoints: { chapter: "/generate/chapter" },
}));
vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  sseFetch: vi.fn(),
}));

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
const originalVersion = "a".repeat(64);
const savedVersion = "b".repeat(64);
const bodyPlaceholder = /点击“生成正文”/;
function editor(chapterId = "a") {
  return (
    <ConfigProvider theme={{ token: { motion: false } }}>
      <App>
        <ChapterEditor bookId="book" chapterId={chapterId} />
      </App>
    </ConfigProvider>
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getChapter).mockResolvedValue(chapter);
  vi.mocked(api.getContent).mockResolvedValue({
    chapter_id: "a",
    version: originalVersion,
    body: "原正文",
  });
  vi.mocked(api.updateChapter).mockResolvedValue(chapter);
  vi.mocked(api.saveContent).mockResolvedValue({
    chapter_id: "a",
    version: savedVersion,
    body: "原正文",
    status: "done",
    word_count: 3,
  });
});

describe("ChapterEditor 编辑安全", () => {
  it.each(["error", "stop"])("已有生成预览时%s仍保留原编辑草稿", async (ending) => {
    const stream = deferred<void>();
    vi.mocked(sseFetch).mockReturnValueOnce(stream.promise);
    render(editor());
    const body = await screen.findByPlaceholderText(bodyPlaceholder);
    fireEvent.change(body, { target: { value: "未保存的作者草稿" } });
    fireEvent.click(screen.getByRole("button", { name: /生成正文/ }));
    const handlers = vi.mocked(sseFetch).mock.calls[0][2];
    await act(async () => handlers.onEvent("token", "模型正在生成的预览"));
    expect(body).toHaveValue("模型正在生成的预览");
    if (ending === "stop") fireEvent.click(screen.getByRole("button", { name: /停止/ }));
    else await act(async () => handlers.onEvent("error", "章节已在其他窗口更新，本次生成未保存"));
    await act(async () => stream.resolve());
    expect(body).toHaveValue("未保存的作者草稿");
    fireEvent.click(screen.getByRole("button", { name: /保存$/ }));
    await waitFor(() =>
      expect(api.saveContent).toHaveBeenLastCalledWith("a", "未保存的作者草稿", originalVersion)
    );
  });

  it("只有收到已保存结束事件后，生成预览才成为编辑稿", async () => {
    const stream = deferred<void>();
    vi.mocked(sseFetch).mockReturnValueOnce(stream.promise);
    vi.mocked(api.getContent)
      .mockResolvedValueOnce({ chapter_id: "a", version: originalVersion, body: "原正文" })
      .mockResolvedValueOnce({ chapter_id: "a", version: savedVersion, body: "新生成正文" });
    render(editor());
    const body = await screen.findByPlaceholderText(bodyPlaceholder);
    fireEvent.click(screen.getByRole("button", { name: /生成正文/ }));
    const handlers = vi.mocked(sseFetch).mock.calls[0][2];
    await act(async () => handlers.onEvent("token", "  新生成正文\n"));
    expect(body).toBeDisabled();
    await act(async () => {
      handlers.onEvent("done", "");
      stream.resolve();
    });
    expect(body).toHaveValue("新生成正文");
    expect(body).toBeEnabled();
  });

  it("正文仍在加载时不能输入、保存或生成，加载后才展示已有正文", async () => {
    const content = deferred<ChapterContent>();
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
    await act(async () =>
      content.resolve({ chapter_id: "a", version: originalVersion, body: "服务器原正文" })
    );
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
    const pending = deferred<ChapterContentSaved>();
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
    await waitFor(() =>
      expect(api.saveContent).toHaveBeenLastCalledWith("a", "继续输入的草稿", originalVersion)
    );
  });

  it("切换章节后忽略旧加载结果，卸载后取消生成并忽略后续事件", async () => {
    const oldContent = deferred<ChapterContent>();
    const stream = deferred<void>();
    vi.mocked(api.getContent)
      .mockReturnValueOnce(oldContent.promise)
      .mockResolvedValueOnce({ chapter_id: "b", version: originalVersion, body: "正文 B" });
    vi.mocked(api.getChapter)
      .mockResolvedValueOnce(chapter)
      .mockResolvedValueOnce({ ...chapter, id: "b", title: "章节 B" });
    vi.mocked(sseFetch).mockReturnValueOnce(stream.promise);
    const { rerender, unmount } = render(editor());
    rerender(editor("b"));
    const body = await screen.findByPlaceholderText(bodyPlaceholder);
    expect(body).toHaveValue("正文 B");
    await act(async () =>
      oldContent.resolve({ chapter_id: "a", version: originalVersion, body: "过期正文 A" })
    );
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
        expect(api.saveContent).toHaveBeenLastCalledWith("a", "尚未保存的手写草稿", originalVersion)
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

  it("加载失败可就地重试，并取消旧请求", async () => {
    vi.mocked(api.getContent)
      .mockRejectedValueOnce(new Error("正文暂不可用"))
      .mockResolvedValue({ chapter_id: "a", version: originalVersion, body: "重试后原稿" });
    const view = render(editor());
    expect(await screen.findByText("正文暂不可用")).toBeInTheDocument();
    const firstSignal = vi.mocked(api.getContent).mock.calls[0][1];
    expect(firstSignal?.aborted).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "重新加载章节" }));
    expect(await screen.findByPlaceholderText(bodyPlaceholder)).toHaveValue("重试后原稿");
    const currentSignal = vi.mocked(api.getContent).mock.calls[1][1];
    expect(currentSignal?.aborted).toBe(false);
    view.unmount();
    expect(currentSignal?.aborted).toBe(true);
  });

  it.each([
    { body: "保存的新正文", status: "done", label: "已完成", word_count: 6 },
    { body: "", status: "outline", label: "已有大纲", word_count: 0 },
  ])("保存正文后使用服务端状态 $status，保留后续本地草稿", async (saved) => {
    const pending = deferred<ChapterContentSaved>();
    vi.mocked(api.saveContent).mockReturnValueOnce(pending.promise);
    render(editor());
    const body = await screen.findByPlaceholderText(bodyPlaceholder);
    fireEvent.change(body, { target: { value: saved.body } });
    fireEvent.click(screen.getByRole("button", { name: /保存$/ }));
    fireEvent.change(body, { target: { value: "保存期间的新草稿" } });
    await act(async () => pending.resolve({ chapter_id: "a", version: savedVersion, ...saved }));
    expect(screen.getByText(saved.label)).toBeInTheDocument();
    expect(body).toHaveValue("保存期间的新草稿");
    expect(api.getChapter).toHaveBeenCalledTimes(1);
  });

  it("生成结束先确认新版本，等待期间继续输入的草稿不会被响应覆盖", async () => {
    const oldStatus = deferred<Chapter>();
    const newContent = deferred<ChapterContent>();
    const stream = deferred<void>();
    vi.mocked(api.getChapter).mockResolvedValueOnce(chapter).mockReturnValueOnce(oldStatus.promise);
    vi.mocked(api.getContent)
      .mockResolvedValueOnce({ chapter_id: "a", version: originalVersion, body: "原正文" })
      .mockReturnValueOnce(newContent.promise);
    vi.mocked(sseFetch).mockReturnValueOnce(stream.promise);
    vi.mocked(api.saveContent).mockResolvedValueOnce({
      chapter_id: "a",
      version: savedVersion,
      body: "",
      status: "outline",
      word_count: 0,
    });
    render(editor());
    const body = await screen.findByPlaceholderText(bodyPlaceholder);
    fireEvent.click(screen.getByRole("button", { name: /生成正文/ }));
    await act(async () => {
      vi.mocked(sseFetch).mock.calls[0][2].onEvent("token", "生成的新正文");
      vi.mocked(sseFetch).mock.calls[0][2].onEvent("done", "ok");
      stream.resolve();
    });
    fireEvent.change(body, { target: { value: "" } });
    expect(screen.getByRole("button", { name: /保存$/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /生成正文/ })).toBeDisabled();
    await act(async () => {
      oldStatus.resolve({ ...chapter, status: "done", word_count: 6 });
      newContent.resolve({ chapter_id: "a", body: "生成的新正文", version: savedVersion });
    });
    expect(body).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: /保存$/ }));
    expect(await screen.findByText("已有大纲")).toBeInTheDocument();
    expect(api.saveContent).toHaveBeenLastCalledWith("a", "", savedVersion);
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
    expect(body).toHaveValue("");
  });

  it("连续保存携带最近确认的版本，同时保留保存期间的新输入", async () => {
    const pending = deferred<ChapterContentSaved>();
    vi.mocked(api.saveContent).mockReturnValueOnce(pending.promise);
    render(editor());
    const body = await screen.findByPlaceholderText(bodyPlaceholder);
    fireEvent.change(body, { target: { value: "第一稿" } });
    fireEvent.click(screen.getByRole("button", { name: /保存$/ }));
    expect(api.saveContent).toHaveBeenLastCalledWith("a", "第一稿", originalVersion);
    fireEvent.change(body, { target: { value: "第二稿" } });
    await act(async () =>
      pending.resolve({
        chapter_id: "a",
        body: "第一稿",
        version: savedVersion,
        status: "done",
        word_count: 3,
      })
    );
    expect(body).toHaveValue("第二稿");
    fireEvent.click(screen.getByRole("button", { name: /保存$/ }));
    await waitFor(() =>
      expect(api.saveContent).toHaveBeenLastCalledWith("a", "第二稿", savedVersion)
    );
  });

  it("版本冲突保留草稿，取消恢复不读正文，明确确认后才替换并使用服务器新版本", async () => {
    vi.mocked(api.saveContent).mockRejectedValueOnce(new ApiError(409, "正文已有新版本"));
    const latest = deferred<ChapterContent>();
    vi.mocked(api.getContent)
      .mockResolvedValueOnce({ chapter_id: "a", body: "原正文", version: originalVersion })
      .mockReturnValueOnce(latest.promise);
    render(editor());
    const body = await screen.findByPlaceholderText(bodyPlaceholder);
    fireEvent.change(body, { target: { value: "我的未保存草稿" } });
    fireEvent.click(screen.getByRole("button", { name: /保存$/ }));
    expect(await screen.findByText("服务器正文已有新版本，本地草稿已保留")).toBeInTheDocument();
    expect(body).toHaveValue("我的未保存草稿");
    expect(screen.getByRole("button", { name: /保存$/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /生成正文/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重新加载正文" }));
    fireEvent.click(await screen.findByRole("button", { name: "保留本地草稿" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.getContent).toHaveBeenCalledTimes(1);
    expect(body).toHaveValue("我的未保存草稿");
    fireEvent.click(screen.getByRole("button", { name: "重新加载正文" }));
    fireEvent.click(await screen.findByRole("button", { name: "替换并重新加载" }));
    await waitFor(() => expect(api.getContent).toHaveBeenCalledTimes(2));
    expect(body).toBeDisabled();
    await act(async () =>
      latest.resolve({ chapter_id: "a", body: "另一窗口的新稿", version: savedVersion })
    );
    expect(body).toHaveValue("另一窗口的新稿");
    expect(body).toBeEnabled();
    expect(screen.queryByText("服务器正文已有新版本，本地草稿已保留")).not.toBeInTheDocument();
    fireEvent.change(body, { target: { value: "手工合并的草稿" } });
    fireEvent.click(screen.getByRole("button", { name: /保存$/ }));
    await waitFor(() =>
      expect(api.saveContent).toHaveBeenLastCalledWith("a", "手工合并的草稿", savedVersion)
    );
  });

  it("生成后的版本确认失败可以重试，重试成功也保留手写内容", async () => {
    const stream = deferred<void>();
    vi.mocked(sseFetch).mockReturnValueOnce(stream.promise);
    vi.mocked(api.getContent)
      .mockResolvedValueOnce({ chapter_id: "a", body: "原正文", version: originalVersion })
      .mockRejectedValueOnce(new Error("版本查询暂不可用"))
      .mockResolvedValueOnce({ chapter_id: "a", body: "新生成正文", version: savedVersion });
    render(editor());
    const body = await screen.findByPlaceholderText(bodyPlaceholder);
    fireEvent.click(screen.getByRole("button", { name: /生成正文/ }));
    await act(async () => {
      const handlers = vi.mocked(sseFetch).mock.calls[0][2];
      handlers.onEvent("token", "新生成正文");
      handlers.onEvent("done", "");
      stream.resolve();
    });
    expect(await screen.findByText("尚未确认生成后的正文版本，当前草稿已保留")).toBeInTheDocument();
    fireEvent.change(body, { target: { value: "继续润色的新稿" } });
    expect(screen.getByRole("button", { name: /保存$/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重新确认版本" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /保存$/ })).toBeEnabled());
    expect(body).toHaveValue("继续润色的新稿");
    fireEvent.click(screen.getByRole("button", { name: /保存$/ }));
    await waitFor(() =>
      expect(api.saveContent).toHaveBeenLastCalledWith("a", "继续润色的新稿", savedVersion)
    );
  });

  it("生成后读取到其他窗口的新正文时不能把它的版本配给本地稿", async () => {
    const stream = deferred<void>();
    vi.mocked(sseFetch).mockReturnValueOnce(stream.promise);
    vi.mocked(api.getContent)
      .mockResolvedValueOnce({ chapter_id: "a", body: "原正文", version: originalVersion })
      .mockResolvedValueOnce({ chapter_id: "a", body: "另一窗口已编辑", version: savedVersion });
    render(editor());
    const body = await screen.findByPlaceholderText(bodyPlaceholder);
    fireEvent.click(screen.getByRole("button", { name: /生成正文/ }));
    await act(async () => {
      const handlers = vi.mocked(sseFetch).mock.calls[0][2];
      handlers.onEvent("token", "刚生成的正文");
      handlers.onEvent("done", "");
      stream.resolve();
    });
    expect(await screen.findByText("服务器正文已有新版本，本地草稿已保留")).toBeInTheDocument();
    expect(body).toHaveValue("刚生成的正文");
    expect(screen.getByRole("button", { name: /保存$/ })).toBeDisabled();
    expect(api.saveContent).not.toHaveBeenCalled();
  });
});
