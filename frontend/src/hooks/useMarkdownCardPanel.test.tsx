import type { PropsWithChildren } from "react";
import { App } from "antd";
import { act, renderHook, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { sseFetch } from "../api/client";
import { useMarkdownCardPanel } from "./useMarkdownCardPanel";

vi.mock("../api/client", () => ({ sseFetch: vi.fn() }));

interface Card {
  id: string;
  title: string;
  md_content: string;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const a: Card = { id: "a", title: "A", md_content: "正文 A" };
const b: Card = { id: "b", title: "B", md_content: "正文 B" };
const list = vi.fn<() => Promise<Card[]>>();
const update = vi.fn<(id: string, patch: Partial<Card>) => Promise<Card>>();
const create = vi.fn<() => Promise<Card>>();
const remove = vi.fn<(id: string) => Promise<unknown>>();
const options = {
  bookId: "book",
  list,
  update,
  create,
  remove,
  generateEndpoint: "/generate/cards",
  generateBody: (bookId: string) => ({ bookId }),
};
const wrapper = ({ children }: PropsWithChildren) => <App>{children}</App>;

beforeEach(() => {
  vi.clearAllMocks();
  list.mockResolvedValue([a, b]);
  update.mockImplementation(async (id, patch) => ({ ...(id === "a" ? a : b), ...patch }));
});

describe("useMarkdownCardPanel 编辑安全", () => {
  it("切换卡片后忽略前一卡片保存响应，下一次保存仍提交当前卡片", async () => {
    const pending = deferred<Card>();
    update.mockReturnValueOnce(pending.promise);
    const { result } = renderHook(() => useMarkdownCardPanel(options), { wrapper });
    await waitFor(() => expect(result.current.items).toHaveLength(2));
    act(() => result.current.select(a));
    act(() => result.current.setMd("A 保存内容"));
    let saving!: Promise<void>;
    act(() => {
      saving = result.current.save();
    });
    act(() => result.current.select(b));
    act(() => result.current.setMd("B 新草稿"));
    await act(async () => {
      pending.resolve({ ...a, md_content: "A 保存内容" });
      await saving;
    });
    expect(result.current.current?.id).toBe("b");
    expect(result.current.md).toBe("B 新草稿");
    await act(async () => result.current.save());
    expect(update).toHaveBeenLastCalledWith("b", { md_content: "B 新草稿" });
  });

  it("保存期间继续编辑同一卡片，响应保留新草稿和标题", async () => {
    const pending = deferred<Card>();
    update.mockReturnValueOnce(pending.promise);
    const { result } = renderHook(() => useMarkdownCardPanel(options), { wrapper });
    await waitFor(() => expect(result.current.items).toHaveLength(2));
    act(() => result.current.select(a));
    let saving!: Promise<void>;
    act(() => {
      saving = result.current.save();
    });
    act(() => {
      result.current.setMd("保存后继续写");
      result.current.setCurrent({ ...a, title: "新标题" });
    });
    await act(async () => {
      pending.resolve(a);
      await saving;
    });
    expect(result.current.md).toBe("保存后继续写");
    expect(result.current.current?.title).toBe("新标题");
  });

  it("多次刷新乱序返回时保留最新列表，卸载后不显示过期失败", async () => {
    const older = deferred<Card[]>();
    const newer = deferred<Card[]>();
    list.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    const { result, unmount } = renderHook(() => useMarkdownCardPanel(options), { wrapper });
    let refresh!: Promise<void>;
    act(() => {
      refresh = result.current.load();
    });
    await act(async () => {
      newer.resolve([b]);
      await refresh;
    });
    await act(async () => older.resolve([a]));
    expect(result.current.items).toEqual([b]);
    const stale = deferred<Card[]>();
    list.mockReturnValueOnce(stale.promise);
    act(() => {
      refresh = result.current.load();
    });
    unmount();
    await act(async () => {
      stale.reject(new Error("过期加载错误"));
      await refresh;
    });
    expect(screen.queryByText("过期加载错误")).not.toBeInTheDocument();
  });

  it.each(["create", "remove"] as const)("%s 失败可见且保留当前草稿", async (operation) => {
    const error = new Error("操作失败");
    if (operation === "create") create.mockRejectedValueOnce(error);
    else remove.mockRejectedValueOnce(error);
    const { result } = renderHook(() => useMarkdownCardPanel(options), { wrapper });
    await waitFor(() => expect(result.current.items).toHaveLength(2));
    act(() => result.current.select(a));
    act(() => result.current.setMd("保留的草稿"));
    await act(async () => {
      if (operation === "create") await result.current.add();
      else await result.current.remove("a");
    });
    expect(await screen.findByText("操作失败")).toBeInTheDocument();
    expect(result.current.current?.id).toBe("a");
    expect(result.current.md).toBe("保留的草稿");
  });

  it("保存失败可见，保留草稿并允许重试", async () => {
    update.mockRejectedValueOnce(new Error("卡片保存失败"));
    const { result } = renderHook(() => useMarkdownCardPanel(options), { wrapper });
    await waitFor(() => expect(result.current.items).toHaveLength(2));
    act(() => result.current.select(a));
    act(() => result.current.setMd("未保存草稿"));
    await act(async () => result.current.save());
    expect(await screen.findByText("卡片保存失败")).toBeInTheDocument();
    expect(result.current.md).toBe("未保存草稿");
    await act(async () => result.current.save());
    expect(update).toHaveBeenLastCalledWith("a", { md_content: "未保存草稿" });
  });

  it("名称保存失败保留标题与正文草稿，再次保存能够成功", async () => {
    update.mockRejectedValueOnce(new Error("名称保存失败"));
    const { result } = renderHook(() => useMarkdownCardPanel(options), { wrapper });
    await waitFor(() => expect(result.current.items).toHaveLength(2));
    act(() => {
      result.current.select(a);
      result.current.setCurrent({ ...a, title: "未保存标题" });
      result.current.setMd("尚未提交的正文");
    });
    await act(async () => result.current.saveMetadata({ title: "未保存标题" }));
    expect(await screen.findByText("名称保存失败")).toBeInTheDocument();
    expect(result.current.current?.title).toBe("未保存标题");
    expect(result.current.md).toBe("尚未提交的正文");
    await act(async () => result.current.saveMetadata({ title: "未保存标题" }));
    expect(update).toHaveBeenLastCalledWith("a", { title: "未保存标题" });
    expect(result.current.md).toBe("尚未提交的正文");
  });

  it("名称保存的迟到响应不覆盖后来选择的卡片或新书，也不提示过期错误", async () => {
    const pending = deferred<Card>();
    update.mockReturnValueOnce(pending.promise);
    const { result, rerender } = renderHook(
      ({ bookId }) => useMarkdownCardPanel({ ...options, bookId }),
      { wrapper, initialProps: { bookId: "first" } }
    );
    await waitFor(() => expect(result.current.items).toHaveLength(2));
    act(() => result.current.select(a));
    let saving!: Promise<void>;
    act(() => {
      saving = result.current.saveMetadata({ title: "保存中" });
    });
    rerender({ bookId: "second" });
    await waitFor(() => expect(result.current.items).toHaveLength(2));
    act(() => result.current.select(b));
    act(() => result.current.setMd("新书草稿"));
    await act(async () => {
      pending.reject(new Error("旧书名称错误"));
      await saving;
    });
    expect(result.current.current?.id).toBe("b");
    expect(result.current.md).toBe("新书草稿");
    expect(screen.queryByText("旧书名称错误")).not.toBeInTheDocument();
  });

  it("切换书籍取消生成，只有当前书籍的完成事件会刷新列表", async () => {
    vi.mocked(sseFetch).mockImplementation(() => new Promise<void>(() => {}));
    const { result, rerender } = renderHook(
      ({ bookId }) => useMarkdownCardPanel({ ...options, bookId }),
      { wrapper, initialProps: { bookId: "first" } }
    );
    await waitFor(() => expect(result.current.items).toHaveLength(2));
    act(() => result.current.generate());
    const previous = vi.mocked(sseFetch).mock.calls[0][2];
    expect(result.current.generating).toBe(true);
    rerender({ bookId: "second" });
    expect(previous.signal?.aborted).toBe(true);
    expect(result.current.generating).toBe(false);
    expect(list).toHaveBeenCalledTimes(2);
    act(() => {
      previous.onEvent("done", "");
      previous.onError?.(new Error("过期生成失败"));
      result.current.generate();
    });
    expect(screen.queryByText("过期生成失败")).not.toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
    await act(async () => vi.mocked(sseFetch).mock.calls[1][2].onEvent("done", ""));
    expect(result.current.generating).toBe(false);
    expect(list).toHaveBeenCalledTimes(3);
    expect(list).toHaveBeenLastCalledWith("second");
  });
});
