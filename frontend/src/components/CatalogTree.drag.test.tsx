import { act, render, screen, waitFor } from "@testing-library/react";
import { App, type TreeProps } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { BookTree } from "../api/types";
import CatalogTree from "./CatalogTree";

const harness = vi.hoisted(() => ({ props: undefined as TreeProps | undefined }));
vi.mock("antd", async (importOriginal) => {
  const antd = await importOriginal<typeof import("antd")>();
  return {
    ...antd,
    Tree: (props: TreeProps) => {
      harness.props = props;
      return <div>目录节点:{props.treeData?.length ?? 0}</div>;
    },
  };
});
vi.mock("../api", () => ({ api: { getTree: vi.fn(), reorderChapters: vi.fn() } }));

const tree: BookTree = {
  volumes: [
    {
      id: "v1",
      title: "卷",
      order_index: 0,
      chapters: [{ id: "a", title: "章", order_index: 0, status: "done", word_count: 100 }],
    },
  ],
  loose_chapters: [{ id: "b", title: "未归卷章", order_index: 1, status: "done", word_count: 100 }],
};
type DropInfo = Parameters<NonNullable<TreeProps["onDrop"]>>[0];

async function dropInto(key: string) {
  await act(async () => {
    await harness.props?.onDrop?.({
      dragNode: { key: "c-a" },
      node: { key, pos: "0-1" },
      dropToGap: false,
      dropPosition: 1,
    } as DropInfo);
  });
}

describe("CatalogTree 拖拽持久化", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    harness.props = undefined;
    vi.mocked(api.getTree).mockResolvedValue(tree);
    vi.mocked(api.reorderChapters).mockResolvedValue({ ok: true });
  });

  it("启用章节拖拽，移到未归卷后提交全书顺序并重新加载", async () => {
    render(
      <App>
        <CatalogTree bookId="book" selectedChapter="a" onSelectChapter={vi.fn()} />
      </App>
    );
    await screen.findByText("目录节点:2");
    expect(harness.props?.draggable).toBeTruthy();
    await dropInto("loose");
    expect(api.reorderChapters).toHaveBeenCalledWith([
      { id: "b", volume_id: null, order_index: 0 },
      { id: "a", volume_id: null, order_index: 1 },
    ]);
    expect(api.getTree).toHaveBeenCalledTimes(2);
  });

  it("失败时提示错误、保留现有目录和选择，并可重试", async () => {
    vi.mocked(api.reorderChapters).mockRejectedValueOnce(new Error("排序保存失败"));
    const select = vi.fn();
    render(
      <App>
        <CatalogTree bookId="book" selectedChapter="a" onSelectChapter={select} />
      </App>
    );
    await screen.findByText("目录节点:2");
    const original = harness.props?.treeData;
    await dropInto("loose");
    expect(await screen.findByText("排序保存失败")).toBeInTheDocument();
    expect(harness.props?.treeData?.map((item) => item.key)).toEqual(
      original?.map((item) => item.key)
    );
    expect(harness.props?.selectedKeys).toEqual(["c-a"]);
    expect(select).not.toHaveBeenCalled();
    await dropInto("loose");
    await waitFor(() => expect(api.reorderChapters).toHaveBeenCalledTimes(2));
  });
});
