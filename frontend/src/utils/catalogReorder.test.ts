import { describe, expect, it } from "vitest";
import type { BookTree, TreeChapter } from "../api/types";
import { reorderCatalog } from "./catalogReorder";

const chapter = (id: string, order_index: number): TreeChapter => ({
  id,
  order_index,
  title: id,
  word_count: 100,
  status: "done",
});
const tree: BookTree = {
  volumes: [
    { id: "v1", title: "第一卷", order_index: 0, chapters: [chapter("a", 0), chapter("b", 1)] },
    { id: "v2", title: "第二卷", order_index: 1, chapters: [chapter("c", 2)] },
    { id: "empty", title: "空卷", order_index: 2, chapters: [] },
  ],
  loose_chapters: [chapter("d", 3)],
};

describe("reorderCatalog", () => {
  it("卷内前后移动保持唯一连续全局序号且不修改原目录", () => {
    const before = structuredClone(tree);
    const orders = reorderCatalog(tree, "b", { kind: "chapter", id: "a", position: "before" });
    expect(orders.map((item) => item.id)).toEqual(["b", "a", "c", "d"]);
    expect(orders.map((item) => item.order_index)).toEqual([0, 1, 2, 3]);
    expect(tree).toEqual(before);
    expect(reorderCatalog(tree, "a", { kind: "chapter", id: "b", position: "after" })).toEqual(
      orders
    );
  });

  it("跨卷移动及移入空卷不丢章，更新归属与整本书顺序", () => {
    const orders = reorderCatalog(tree, "a", { kind: "volume", id: "v2" });
    expect(orders.map((item) => item.id)).toEqual(["b", "c", "a", "d"]);
    expect(orders.find((item) => item.id === "a")?.volume_id).toBe("v2");
    const empty = reorderCatalog(tree, "d", { kind: "volume", id: "empty" });
    expect(empty).toHaveLength(4);
    expect(empty.find((item) => item.id === "d")?.volume_id).toBe("empty");
  });

  it("支持移入及移出未归卷区域", () => {
    const loose = reorderCatalog(tree, "a", { kind: "volume", id: null });
    expect(loose.map((item) => item.id)).toEqual(["b", "c", "d", "a"]);
    expect(loose[loose.length - 1].volume_id).toBeNull();
    const assigned = reorderCatalog(tree, "d", { kind: "chapter", id: "b", position: "before" });
    expect(assigned.map((item) => item.id)).toEqual(["a", "d", "b", "c"]);
    expect(assigned[1].volume_id).toBe("v1");
  });

  it("忽略自身或不存在的拖拽节点，避免提交错误重排", () => {
    expect(reorderCatalog(tree, "a", { kind: "chapter", id: "a", position: "after" })).toEqual([]);
    expect(reorderCatalog(tree, "missing", { kind: "volume", id: "v1" })).toEqual([]);
    expect(reorderCatalog(tree, "a", { kind: "volume", id: "missing" })).toEqual([]);
  });
});
